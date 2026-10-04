"""Test oracle: fontTools' *independent* instantiation implementation.

This module exists only under ``tests/`` and is never imported by the
production engine.  It instantiates the same font with
``fontTools.varLib.instancer.instantiateVariableFont`` and returns the
resulting integer coordinates, phantom points and rounded advance width so
the self-written engine can be checked point by point under identical
otRound rounding.
"""
from __future__ import annotations

import io

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


def fonttools_instance(font_bytes: bytes, location: dict[str, float],
                        glyph_name: str):
    """Return dict with: coords (outline ints), phantoms, advance."""
    f = TTFont(io.BytesIO(font_bytes))
    f2 = instantiateVariableFont(f, dict(location), inplace=False, optimize=False)
    glyf = f2["glyf"]
    glyph = glyf[glyph_name]

    # Phantoms from the instantiated hmtx + recalculated bbox, mirroring the
    # engine's StaticFont.phantom_points (no vmtx in these test fonts).
    glyph.recalcBounds(glyf)
    advance, lsb = f2["hmtx"][glyph_name]
    left_x = glyph.xMin - lsb
    right_x = left_x + advance

    coords = list(glyph.coordinates) if hasattr(glyph, "coordinates") else []
    return {
        "coords": [(int(round(x)), int(round(y))) for x, y in coords],
        "phantoms": [(left_x, 0), (right_x, 0), (0, 0), (0, 0)],
        "advance": int(advance),
    }


def fonttools_raw_variations(font_bytes: bytes):
    """Return fontTools' own *parse* of every gvar tuple.

    Used to prove the hand-written binary decoder (shared tuples, packed
    point runs, zero/byte/word/long delta runs) agrees at the raw level, not
    merely after interpolation.
    """
    f = TTFont(io.BytesIO(font_bytes))
    out = {}
    for name, variations in f["gvar"].variations.items():
        tuples = []
        for v in variations:
            support = {}
            for axis, triple in v.axes.items():
                support[axis] = tuple(triple)
            explicit = {
                i: tuple(d) for i, d in enumerate(v.coordinates) if d is not None
            }
            tuples.append({
                "support": support,
                "explicit": explicit,
                "length": len(v.coordinates),
            })
        out[name] = tuples
    return out
