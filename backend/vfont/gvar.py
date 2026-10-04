"""Hand-written ``gvar`` (glyph variations) table parser.

Layout implemented from the TrueType/OpenType common-variation-data spec:

* the per-font shared tuple coordinate table (F2Dot14 per axis);
* short glyph offsets (uint16) are *half-offsets* and must be doubled;
* per-glyph optional shared packed point number list;
* TupleVariationHeader flags: EMBEDDED_PEAK_TUPLE, INTERMEDIATE_REGION and
  PRIVATE_POINT_NUMBERS;
* packed point numbers: count prefix (two-byte form when high bit set),
  byte/word delta runs; count == 0 means "all points";
* delta runs: zero / int8 / int16 / int32, x array then y array.

The parser produces :class:`GlyphVariation` objects whose ``deltas`` list
has length ``outline_point_count + 4`` (the four phantom points appended);
unreferenced entries are ``None``.
"""
from __future__ import annotations

import array

from .binary import Reader

EMBEDDED_PEAK_TUPLE = 0x8000
INTERMEDIATE_REGION = 0x4000
PRIVATE_POINT_NUMBERS = 0x2000

DELTAS_ARE_ZERO = 0x80
DELTAS_ARE_WORDS = 0x40
DELTAS_ARE_LONGS = 0xC0
DELTAS_SIZE_MASK = 0xC0
DELTA_RUN_COUNT_MASK = 0x3F

POINTS_ARE_WORDS = 0x80
POINT_RUN_COUNT_MASK = 0x7F

TUPLES_SHARE_POINT_NUMBERS = 0x8000
TUPLE_COUNT_MASK = 0x0FFF
TUPLE_INDEX_MASK = 0x0FFF


class GlyphVariation:
    """One tuple for one glyph.

    ``support`` maps axis tag -> (start, peak, end) normalized triples; axes
    at the implicit (0,0,0) triple are omitted.  ``deltas`` parallels the
    glyph coordinate array (outline points followed by 4 phantoms); entries
    not encoded in the tuple are ``None``.
    """

    __slots__ = ("support", "deltas", "explicit_points")

    def __init__(self, support, deltas):
        self.support = support
        self.deltas = deltas
        self.explicit_points = frozenset(
            i for i, d in enumerate(deltas) if d is not None
        )


# ---------------------------------------------------------------------------
# shared tuples (peak coordinates only; F2Dot14 per axis for every axis)
# ---------------------------------------------------------------------------


def parse_shared_tuples(data: bytes, offset: int, count: int, axis_count: int):
    tuples = []
    r = Reader(data, offset)
    for _ in range(count):
        coord = {}
        values = [r.read("h") / 16384.0 for _ in range(axis_count)]
        tuples.append(values)
    return tuples


# ---------------------------------------------------------------------------
# packed point numbers
# ---------------------------------------------------------------------------


def parse_point_numbers(data: bytes, offset: int, total_points: int):
    """Decode a packed point-number array.

    Returns ``(points, new_offset)``.  A leading count of 0 denotes *all*
    points in the glyph, i.e. range(total_points).
    """
    pos = offset
    num_points = data[pos]
    pos += 1
    if num_points & POINTS_ARE_WORDS:
        num_points = (num_points & POINT_RUN_COUNT_MASK) << 8 | data[pos]
        pos += 1
    if num_points == 0:
        return list(range(total_points)), pos

    relative = []
    while len(relative) < num_points:
        run_header = data[pos]
        pos += 1
        run_count = (run_header & POINT_RUN_COUNT_MASK) + 1
        if run_header & POINTS_ARE_WORDS:
            values = array.array("H")
            values.frombytes(data[pos : pos + 2 * run_count])
            pos += 2 * run_count
        else:
            values = array.array("B")
            values.frombytes(data[pos : pos + run_count])
            pos += run_count
        if array.array("H").itemsize > 0 and values.itemsize == 2:
            values.byteswap()
        relative.extend(values)

    absolute = []
    current = 0
    for delta in relative:
        current += delta
        absolute.append(current)
    return absolute, pos


# ---------------------------------------------------------------------------
# delta runs
# ---------------------------------------------------------------------------


def parse_delta_run(data: bytes, offset: int, count: int):
    """Decode exactly ``count`` delta values (zero/byte/word/long runs)."""
    pos = offset
    result = []
    while len(result) < count:
        header = data[pos]
        pos += 1
        run_count = (header & DELTA_RUN_COUNT_MASK) + 1
        size_bits = header & DELTAS_SIZE_MASK
        if size_bits == DELTAS_ARE_ZERO:
            result.extend([0] * run_count)
        elif size_bits == DELTAS_ARE_LONGS:
            values = array.array("i")
            values.frombytes(data[pos : pos + 4 * run_count])
            values.byteswap()
            pos += 4 * run_count
            result.extend(values)
        elif size_bits == DELTAS_ARE_WORDS:
            values = array.array("h")
            values.frombytes(data[pos : pos + 2 * run_count])
            values.byteswap()
            pos += 2 * run_count
            result.extend(values)
        else:
            values = array.array("b")
            values.frombytes(data[pos:pos + run_count])
            pos += run_count
            result.extend(values)
    return result, pos


# ---------------------------------------------------------------------------
# gvar table
# ---------------------------------------------------------------------------


class GvarTable:
    def __init__(self, data: bytes, axis_tags: list[str]):
        self.data = data
        self.axis_tags = axis_tags
        axis_count = len(axis_tags)

        self.version = Reader(data).read("H")
        self.axis_count = struct_get(data, 4, "H")
        shared_tuple_count = struct_get(data, 6, "H")
        offset_to_shared = struct_get(data, 8, "I")
        self.glyph_count = struct_get(data, 12, "H")
        self.flags = struct_get(data, 14, "H")
        offset_to_data = struct_get(data, 16, "I")
        self.offset_to_data = offset_to_data
        self.long_offsets = bool(self.flags & 1)

        self.shared_tuples = parse_shared_tuples(
            data, offset_to_shared, shared_tuple_count, axis_count
        )
        self._variation_cache: dict[int, list[GlyphVariation] | None] = {}

    def glyph_variations(
        self, gid: int, total_points: int
    ) -> list[GlyphVariation] | None:
        if gid in self._variation_cache:
            return self._variation_cache[gid]

        if self.long_offsets:
            header_size = 20
            start = struct_get(self.data, header_size + 4 * gid, "I")
            end = struct_get(self.data, header_size + 4 * (gid + 1), "I")
        else:
            header_size = 20
            raw_start = struct_get(self.data, header_size + 2 * gid, "H")
            raw_end = struct_get(self.data, header_size + 2 * (gid + 1), "H")
            # Undocumented FreeType behavior: uint16 offsets are half-offsets.
            start, end = raw_start * 2, raw_end * 2

        blob = self.data[self.offset_to_data + start : self.offset_to_data + end]
        if not blob:
            self._variation_cache[gid] = None
            return None
        result = self._parse_glyph_blob(blob, total_points)
        self._variation_cache[gid] = result
        return result

    def _parse_glyph_blob(
        self, blob: bytes, total_points: int
    ) -> list[GlyphVariation]:
        tuple_variation_count = struct_get(blob, 0, "H")
        # Per-glyph data-offset field is uint16 here (gvar, as opposed to the
        # 3-byte form used by GVAR/CFF2).
        data_offset = struct_get(blob, 2, "H")

        share_points = bool(tuple_variation_count & TUPLES_SHARE_POINT_NUMBERS)
        tuple_count = tuple_variation_count & TUPLE_COUNT_MASK

        data_pos = data_offset
        if share_points:
            shared_points, data_pos = parse_point_numbers(
                blob, data_pos, total_points
            )
        else:
            shared_points = []

        variations = []
        tuple_pos = 4
        for _ in range(tuple_count):
            data_size, tuple_flags = struct_get(blob, tuple_pos, "HH")
            header_blob = blob[tuple_pos:]
            # The per-tuple *data* blob holds packed private point numbers (if
            # any) immediately followed by the x/y delta runs.
            data_blob = blob[data_pos : data_pos + data_size]

            header_cursor = 4
            if tuple_flags & EMBEDDED_PEAK_TUPLE:
                peak = [
                    struct_get(header_blob, header_cursor + 2 * a, "h") / 16384.0
                    for a in range(self.axis_count)
                ]
                header_cursor += 2 * self.axis_count
            else:
                peak = self.shared_tuples[tuple_flags & TUPLE_INDEX_MASK]

            if tuple_flags & INTERMEDIATE_REGION:
                start_vals = [
                    struct_get(header_blob, header_cursor + 2 * a, "h") / 16384.0
                    for a in range(self.axis_count)
                ]
                header_cursor += 2 * self.axis_count
                end_vals = [
                    struct_get(header_blob, header_cursor + 2 * a, "h") / 16384.0
                    for a in range(self.axis_count)
                ]
                header_cursor += 2 * self.axis_count
            else:
                start_vals = [min(p, 0.0) for p in peak]
                end_vals = [max(p, 0.0) for p in peak]

            support = {}
            for axis_tag, s, p, e in zip(self.axis_tags, start_vals, peak, end_vals):
                if (s, p, e) != (0.0, 0.0, 0.0):
                    support[axis_tag] = (s, p, e)

            # Point numbers and deltas both live in the data blob: private
            # points (header flag) are decoded from its start, otherwise the
            # glyph-level shared point set applies.
            aux_pos = 0
            if tuple_flags & PRIVATE_POINT_NUMBERS:
                points, aux_pos = parse_point_numbers(
                    data_blob, 0, total_points
                )
            else:
                points = shared_points

            deltas = [None] * total_points
            dx, aux_pos = parse_delta_run(data_blob, aux_pos, len(points))
            dy, aux_pos = parse_delta_run(data_blob, aux_pos, len(points))
            for point_index, x, y in zip(points, dx, dy):
                if 0 <= point_index < total_points:
                    deltas[point_index] = (x, y)

            variations.append(GlyphVariation(support, deltas))
            tuple_pos += _tuple_header_size(tuple_flags, self.axis_count)
            data_pos += data_size
        return variations


def _tuple_header_size(flags: int, axis_count: int) -> int:
    size = 4
    if flags & EMBEDDED_PEAK_TUPLE:
        size += 2 * axis_count
    if flags & INTERMEDIATE_REGION:
        size += 4 * axis_count
    return size


def struct_get(data: bytes, offset: int, fmt: str):
    import struct

    fmt = ">" + fmt
    values = struct.unpack_from(fmt, data, offset)
    return values[0] if len(values) == 1 else values
