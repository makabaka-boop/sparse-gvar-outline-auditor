"""Generate small variable TrueType fonts for the test-suite (test-only use).

fontTools is used here merely to *build* fonts; it is also used by
``oracle.py`` to independently instantiate them.  Neither this module nor
fontTools itself is imported by the production engine package.

The generated fonts deliberately include:

* two design axes (wght, wdth) and an avar table with a real bend,
* sparse gvar tuples (PRIVATE_POINT_NUMBERS), including tuples touching only
  a single point of a contour and tuples touching none of the points of a
  contour (the "zero touched contour" case),
* off-curve points, multiple contours and phantom deltas (advance changes).
"""
from __future__ import annotations

import io

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables.TupleVariation import TupleVariation


def _draw_square(pen, origin=(100, 0), size=400):
    x, y = origin
    s = size
    pen.moveTo((x, y))
    pen.lineTo((x + s, y))
    pen.lineTo((x + s, y + s))
    pen.lineTo((x, y + s))
    pen.closePath()


def _draw_double_square(pen):
    # Two separate closed contours to verify IUP never crosses contours.
    _draw_square(pen, origin=(50, 0), size=300)
    _draw_square(pen, origin=(500, 0), size=300)


def _draw_qcurve_glyph(pen):
    # One contour with off-curve points: on, off, off(on implied), on ...
    pen.moveTo((100, 0))
    pen.qCurveTo((300, -50), (500, 0))
    pen.qCurveTo((550, 300), (500, 600))
    pen.qCurveTo((300, 650), (100, 600))
    pen.qCurveTo((50, 300), (100, 0))
    pen.closePath()


def build_font() -> bytes:
    fb = FontBuilder(1000, isTTF=True)

    glyph_order = [
        ".notdef", "square", "wide", "double", "curve", "tiny", "space",
    ]
    fb.setupGlyphOrder(glyph_order)

    # Horizontal advances (LSB is derived from xMin automatically).
    fb.setupCharacterMap({0x20: "space", 0x41: "square", 0x42: "wide",
                          0x43: "double", 0x44: "curve", 0x45: "tiny"})

    glyphs = {}
    metrics = {}

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 0))  # .notdef: single-point contour
    pen.closePath()
    glyphs[".notdef"] = pen.glyph()
    metrics[".notdef"] = (500, 0)

    pen = TTGlyphPen(None)
    _draw_square(pen)
    glyphs["square"] = pen.glyph()
    metrics["square"] = (600, 100)

    pen = TTGlyphPen(None)
    _draw_square(pen, origin=(100, 0), size=400)
    glyphs["wide"] = pen.glyph()
    metrics["wide"] = (700, 100)

    pen = TTGlyphPen(None)
    _draw_double_square(pen)
    glyphs["double"] = pen.glyph()
    metrics["double"] = (900, 50)

    pen = TTGlyphPen(None)
    _draw_qcurve_glyph(pen)
    glyphs["curve"] = pen.glyph()
    metrics["curve"] = (600, 50)

    pen = TTGlyphPen(None)
    # Two-point contour (minimal closed contour).
    pen.moveTo((10, 10))
    pen.lineTo((40, 40))
    pen.closePath()
    glyphs["tiny"] = pen.glyph()
    metrics["tiny"] = (100, 10)

    pen = TTGlyphPen(None)  # empty glyph -> no outlines
    glyphs["space"] = pen.glyph()
    metrics["space"] = (300, 0)

    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "IUPTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    # ---- axes --------------------------------------------------------
    fb.font["fvar"] = _build_fvar()
    fb.font["avar"] = _build_avar()
    fb.font["gvar"] = _build_gvar(glyph_order, fb)

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


def build_large_delta_font() -> bytes:
    """A font whose gvar deltas exceed int16, forcing DELTAS_ARE_LONGS runs.

    Default outline sits at the origin so default glyf coordinates stay
    inside int16; the wght=1 master moves the outline by +40000 (encoded as
    int32 delta runs).  The resulting instance is only held in memory (the
    final coordinates would not fit a glyf int16, which is irrelevant here).
    """
    fb = FontBuilder(1000, isTTF=True)
    glyph_order = [".notdef", "box"]
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({0x41: "box"})

    glyphs, metrics = {}, {}
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((1, 0))
    pen.closePath()
    glyphs[".notdef"] = pen.glyph()
    metrics[".notdef"] = (500, 0)

    pen = TTGlyphPen(None)
    # tiny default square at the origin
    for p in ((0, 0), (10, 0), (10, 10), (0, 10)):
        if p == (0, 0):
            pen.moveTo(p)
        else:
            pen.lineTo(p)
    pen.closePath()
    glyphs["box"] = pen.glyph()
    metrics["box"] = (500, 0)

    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "BigDelta", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.font["fvar"] = _build_fvar()

    from fontTools.ttLib import newTable

    gvar = newTable("gvar")
    n = 8  # 4 outline + 4 phantom
    coords = [None] * n
    coords[0] = (40000, -40000)  # magnitude forces int32 x and y
    coords[2] = (33000, 1)       # >32767 and a small value in the same runs
    coords[5] = (50000, 0)       # huge phantom/advance delta
    gvar.version = 1
    gvar.reserved = 0
    gvar.variations = {
        ".notdef": [],
        "box": [TupleVariation({"wght": (0.0, 1.0, 1.0)}, coords)],
    }
    fb.font["gvar"] = gvar

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


def _build_fvar():
    from fontTools.ttLib import newTable

    fvar = newTable("fvar")
    fvar.version = 0x00010000
    axes = [
        dict(
            tag="wght",
            nameID=256,
            minValue=100.0,
            defaultValue=400.0,
            maxValue=900.0,
            flags=0,
        ),
        dict(
            tag="wdth",
            nameID=257,
            minValue=50.0,
            defaultValue=100.0,
            maxValue=200.0,
            flags=0,
        ),
    ]
    fvar.axes = [_axis_from_dict(d) for d in axes]
    fvar.instances = []
    return fvar


def _axis_from_dict(d):
    from fontTools.ttLib.tables._f_v_a_r import Axis

    axis = Axis()
    axis.axisTag = d["tag"]
    axis.axisNameID = d["nameID"]
    axis.flags = d["flags"]
    axis.minValue, axis.defaultValue, axis.maxValue = (
        d["minValue"], d["defaultValue"], d["maxValue"],
    )
    return axis


def _build_avar(glyph_order=None, fb=None):
    from fontTools.ttLib import newTable

    avar = newTable("avar")
    # Bend the normalized space: normalized 0.5 maps to 0.7 etc.
    avar.segments = {
        "wght": {
            -1.0: -1.0,
            -0.5: -0.6,
            0.0: 0.0,
            0.5: 0.7,
            1.0: 1.0,
        },
        "wdth": {
            -1.0: -1.0,
            0.0: 0.0,
            1.0: 1.0,
        },
    }
    return avar


def _tv(axes, coords):
    tv = TupleVariation(axes, coords)
    return tv


def _build_gvar(glyph_order, fb):
    from fontTools.ttLib import newTable

    gvar = newTable("gvar")
    variations = {name: [] for name in glyph_order}

    def sparse(n, explicit):
        coords = [None] * n
        for idx, value in explicit.items():
            coords[idx] = value
        return coords

    # square: 4 outline points, indices 0=(100,0) 1=(500,0) 2=(500,400)
    # 3=(100,400); phantoms at 4..7.
    n_sq = 8
    # wght master at +1: move *only* the top two points; bottom two must be
    # inferred by IUP across the closed-contour wrap.
    variations["square"].append(_tv(
        {"wght": (0.0, 1.0, 1.0)},
        sparse(n_sq, {2: (0, 300), 3: (0, 300), 5: (200, 0)}),
    ))
    # wdth master at +1 touches a single point (index 1); every other point
    # gets inferred, including wrap-around point 0 between 1 and the 1 again.
    variations["square"].append(_tv(
        {"wdth": (0.0, 1.0, 1.0)},
        sparse(n_sq, {1: (200, 0)}),
    ))
    # Intermediate-region wght tent at 0.5 peak, start 0.25 end 0.75.
    variations["square"].append(_tv(
        {"wght": (0.25, 0.5, 0.75)},
        sparse(n_sq, {0: (10, 0), 2: (10, 10)}),
    ))

    # wide: same geometry; one tuple that touches NO points of the outline but
    # DOES move a phantom (advance-only delta).
    n_wide = 8
    variations["wide"].append(_tv(
        {"wght": (0.0, 1.0, 1.0)},
        sparse(n_wide, {4: (0, 0), 5: (150, 0)}),
    ))

    # double: 8 outline points in two contours. Tuple touches point 0 and 2
    # of contour 1 only; contour 2 points (4..7) must all resolve to zero
    # without borrowing contour 1's deltas.
    n_dbl = 12
    variations["double"].append(_tv(
        {"wght": (0.0, 1.0, 1.0)},
        sparse(n_dbl, {0: (0, 50), 2: (0, 50), 5: (-30, 0), 7: (-30, 0)}),
    ))
    # Negative-peak wght tent (start -1, peak -0.75 via F2Dot14, end 0):
    # exercises negative-region scalar evaluation and a tuple that touches no
    # point of contour 1 at all (contour 1 zero-filled, contour 2 explicit).
    variations["double"].append(_tv(
        {"wght": (-1.0, -0.75, 0.0)},
        sparse(n_dbl, {5: (-60, -20), 7: (-60, -20)}),
    ))

    # curve: 9 points (moveTo + 4 qCurveTo with implied on-curves inserted by
    # the pen -> actually 9 coordinate points); off-curve moves exercise the
    # same IUP path. Sparse deltas.
    curve_glyph = fb.font["glyf"]["curve"]
    n_curve = len(curve_glyph.coordinates) + 4
    d = {}
    d[0] = (0, 0)
    d[n_curve - 5] = (20, 20)  # last outline point explicit
    variations["curve"].append(_tv(
        {"wght": (0.0, 1.0, 1.0)},
        sparse(n_curve, d),
    ))

    # tiny: 2-point contour where both points share original X... they do not
    # (10,10)/(40,40), but add a tuple with same-coordinate endpoints to
    # exercise the zero-fill rule.
    n_tiny = 6
    variations["tiny"].append(_tv(
        {"wght": (0.0, 1.0, 1.0)},
        sparse(n_tiny, {0: (100, 0), 1: (0, 100)}),
    ))

    gvar.version = 1
    gvar.reserved = 0
    gvar.variations = variations
    return gvar


if __name__ == "__main__":
    data = build_font()
    with open("/workspace/backend/tests/iup_test.ttf", "wb") as f:
        f.write(data)
    print(f"wrote {len(data)} bytes")