"""Hand-written parsers for the static TrueType tables the engine needs.

Covers (read-only): head, maxp, hhea, hmtx, post, loca and simple ``glyf``
glyphs.  Composite glyphs are explicitly rejected.  The four phantom points
are constructed exactly as fontTools builds them from xMin/hmtx.
"""
from __future__ import annotations

import struct

from .binary import Reader, VariationEngineError

# glyf simple-glyph flag bits
FLAG_ON_CURVE = 0x01
FLAG_X_SHORT = 0x02
FLAG_Y_SHORT = 0x04
FLAG_REPEAT = 0x08
FLAG_X_SAME_OR_POSITIVE = 0x10
FLAG_Y_SAME_OR_POSITIVE = 0x20


class SimpleGlyph:
    def __init__(self, name, number_of_contours, coordinates, flags, end_pts,
                 x_min, y_min, x_max, y_max):
        self.name = name
        self.number_of_contours = number_of_contours
        # list of (x, y) ints; on-curve markers live in ``flags``
        self.coordinates = coordinates
        self.on_curve = [bool(f & FLAG_ON_CURVE) for f in flags]
        self.end_pts = end_pts  # indices of last point of each contour
        self.x_min, self.y_min = x_min, y_min
        self.x_max, self.y_max = x_max, y_max

    @property
    def contours(self) -> list[list[int]]:
        """Point indices grouped by contour (glyf endPtsOfContours)."""
        groups = []
        start = 0
        for end in self.end_pts:
            groups.append(list(range(start, end + 1)))
            start = end + 1
        return groups


class StaticFont:
    def __init__(self, tables: dict[str, bytes]):
        self.tables = tables
        required = {"head", "maxp", "hhea", "hmtx", "loca", "glyf"}
        missing = required - set(tables)
        if missing:
            raise ValueError(f"missing required table(s): {sorted(missing)}")

        self.index_to_loc_format, self.units_per_em = self._parse_head(tables["head"])
        self.num_glyphs = self._parse_maxp(tables["maxp"])
        self.num_hmetrics = self._parse_hhea(tables["hhea"])
        self.h_metrics = self._parse_hmtx(tables["hmtx"], self.num_glyphs,
                                          self.num_hmetrics)
        self.glyph_order = self._parse_post(tables.get("post"), self.num_glyphs)
        self.glyphs = self._parse_glyf(tables["glyf"], tables["loca"])

    # ------------------------------------------------------------------
    # table parsers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_head(data: bytes):
        # unitsPerEm is at offset 18; indexToLocFormat at offset 50.
        units_per_em = struct.unpack_from(">H", data, 18)[0]
        index_to_loc_format = struct.unpack_from(">h", data, 50)[0]
        return index_to_loc_format, units_per_em

    @staticmethod
    def _parse_maxp(data: bytes):
        # numGlyphs is at offset 4.
        return struct.unpack_from(">H", data, 4)[0]

    @staticmethod
    def _parse_hhea(data: bytes):
        # numberOfHMetrics is the last uint16 of the hhea record (offset 34).
        return struct.unpack_from(">H", data, 34)[0]

    @staticmethod
    def _parse_hmtx(data: bytes, num_glyphs, num_hmetrics):
        r = Reader(data)
        metrics = []
        for _ in range(num_hmetrics):
            metrics.append(r.read_struct("Hh"))
        # remaining glyphs reuse the last advance width; bearings are int16
        bearings = []
        for _ in range(num_glyphs - num_hmetrics):
            bearings.append(r.read("h"))
        last_advance = metrics[-1][0] if metrics else 0
        result = list(metrics)
        for lsb in bearings:
            result.append((last_advance, lsb))
        return result

    @staticmethod
    def _parse_post(data: bytes | None, num_glyphs: int) -> list[str]:
        if not data or len(data) < 4:
            return [f"gid{i}" for i in range(num_glyphs)]
        r = Reader(data)
        version = r.read("I")
        if version == 0x00030000:
            return [f"gid{i}" for i in range(num_glyphs)]
        if version == 0x00020000:
            r.pos = 32
            # numberOfGlyphs uint16, followed by that many uint16 name indices;
            # custom Pascal strings begin only after the index array.
            index_count = r.read("H")
            name_indices = [r.read("H") for _ in range(index_count)]
            strings = []
            while r.pos < len(data):
                length = r.data[r.pos]
                r.pos += 1
                strings.append(r.bytes(length).decode("latin-1"))
            standard = _STANDARD_MAC_NAMES
            order = []
            for idx in name_indices:
                if idx < 258:
                    order.append(standard[idx])
                else:
                    order.append(strings[idx - 258])
            return order
        if version in (0x00010000, 0x00025000):
            # PostScript format 1 / 2.5: standard names by gid.
            if version == 0x00025000:
                names = []
                for i in range(num_glyphs):
                    offset = data[32 + i]
                    offset = offset - 256 if offset >= 128 else offset
                    names.append(_STANDARD_MAC_NAMES[i + offset])
                return names
            return [_STANDARD_MAC_NAMES[i] if i < len(_STANDARD_MAC_NAMES)
                    else f"gid{i}" for i in range(num_glyphs)]
        return [f"gid{i}" for i in range(num_glyphs)]

    def _parse_glyf(self, glyf_data: bytes, loca_data: bytes):
        loca_r = Reader(loca_data)
        if self.index_to_loc_format == 0:
            offsets = [loca_r.read("H") * 2 for _ in range(self.num_glyphs + 1)]
        elif self.index_to_loc_format == 1:
            offsets = [loca_r.read("I") for _ in range(self.num_glyphs + 1)]
        else:
            raise ValueError(
                f"unsupported indexToLocFormat {self.index_to_loc_format}"
            )

        glyphs = []
        for gid in range(self.num_glyphs):
            start, end = offsets[gid], offsets[gid + 1]
            record = glyf_data[start:end]
            glyphs.append(self._parse_glyph_record(gid, record))
        return glyphs

    def _parse_glyph_record(self, gid: int, data: bytes):
        name = self.glyph_order[gid] if gid < len(self.glyph_order) else f"gid{gid}"
        if not data.strip(b"\0"):
            # Empty glyph (e.g. .notdef/space): no outlines at all.
            return SimpleGlyph(name, 0, [], [], [], 0, 0, 0, 0)
        r = Reader(data)
        number_of_contours = r.read("h")
        x_min, y_min, x_max, y_max = r.read_struct("hhhh")
        if number_of_contours == -1:
            raise VariationEngineError(
                f"glyph {name!r} is a composite glyph; composites are not supported"
            )
        if number_of_contours < 0:
            raise ValueError(f"glyph {name!r}: invalid numberOfContours")
        end_pts = [r.read("H") for _ in range(number_of_contours)]
        instruction_length = r.read("H")
        r.pos += instruction_length  # hinting is ignored

        point_count = (end_pts[-1] + 1) if end_pts else 0
        raw_flags = self._read_flags(r, point_count)
        coordinates = self._read_coordinates(r, raw_flags)
        return SimpleGlyph(
            name, number_of_contours, coordinates, raw_flags, end_pts,
            x_min, y_min, x_max, y_max,
        )

    @staticmethod
    def _read_flags(r: Reader, count: int) -> list[int]:
        flags = []
        while len(flags) < count:
            flag = r.read("B")
            flags.append(flag)
            if flag & FLAG_REPEAT:
                repeats = r.read("B")
                flags.extend([flag] * repeats)
        return flags[:count]

    @staticmethod
    def _read_coordinates(r: Reader, flags: list[int]) -> list[tuple[int, int]]:
        xs = [0] * len(flags)
        ys = [0] * len(flags)
        x = y = 0
        for i, flag in enumerate(flags):
            if flag & FLAG_X_SHORT:
                dx = r.read("B")
                if not (flag & FLAG_X_SAME_OR_POSITIVE):
                    dx = -dx
            else:
                if flag & FLAG_X_SAME_OR_POSITIVE:
                    dx = 0
                else:
                    dx = r.read("h")
            x += dx
            xs[i] = x
        for i, flag in enumerate(flags):
            if flag & FLAG_Y_SHORT:
                dy = r.read("B")
                if not (flag & FLAG_Y_SAME_OR_POSITIVE):
                    dy = -dy
            else:
                if flag & FLAG_Y_SAME_OR_POSITIVE:
                    dy = 0
                else:
                    dy = r.read("h")
            y += dy
            ys[i] = y
        return list(zip(xs, ys))

    # ------------------------------------------------------------------
    # phantom points
    # ------------------------------------------------------------------
    def phantom_points(self, gid: int) -> list[tuple[int, int]]:
        """Return the four phantom points (left, right, top, bottom).

        Only horizontal metrics exist (vmtx/VVAR are out of scope), so the
        vertical phantoms sit at (0, 0), exactly like fontTools with no
        vMetrics.
        """
        glyph = self.glyphs[gid]
        advance_width, left_side_bearing = self.h_metrics[gid]
        left_side_x = glyph.x_min - left_side_bearing
        right_side_x = left_side_x + advance_width
        return [
            (left_side_x, 0),
            (right_side_x, 0),
            (0, 0),
            (0, 0),
        ]

    def default_coordinates(self, gid: int) -> list[tuple[int, int]]:
        return list(self.glyphs[gid].coordinates) + self.phantom_points(gid)


# Standard Macintosh glyph names (post format 1/2), the first 258 entries.
_STANDARD_MAC_NAMES = [
    ".notdef", ".null", "nonmarkingreturn", "space", "exclam", "quotedbl",
    "numbersign", "dollar", "percent", "ampersand", "quotesingle",
    "parenleft", "parenright", "asterisk", "plus", "comma", "hyphen",
    "period", "slash",
] + [str(d) for d in range(10)] + [
    "colon", "semicolon", "less", "equal", "greater", "question", "at",
] + [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [
    "bracketleft", "backslash", "bracketright", "asciicircum",
    "underscore", "grave",
] + [chr(c) for c in range(ord("a"), ord("z") + 1)] + [
    "braceleft", "bar", "braceright", "asciitilde", "Adieresis", "Aring",
    "Ccedilla", "Eacute", "Ntilde", "Odieresis", "Udieresis", "aacute",
    "agrave", "acircumflex", "adieresis", "atilde", "aring", "cedilla",
    "eacute", "egrave", "ecircumflex", "edieresis", "iacute", "igrave",
    "icircumflex", "idieresis", "ntilde", "oacute", "ograve", "ocircumflex",
    "odieresis", "otilde", "uacute", "ugrave", "ucircumflex", "udieresis",
    "dagger", "degree", "cent", "sterling", "section", "bullet", "paragraph",
    "germandbls", "registered", "copyright", "trademark", "acute",
    "dieresis", "notequal", "AE", "Oslash", "infinity", "plusminus",
    "lessequal", "greaterequal", "yen", "mu", "partialdiff", "summation",
    "product", "pi", "integral", "ordfeminine", "ordmasculine", "Omega",
    "ae", "oslash", "questiondown", "exclamdown", "logicalnot", "radical",
    "florin", "approxequal", "increment", "guillemotleft", "guillemotright",
    "ellipsis", "nonbreakingspace", "Agrave", "Atilde", "Otilde", "OE", "oe",
    "endash", "emdash", "quotedblleft", "quotedblright", "quoteleft",
    "quoteright", "divide", "lozenge", "ydieresis", "Ydieresis", "fraction",
    "currency", "guilsinglleft", "guilsinglright", "fi", "fl", "daggerdbl",
    "periodcentered", "quotesinglbase", "quotedblbase", "perthousand",
    "Acircumflex", "Ecircumflex", "Aacute", "Edieresis", "Egrave", "Iacute",
    "Icircumflex", "Idieresis", "Igrave", "Oacute", "Ocircumflex",
    "apple", "Ograve", "Uacute", "Ucircumflex", "Ugrave", "dotlessi",
    "circumflex", "tilde", "macron", "breve", "dotaccent", "ring",
    "cedilla", "hungarumlaut", "ogonek", "caron", "Lslash", "lslash",
    "Scaron", "scaron", "Zcaron", "zcaron", "brokenbar", "Eth", "eth",
    "Yacute", "yacute", "Thorn", "thorn", "minus", "multiply", "onesuperior",
    "twosuperior", "threesuperior", "onehalf", "onequarter",
    "threequarters", "franc", "Gbreve", "gbreve", "Idotaccent",
    "Scedilla", "scedilla", "Cacute", "cacute", "Ccaron", "ccaron",
    "dcroat",
]
