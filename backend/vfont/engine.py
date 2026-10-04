"""The variation interpolation engine (production path).

The only third-party code this package imports in production is the Python
standard library: SFNT directory, head/maxp/hhea/hmtx/post/loca/glyf,
fvar/avar and gvar are all parsed by hand.  fontTools is used *exclusively*
by the test-suite oracle.

Pipeline for an instance at user-space location ``loc``::

    1. fvar min/default/max  -> linear normalization (clamped)
    2. avar piecewise-linear segment map
    3. quantize to F2Dot14
    4. for each gvar tuple of the glyph: tent scalar (tuple "weight")
    5. per tuple: IUP fills unreferenced points **within each contour only**
    6. accumulate scalar * delta over active tuples
    7. add to default coordinates; round each final coordinate with otRound
    4 phantom points ride along but never participate in outline IUP;
    advance = otRound(rightX - leftX), clamped to >= 0.
"""
from __future__ import annotations

from .binary import read_sfnt_tables, VariationEngineError
from .glyf import StaticFont
from .gvar import GvarTable
from .iup import (
    ORIGIN_EXPLICIT,
    ORIGIN_INFERRED,
    ORIGIN_NONE,
    iup_delta,
)
from .normalization import (
    Axis,
    normalize_location,
    ot_round,
    parse_avar,
    parse_fvar,
    support_scalar,
)

MAX_AXES = 2
MAX_SIMPLE_GLYPHS = 12
MAX_POINTS_PER_GLYPH = 300


class TupleContribution:
    __slots__ = ("index", "support", "weight", "peak_location")

    def __init__(self, index, support, weight):
        self.index = index
        self.support = support
        self.weight = weight
        self.peak_location = {
            axis: triple[1] for axis, triple in support.items()
        }

    def to_dict(self):
        return {
            "tuple": self.index,
            "weight": self.weight,
            "peak": self.peak_location,
            "support": {
                axis: list(triple) for axis, triple in self.support.items()
            },
        }


class VariationEngine:
    def __init__(self, font_bytes: bytes):
        self.blob = bytes(font_bytes)
        tables = read_sfnt_tables(self.blob)
        self.tables_present = sorted(tables)

        if "fvar" not in tables:
            raise VariationEngineError("font has no fvar table")
        self.axes, _ = parse_fvar(tables["fvar"])
        if len(self.axes) > MAX_AXES:
            raise VariationEngineError(
                f"only fonts with at most {MAX_AXES} design axes are supported "
                f"(font has {len(self.axes)})"
            )

        self.avar_segments = (
            parse_avar(tables["avar"], [a.tag for a in self.axes])
            if "avar" in tables
            else {}
        )
        self.static = StaticFont(tables)

        if self.static.num_glyphs > MAX_SIMPLE_GLYPHS:
            raise VariationEngineError(
                f"only fonts with at most {MAX_SIMPLE_GLYPHS} glyphs are "
                f"supported (font has {self.static.num_glyphs})"
            )

        self.gvar = (
            GvarTable(tables["gvar"], [a.tag for a in self.axes])
            if "gvar" in tables
            else None
        )

        if self.gvar is not None and self.gvar.glyph_count != self.static.num_glyphs:
            raise VariationEngineError(
                "gvar glyphCount does not match maxp.numGlyphs"
            )

        for forbidden in ("HVAR", "VVAR", "vmtx"):
            if forbidden in tables:
                raise VariationEngineError(
                    f"'{forbidden}' is not supported by this tool"
                )

    # ------------------------------------------------------------------
    # metadata
    # ------------------------------------------------------------------
    def font_info(self):
        glyphs = []
        for gid, glyph in enumerate(self.static.glyphs):
            glyphs.append({
                "gid": gid,
                "name": glyph.name,
                "numberOfContours": glyph.number_of_contours,
                "pointCount": len(glyph.coordinates),
                "contours": glyph.contours,
                "onCurve": glyph.on_curve,
                "defaultAdvance": self.static.h_metrics[gid][0],
                "hasVariations": (
                    self.gvar is not None
                    and self.gvar.glyph_variations(
                        gid, len(glyph.coordinates) + 4
                    )
                    is not None
                ),
            })
        return {
            "axes": [
                {
                    "tag": a.tag,
                    "min": a.min_value,
                    "default": a.default_value,
                    "max": a.max_value,
                    "nameID": a.name_id,
                }
                for a in self.axes
            ],
            "avarSegments": {
                tag: [[a, b] for a, b in segments]
                for tag, segments in self.avar_segments.items()
            },
            "unitsPerEm": self.static.units_per_em,
            "glyphs": glyphs,
            "tables": self.tables_present,
            "limits": {
                "maxAxes": MAX_AXES,
                "maxSimpleGlyphs": MAX_SIMPLE_GLYPHS,
                "maxPointsPerGlyph": MAX_POINTS_PER_GLYPH,
            },
        }

    def normalized(self, user_location: dict[str, float]):
        return normalize_location(user_location, self.axes, self.avar_segments)

    # ------------------------------------------------------------------
    # the core computation
    # ------------------------------------------------------------------
    def instance(self, user_location: dict[str, float], gid: int):
        glyph = self.static.glyphs[gid]
        if len(glyph.coordinates) > MAX_POINTS_PER_GLYPH:
            raise VariationEngineError(
                f"glyph {glyph.name!r} has {len(glyph.coordinates)} points; "
                f"limit is {MAX_POINTS_PER_GLYPH}"
            )

        normalized = self.normalized(user_location)

        default_coords = self.static.default_coordinates(gid)
        total_points = len(default_coords)
        outline_points = len(glyph.coordinates)

        # Float accumulation of deltas, exactly like fontTools' mutator.
        net_delta = [(0.0, 0.0) for _ in range(total_points)]

        # For each point, record every tuple's contribution so an anomalous
        # final coordinate can be traced to the exact rule that made it.
        contributions: dict[int, list[dict]] = {
            i: [] for i in range(total_points)
        }
        inferred_steps: dict[int, list[dict]] = {
            i: [] for i in range(total_points)
        }
        tuple_reports = []

        variations = (
            self.gvar.glyph_variations(gid, total_points)
            if self.gvar is not None
            else None
        )

        if variations:
            for tuple_index, variation in enumerate(variations):
                weight = support_scalar(normalized, variation.support)
                tuple_reports.append(
                    TupleContribution(
                        tuple_index, dict(variation.support), weight
                    ).to_dict()
                )
                if weight == 0.0:
                    # Tuple present but outside its tent: no influence, but it
                    # still shows up in the report with weight 0.
                    for point_index in range(total_points):
                        contributions[point_index].append({
                            "tuple": tuple_index,
                            "weight": weight,
                            "source": (
                                ORIGIN_EXPLICIT
                                if point_index in variation.explicit_points
                                else "unreferenced"
                            ),
                            "rawDelta": None,
                            "weightedDelta": [0.0, 0.0],
                        })
                    continue

                filled, traces = iup_delta(
                    variation.deltas, default_coords, list(glyph.end_pts)
                )
                for point_index in range(total_points):
                    dx, dy = filled[point_index]
                    sdx, sdy = weight * dx, weight * dy
                    net_delta[point_index] = (
                        net_delta[point_index][0] + sdx,
                        net_delta[point_index][1] + sdy,
                    )
                    if point_index in variation.explicit_points:
                        source = ORIGIN_EXPLICIT
                        raw = variation.deltas[point_index]
                    elif traces[point_index].origin == ORIGIN_NONE:
                        # Contour had no referenced points at all.
                        source = ORIGIN_NONE
                        raw = (dx, dy)
                    else:
                        source = ORIGIN_INFERRED
                        raw = (dx, dy)
                        trace = traces[point_index]
                        inferred_steps[point_index].append({
                            "tuple": tuple_index,
                            "weight": weight,
                            "weightedDelta": [sdx, sdy],
                            "rawDelta": [dx, dy],
                            "mode": trace.origin,
                            "xRule": _rule_to_dict(trace.x_rule),
                            "yRule": _rule_to_dict(trace.y_rule),
                        })
                    contributions[point_index].append({
                        "tuple": tuple_index,
                        "weight": weight,
                        "source": source,
                        "rawDelta": list(raw) if raw is not None else None,
                        "weightedDelta": [sdx, sdy],
                    })

        # Final coordinates: default + accumulated float delta, then otRound.
        points = []
        for point_index, ((dx, dy), (bx, by)) in enumerate(
            zip(net_delta, default_coords)
        ):
            fx, fy = bx + dx, by + dy
            rx, ry = ot_round(fx), ot_round(fy)
            is_phantom = point_index >= outline_points
            point_contributions = contributions[point_index]
            if is_phantom:
                # Phantoms are reported but never drive outline IUP.
                origin = "phantom"
            elif any(c["source"] == ORIGIN_EXPLICIT for c in point_contributions):
                origin = ORIGIN_EXPLICIT
            elif any(c["source"] == ORIGIN_INFERRED for c in point_contributions):
                origin = ORIGIN_INFERRED
            else:
                origin = ORIGIN_NONE
            points.append({
                "index": point_index,
                "phantom": is_phantom,
                "phantomRole": (
                    ["left", "right", "top", "bottom"][point_index - outline_points]
                    if is_phantom else None
                ),
                "onCurve": (
                    glyph.on_curve[point_index]
                    if not is_phantom and point_index < len(glyph.on_curve)
                    else None
                ),
                "default": [bx, by],
                "floatDelta": [dx, dy],
                "floatCoord": [fx, fy],
                "final": [rx, ry],
                "origin": origin,
                "tupleContributions": point_contributions,
                "inference": inferred_steps[point_index],
            })

        left_x = points[outline_points]["floatCoord"][0]
        right_x = points[outline_points + 1]["floatCoord"][0]
        advance_float = right_x - left_x
        advance = max(0, ot_round(advance_float))
        default_advance = self.static.h_metrics[gid][0]

        return {
            "gid": gid,
            "glyph": glyph.name,
            "requestedLocation": user_location,
            "normalizedLocation": normalized,
            "avarMapped": bool(self.avar_segments),
            "tuples": tuple_reports,
            "contours": glyph.contours,
            "points": points,
            "outlinePointCount": outline_points,
            "advance": {
                "default": default_advance,
                "float": advance_float,
                "final": advance,
                "delta": advance - default_advance,
            },
            "note": (
                "Coordinates computed directly from gvar/glyf, not from a "
                "rasterized glyph image."
            ),
        }


def _rule_to_dict(rule):
    if rule is None:
        return None
    return {
        "references": list(rule.reference_indices),
        "mode": rule.mode,
        "equalCoordinates": rule.equal_coordinates,
        "zeroFilled": rule.zero_filled,
    }
