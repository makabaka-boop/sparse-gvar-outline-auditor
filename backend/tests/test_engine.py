"""Point-by-point tests of the self-written engine against fontTools.

Coverage demanded by the spec:

* axis bend points (avar segment vertices) and values straddling them,
* the default instance (all zero deltas),
* sparse deltas (single touched point; wrap-around inference),
* zero-touched contours (a tuple that moves no outline point of a contour),
* same-original-coordinate endpoint rule and contour isolation,
* phantom points / advance reporting,
* F2Dot14 quantization and otRound half-up behaviour.
"""
from __future__ import annotations

import math
import os

import pytest

from vfont import VariationEngine, VariationEngineError
from vfont.normalization import (
    f2dot14_quantize,
    normalize_location,
    normalize_value,
    ot_round,
    piecewise_linear_map,
    support_scalar,
)
from vfont.iup import iup_contour, iup_delta, ORIGIN_EXPLICIT, ORIGIN_INFERRED

from make_fonts import build_font, build_large_delta_font
from oracle import fonttools_instance, fonttools_raw_variations

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="module")
def font_bytes():
    return build_font()


@pytest.fixture(scope="module")
def engine(font_bytes):
    return VariationEngine(font_bytes)


# ---------------------------------------------------------------------------
# normalization primitives
# ---------------------------------------------------------------------------


def test_ot_round_half_up_toward_plus_infinity():
    assert ot_round(0.5) == 1
    assert ot_round(-0.5) == 0  # floor(-0.5 + 0.5) == 0, not -1
    assert ot_round(-0.6) == -1
    assert ot_round(2.4) == 2


def test_normalize_value_examples():
    triple = (100.0, 400.0, 900.0)
    assert normalize_value(400, triple) == 0.0
    assert normalize_value(100, triple) == -1.0
    assert normalize_value(900, triple) == 1.0
    assert normalize_value(650, triple) == 0.5
    # clamping
    assert normalize_value(0, triple) == -1.0
    assert normalize_value(1000, triple) == 1.0


def test_f2dot14_quantization():
    assert f2dot14_quantize(0.5) == 0.5
    q = f2dot14_quantize(1.0 / 3.0)
    assert q == ot_round((1.0 / 3.0) * 16384) / 16384


def test_piecewise_linear_map_bends():
    mapping = [(-1.0, -1.0), (0.0, 0.0), (0.5, 0.7), (1.0, 1.0)]
    assert piecewise_linear_map(0.5, mapping) == 0.7
    assert piecewise_linear_map(0.25, mapping) == 0.35
    # extrapolation keeps the outer segment slope (identity here)
    assert piecewise_linear_map(1.5, mapping) == 1.5


def test_support_scalar_tents():
    support = {"w": (0.0, 1.0, 1.0)}
    assert support_scalar({"w": 1.0}, support) == 1.0
    assert support_scalar({"w": 0.5}, support) == 0.5
    assert support_scalar({"w": 0.0}, support) == 0.0
    # intermediate region (0.25, 0.5, 0.75)
    support2 = {"w": (0.25, 0.5, 0.75)}
    assert support_scalar({"w": 0.5}, support2) == 1.0
    assert support_scalar({"w": 0.25}, support2) == 0.0
    assert support_scalar({"w": 0.2}, support2) == 0.0
    # malformed tent straddling zero -> zero influence
    assert support_scalar({"w": 0.1}, {"w": (-0.5, 0.5, 1.0)}) == 0.0


# ---------------------------------------------------------------------------
# IUP rules
# ---------------------------------------------------------------------------


def test_iup_single_touched_point():
    # rectangle: (0,0)(100,0)(100,100)(0,100); only point 1 gets (20, 0).
    coords = [(0, 0), (100, 0), (100, 100), (0, 100)]
    sparse = [None, (20, 0), None, None]
    fill = iup_contour(sparse, coords)
    assert fill.deltas[1] == (20, 0)
    # Every other point is inferred from the single reference (constant).
    for i in (0, 2, 3):
        assert fill.traces[i].origin == ORIGIN_INFERRED
        assert fill.deltas[i] == (20, 0)


def test_iup_wrap_around():
    # referenced points 1 and 2; wrap gap 3,0 bridges 2 -> 1.
    coords = [(0, 0), (100, 0), (100, 100), (0, 100)]
    sparse = [None, (0, 10), (0, 30), None]
    fill = iup_contour(sparse, coords)
    # point 0 at y=0, between refs y=0(p1) and y=100(p2) -> interpolate 10
    assert fill.deltas[0][1] == pytest.approx(10.0)
    # point 3 y=100 outside [0,100]? equal -> clamp to p2 (30)
    assert fill.deltas[3][1] == 30


def test_iup_equal_original_coordinates_zero_fill():
    # Two referenced points share X=100 but carry different X deltas:
    # intermediate X must be forced to zero.
    coords = [(0, 0), (100, 0), (100, 100)]
    sparse = [None, (10, 5), (-20, 5)]
    fill = iup_contour(sparse, coords)
    # point 0 bridges last(2)->first(1); ref X equal, deltas differ -> 0
    assert fill.deltas[0][0] == 0
    # Y deltas equal (5,5) -> constant 5
    assert fill.deltas[0][1] == 5
    rule = fill.traces[0]
    assert rule.x_rule.zero_filled is True
    assert rule.y_rule.zero_filled is False


def test_iup_no_referenced_points_is_zero():
    coords = [(0, 0), (10, 10)]
    fill = iup_contour([None, None], coords)
    assert fill.deltas == [(0, 0), (0, 0)]


def test_iup_delta_keeps_contours_isolated_and_phantoms_singletons():
    # two contours of two points each + 4 phantoms
    coords = [(0, 0), (10, 0), (100, 0), (110, 0),
              (0, 0), (999, 0), (0, 0), (0, 0)]
    end_pts = [1, 3]
    sparse = [None, (5, 0), None, None, None, (9, 0), None, None]
    filled, traces = iup_delta(sparse, coords, end_pts)
    # contour 1: point 0 inferred from point 1 (5)
    assert filled[0] == (5, 0)
    # contour 2 untouched -> zeros, must NOT inherit contour 1's 5
    assert filled[2] == (0, 0)
    assert filled[3] == (0, 0)
    # phantom 1 explicit (9) kept; other phantoms zero
    assert filled[5] == (9, 0)
    assert filled[4] == (0, 0)
    assert traces[5].origin == ORIGIN_EXPLICIT


# ---------------------------------------------------------------------------
# end-to-end vs fontTools oracle
# ---------------------------------------------------------------------------


GLYPH_GIDS = {"square": 1, "wide": 2, "double": 3, "curve": 4, "tiny": 5}


@pytest.mark.parametrize("wght,wdth", [
    (400, 100),    # exact default
    (900, 200),    # both max
    (100, 50),     # both min
    (650, 125),    # mid-range (normalized 0.5 -> avar 0.7)
    (525, 112),    # non-F2Dot14 value (quantization matters)
    (775, 180),
    (400, 200),    # wght default, wdth max
    (900, 100),    # wght max, wdth default
    (250, 75),
])
def test_matches_fonttools_per_point(engine, font_bytes, wght, wdth):
    location = {"wght": float(wght), "wdth": float(wdth)}
    for name, gid in GLYPH_GIDS.items():
        result = engine.instance(location, gid)
        oracle = fonttools_instance(font_bytes, location, name)

        mine = [(p["final"][0], p["final"][1])
                for p in result["points"] if not p["phantom"]]
        assert mine == oracle["coords"], (
            f"{name} @ {location}: outline mismatch\n"
            f"engine={mine}\noracle={oracle['coords']}"
        )

        phantoms = [(p["final"][0], p["final"][1])
                    for p in result["points"] if p["phantom"]]
        assert phantoms == oracle["phantoms"], (
            f"{name} phantom mismatch {phantoms} vs {oracle['phantoms']}"
        )
        assert result["advance"]["final"] == oracle["advance"], name


def test_default_instance_is_identity(engine):
    result = engine.instance({"wght": 400, "wdth": 100}, GLYPH_GIDS["square"])
    for p in result["points"]:
        assert p["final"] == p["default"]
        assert p["floatDelta"] == [0.0, 0.0]
    assert result["advance"]["delta"] == 0
    # weights at the default are all zero (peak tents start at 0)
    assert all(t["weight"] == 0.0 for t in result["tuples"])


def test_avar_bend_points_match_oracle(engine, font_bytes):
    # normalized wght 0.5 == user 650 (upper half linear) pre-avar; avar moves
    # it to 0.7. Verify the engine's normalized value and the oracle agree.
    result = engine.instance({"wght": 650, "wdth": 100}, GLYPH_GIDS["square"])
    assert result["normalizedLocation"]["wght"] == pytest.approx(0.7, abs=1e-3)
    oracle = fonttools_instance(font_bytes,
                                {"wght": 650.0, "wdth": 100.0}, "square")
    mine = [(p["final"][0], p["final"][1])
            for p in result["points"] if not p["phantom"]]
    assert mine == oracle["coords"]


def test_sparse_single_touch_reporting(engine):
    result = engine.instance({"wght": 400, "wdth": 200}, GLYPH_GIDS["square"])
    by_index = {p["index"]: p for p in result["points"]}
    # wdth tuple (index 1) encodes only point 1 explicitly.
    p1 = by_index[1]
    contrib1 = next(c for c in p1["tupleContributions"] if c["tuple"] == 1)
    assert contrib1["source"] == "explicit"
    # points 0,2,3 are inferred in tuple 1, each with rules pointing to 1.
    for idx in (0, 2, 3):
        step = next(s for s in by_index[idx]["inference"] if s["tuple"] == 1)
        assert step["xRule"]["references"] == [1, 1]
        assert step["yRule"]["references"] == [1, 1]


def test_zero_touched_contour(engine):
    # 'wide': wght tuple moves the phantoms but no outline point explicitly.
    result = engine.instance({"wght": 900, "wdth": 100}, GLYPH_GIDS["wide"])
    outline = [p for p in result["points"] if not p["phantom"]]
    assert all(p["origin"] == "none" for p in outline)
    assert all(p["final"] == p["default"] for p in outline)
    # advance still grew by the phantom delta (150)
    assert result["advance"]["final"] == result["advance"]["default"] + 150


def test_double_contour_isolation(engine):
    result = engine.instance({"wght": 900, "wdth": 100}, GLYPH_GIDS["double"])
    pts = result["points"]
    # contour 1 points 0..3 move up 50 (p1,p3 inferred from p0,p2);
    # contour 2 points 4..7 have their own explicit deltas (-30 x).
    for i in range(0, 4):
        assert pts[i]["final"][1] == pts[i]["default"][1] + 50
    for i in range(4, 8):
        assert pts[i]["final"][0] == pts[i]["default"][0] - 30
    # inference record for contour-1 point 1 references only contour-1 points
    step = next(s for s in pts[1]["inference"] if s["tuple"] == 0)
    refs = set(step["xRule"]["references"])
    assert refs <= {0, 2}


def test_advance_from_horizontal_phantom_only(engine):
    # square wght tuple adds +200 to the right phantom only.
    result = engine.instance({"wght": 900, "wdth": 100}, GLYPH_GIDS["square"])
    assert result["advance"]["delta"] == 200
    right = next(p for p in result["points"] if p.get("phantomRole") == "right")
    assert right["floatDelta"][0] == 200.0


def test_tuple_weights_reported(engine):
    result = engine.instance({"wght": 650, "wdth": 150}, GLYPH_GIDS["square"])
    weights = {t["tuple"]: t["weight"] for t in result["tuples"]}
    # wght peak-1 tent at avar-mapped 0.7; wdth at normalized 0.5.
    assert weights[0] == pytest.approx(0.7, abs=1e-3)
    assert weights[1] == pytest.approx(0.5)
    # intermediate tuple tent (0.25,0.5,0.75) at loc 0.7 (on its right slope):
    # (0.7 - 0.75)/(0.5 - 0.75) = 0.2
    assert weights[2] == pytest.approx(0.2, abs=1e-3)


# ---------------------------------------------------------------------------
# int32 delta runs (DELTAS_ARE_LONGS)
# ---------------------------------------------------------------------------


def test_int32_delta_runs_match_oracle():
    data = build_large_delta_font()
    engine = VariationEngine(data)
    result = engine.instance({"wght": 900, "wdth": 100}, 1)  # box
    oracle = fonttools_instance(data, {"wght": 900.0, "wdth": 100.0}, "box")
    mine = [(p["final"][0], p["final"][1])
            for p in result["points"] if not p["phantom"]]
    assert mine == oracle["coords"]
    # point 0 carries the explicit +40000/-40000
    p0 = result["points"][0]
    assert p0["origin"] == "explicit"
    assert p0["final"] == [40000, -40000]
    # advance: phantom right moved +50000
    assert result["advance"]["delta"] == 50000


# ---------------------------------------------------------------------------
# long (uint32) glyph-offset format, via an SFNT rebuild
# ---------------------------------------------------------------------------


def _replace_table_and_resave(font_bytes, tag, new_table):
    """Rebuild an SFNT container with one table replaced.

    Table checksums other than the replaced table are left as recorded; only
    structural offsets matter for the engine, and we rebuild the directory.
    """
    import struct

    num = struct.unpack_from(">H", font_bytes, 4)[0]
    entries = []
    for i in range(num):
        off = 12 + 16 * i
        entries.append({
            "tag": font_bytes[off:off + 4],
            "checksum": struct.unpack_from(">I", font_bytes, off + 4)[0],
            "data": font_bytes[
                struct.unpack_from(">I", font_bytes, off + 8)[0]:
                struct.unpack_from(">I", font_bytes, off + 8)[0]
                + struct.unpack_from(">I", font_bytes, off + 12)[0]
            ],
        })
    for e in entries:
        if e["tag"] == tag.encode("latin-1"):
            e["data"] = new_table

    # Rebuild (no search-range optimization needed; still valid SFNT).
    search_range = 0
    entry_selector = 0
    power = 1
    while power * 2 <= num:
        power *= 2
        entry_selector += 1
    search_range = power * 16
    range_shift = num * 16 - search_range

    out = bytearray()
    out += struct.pack(">IHHHH", 0x00010000, num, search_range,
                       entry_selector, range_shift)
    offset = 12 + 16 * num
    bodies = []
    for e in entries:
        out += e["tag"]
        out += struct.pack(">III", e["checksum"], offset, len(e["data"]))
        bodies.append((offset, e["data"]))
        offset += len(e["data"])
    for off, body in bodies:
        assert len(out) == off
        out += body
    return bytes(out)


def _convert_gvar_short_to_long_offsets(gvar_bytes):
    """Rewrite a short-offset gvar table to the uint32 offset format.

    Short glyph offsets are half-units (must be doubled); converting also lets
    us prove the parser handles the 4-byte offset form.
    """
    import struct

    version = struct.unpack_from(">H", gvar_bytes, 0)[0]
    reserved = struct.unpack_from(">H", gvar_bytes, 2)[0]
    axis_count = struct.unpack_from(">H", gvar_bytes, 4)[0]
    shared_tuple_count = struct.unpack_from(">H", gvar_bytes, 6)[0]
    old_shared_off = struct.unpack_from(">I", gvar_bytes, 8)[0]
    glyph_count = struct.unpack_from(">H", gvar_bytes, 12)[0]
    old_flags = struct.unpack_from(">H", gvar_bytes, 14)[0]
    offset_to_data = struct.unpack_from(">I", gvar_bytes, 16)[0]
    assert not (old_flags & 1), "expected short-offset input"

    half_offsets = [
        struct.unpack_from(">H", gvar_bytes, 20 + 2 * i)[0]
        for i in range(glyph_count + 1)
    ]
    offsets = [h * 2 for h in half_offsets]

    shared_tuples = gvar_bytes[old_shared_off:offset_to_data]
    glyph_data = gvar_bytes[offset_to_data:]

    # Re-layout: 20-byte header + uint32 offsets + shared tuples + glyph data.
    new_offset_array_start = 20
    new_shared_off = new_offset_array_start + 4 * (glyph_count + 1)
    new_vardata_off = new_shared_off + len(shared_tuples)

    out = bytearray()
    out += struct.pack(">HHHH", version, reserved, axis_count, shared_tuple_count)
    out += struct.pack(">I", new_shared_off)
    out += struct.pack(">HH", glyph_count, old_flags | 1)  # long offsets
    out += struct.pack(">I", new_vardata_off)
    for o in offsets:
        out += struct.pack(">I", o)
    out += shared_tuples
    out += glyph_data
    return bytes(out)


def test_long_offset_gvar_matches_short(engine, font_bytes):
    import struct

    # Extract the original (short-offset) gvar and convert it.
    num = struct.unpack_from(">H", font_bytes, 4)[0]
    gvar_data = None
    for i in range(num):
        off = 12 + 16 * i
        if font_bytes[off:off + 4] == b"gvar":
            toff, ln = struct.unpack_from(">II", font_bytes, off + 8)
            gvar_data = font_bytes[toff:toff + ln]
    assert gvar_data is not None
    long_gvar = _convert_gvar_short_to_long_offsets(gvar_data)
    assert long_gvar[14:16] == b"\x00\x01"  # big-endian flags = 1

    long_font = _replace_table_and_resave(font_bytes, "gvar", long_gvar)
    long_engine = VariationEngine(long_font)

    for location in [
        {"wght": 900, "wdth": 200},
        {"wght": 650, "wdth": 125},
        {"wght": 400, "wdth": 100},
    ]:
        for gid in range(long_engine.static.num_glyphs):
            a = engine.instance(location, gid)
            b = long_engine.instance(location, gid)
            assert ([p["final"] for p in a["points"]]
                    == [p["final"] for p in b["points"]])
            assert a["advance"]["final"] == b["advance"]["final"]


def test_rejects_more_than_two_axes():
    import io

    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._f_v_a_r import Axis

    font = TTFont(io.BytesIO(build_font()))
    extra = Axis()
    extra.axisTag, extra.axisNameID = "opsz", 258
    extra.minValue, extra.defaultValue, extra.maxValue = 10, 12, 200
    extra.flags = 0
    font["fvar"].axes.append(extra)
    # gvar axisCount would mismatch; remove it so only the axis guard runs.
    del font["gvar"]
    buf = io.BytesIO()
    font.save(buf)
    with pytest.raises(VariationEngineError, match="axes"):
        VariationEngine(buf.getvalue())


def test_raw_gvar_decode_matches_fonttools(engine, font_bytes):
    """The hand-written gvar binary decoder must match fontTools tuple-for-tuple."""
    expected = fonttools_raw_variations(font_bytes)
    info = engine.font_info()
    name_to_gid = {g["name"]: g["gid"] for g in info["glyphs"]}

    assert set(expected) <= set(name_to_gid)
    for name, ft_tuples in expected.items():
        gid = name_to_gid[name]
        glyph = engine.static.glyphs[gid]
        total = len(glyph.coordinates) + 4
        mine = engine.gvar.glyph_variations(gid, total)
        if not ft_tuples:
            assert not mine
            continue
        assert mine is not None and len(mine) == len(ft_tuples)
        for got, want in zip(mine, ft_tuples):
            assert got.support == want["support"]
            assert len(got.deltas) == want["length"]
            got_explicit = {
                i: tuple(d) for i, d in enumerate(got.deltas) if d is not None
            }
            assert got_explicit == want["explicit"]


def test_negative_peak_tuple_weight_and_isolation(engine):
    # double has a negative wght tent (-1, -0.75, 0). At wght=100 (norm -1)
    # its scalar on the left slope is (-1 - -1)/(-0.75 - -1) = 0.
    result = engine.instance({"wght": 100, "wdth": 100}, GLYPH_GIDS["double"])
    neg = next(t for t in result["tuples"] if t["peak"].get("wght", 0) < 0)
    assert neg["weight"] == pytest.approx(0.0)
    # exactly at the negative peak after the avar bend.  avar maps input
    # norm -0.6875 to -0.75 (segment -1..-0.5 -> -1..-0.6); lower-half norm
    # -0.6875 == user wght 400 + (-0.6875)*300 = 193.75.
    result2 = engine.instance({"wght": 193.75, "wdth": 100}, GLYPH_GIDS["double"])
    neg2 = next(t for t in result2["tuples"] if t["peak"].get("wght", 0) < 0)
    assert neg2["weight"] == pytest.approx(1.0, abs=2e-3)


def test_empty_and_single_point_glyphs(engine, font_bytes):
    for name, gid in ((".notdef", 0), ("space", 6)):
        result = engine.instance({"wght": 900, "wdth": 200}, gid)
        oracle = fonttools_instance(font_bytes,
                                    {"wght": 900.0, "wdth": 200.0}, name)
        mine = [(p["final"][0], p["final"][1])
                for p in result["points"] if not p["phantom"]]
        assert mine == oracle["coords"], name
        assert result["advance"]["final"] == oracle["advance"], name


def test_composite_glyph_is_rejected():
    import io

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "part", "comp"])
    fb.setupCharacterMap({0x41: "comp"})

    def square_glyph():
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0)); pen.lineTo((100, 0))
        pen.lineTo((100, 100)); pen.lineTo((0, 100)); pen.closePath()
        return pen.glyph()

    glyphs = {".notdef": square_glyph(), "part": square_glyph(),
              "comp": square_glyph()}
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({".notdef": (300, 0), "part": (300, 0),
                              "comp": (300, 0)})
    # Replace 'comp' with a composite using the now-valid glyph set.
    comp_pen = TTGlyphPen(fb.font.getGlyphSet())
    comp_pen.addComponent("part", (1, 0, 0, 1, 0, 0))
    fb.font["glyf"]["comp"] = comp_pen.glyph()
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "C", "styleName": "R"})
    fb.setupOS2()
    fb.setupPost()

    # The engine requires fvar even to reach the glyf composite check; add a
    # minimal one-axis fvar (no gvar needed).
    from make_fonts import _build_fvar
    fb.font["fvar"] = _build_fvar()

    buf = io.BytesIO()
    fb.font.save(buf)
    # The glyf reader walks every glyph, so a composite is refused at load.
    with pytest.raises(VariationEngineError, match="composite"):
        VariationEngine(buf.getvalue())
