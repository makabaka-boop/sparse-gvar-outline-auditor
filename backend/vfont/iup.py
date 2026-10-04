"""IUP -- inference of deltas for un-referenced points, per contour.

This is a self-contained reimplementation of the algorithm described in the
Apple ``gvar`` specification and implemented by fontTools'
``varLib.iup`` module.  In addition to filling out a tuple's sparse delta
list, every inferred point carries a *trace* that records exactly which rule
produced it, so the UI can explain an anomalous point.

IUP never crosses contour boundaries: each contour listed in
``end_pts`` is handled independently.  The four phantom points are appended
as four separate one-point "contours", which is how they are excluded from
outline interpolation.

Per-axis rules between two referenced points with original coordinates
``c1``, ``c2`` and deltas ``d1``, ``d2``:

* if ``c1 == c2`` and ``d1 == d2`` -> every intermediate point gets ``d1``;
* if ``c1 == c2`` and ``d1 != d2`` -> every intermediate point gets ``0``
  (the "same original coordinate endpoints" rule);
* otherwise interpolate linearly between the references, but *clamp* to the
  nearer reference delta outside the [c1, c2] range -- i.e. the wrap-around
  gaps do not extrapolate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Origin markers stored on point traces.
ORIGIN_EXPLICIT = "explicit"  # delta literally encoded in the tuple
ORIGIN_INFERRED = "inferred"  # filled in by IUP from neighbours
ORIGIN_NONE = "none"  # contour had no referenced points at all (zero delta)


@dataclass
class SegmentRule:
    """How one run of points was derived from two reference points."""

    reference_indices: tuple[int, int]
    mode: str  # "interpolate" | "clamp_before" | "clamp_after"
    equal_coordinates: bool
    zero_filled: bool  # c1 == c2 but deltas differed -> forced to zero


@dataclass
class PointTrace:
    origin: str  # one of the ORIGIN_* constants
    x_rule: SegmentRule | None = None
    y_rule: SegmentRule | None = None


@dataclass
class ContourFill:
    deltas: list[tuple[float, float]] = field(default_factory=list)
    traces: list[PointTrace] = field(default_factory=list)


def _axis_rule(value, c1, c2, d1, d2, ref1, ref2):
    """Derive the delta on one axis for a point at original coordinate value."""
    if c1 == c2:
        if d1 == d2:
            return d1, SegmentRule(
                (ref1, ref2), "equal_coordinate_constant", True, False
            )
        return 0, SegmentRule(
            (ref1, ref2), "equal_coordinate_zero", True, True
        )

    if c1 > c2:
        c1, c2 = c2, c1
        d1, d2 = d2, d1

    if value <= c1:
        return d1, SegmentRule((ref1, ref2), "clamp_before", False, False)
    if value >= c2:
        return d2, SegmentRule((ref1, ref2), "clamp_after", False, False)
    # Disable fused multiply-add for bit-exact parity with the fontTools
    # reference implementation.
    nudge = (value - c1) * ((d2 - d1) / (c2 - c1))
    delta = d1 + nudge
    return delta, SegmentRule((ref1, ref2), "interpolate", False, False)


def _fill_segment(coords, point_indices, ref1, ref2, deltas, traces):
    """Fill points in ``point_indices`` using references at global indices."""
    rc1, rc2 = coords[ref1], coords[ref2]
    rd1, rd2 = deltas[ref1], deltas[ref2]
    for idx in point_indices:
        x, x_rule = _axis_rule(
            coords[idx][0], rc1[0], rc2[0], rd1[0], rd2[0], ref1, ref2
        )
        y, y_rule = _axis_rule(
            coords[idx][1], rc1[1], rc2[1], rd1[1], rd2[1], ref1, ref2
        )
        deltas[idx] = (x, y)
        traces[idx] = PointTrace(ORIGIN_INFERRED, x_rule, y_rule)


def iup_contour(sparse_deltas, coords):
    """Fill missing deltas for one closed contour.

    ``sparse_deltas`` and ``coords`` are equal-length lists for the points of
    a single contour; unreferenced deltas are ``None``.
    """
    n = len(coords)
    deltas = list(sparse_deltas)
    traces = [
        PointTrace(ORIGIN_EXPLICIT) if d is not None else PointTrace(ORIGIN_NONE)
        for d in deltas
    ]

    referenced = [i for i, d in enumerate(deltas) if d is not None]
    if not referenced:
        zero = (0, 0)
        return ContourFill(
            [zero] * n,
            [PointTrace(ORIGIN_NONE) for _ in range(n)],
        )

    first = referenced[0]
    last = referenced[-1]

    # Wrap-around gap before the first referenced point: bridge last -> first.
    if first != 0:
        _fill_segment(coords, range(0, first), first, last, deltas, traces)

    # Interior gaps between consecutive referenced points.
    for a, b in zip(referenced, referenced[1:]):
        if b - a > 1:
            _fill_segment(coords, range(a + 1, b), a, b, deltas, traces)

    # Wrap-around gap after the last referenced point.
    if last != n - 1:
        _fill_segment(coords, range(last + 1, n), last, first, deltas, traces)

    return ContourFill(deltas, traces)


def iup_one_point(delta):
    """A single-point "contour" (this is how phantom points are treated).

    An explicit phantom delta is kept; a missing one resolves to zero without
    touching any outline point.
    """
    if delta is None:
        return (0, 0), PointTrace(ORIGIN_NONE)
    return (delta[0], delta[1]), PointTrace(ORIGIN_EXPLICIT)


def iup_delta(sparse_deltas, coords, end_pts):
    """Fill deltas for a whole glyph, contour by contour.

    ``coords`` includes the four phantom points; ``end_pts`` lists only the
    outline contour endpoints.  The phantoms are each handled as singleton
    contours, matching fontTools' ``iup_delta``.

    Returns ``(filled_deltas, traces)`` where every list entry is resolved.
    """
    n = len(coords)
    assert len(sparse_deltas) == n
    assert sorted(end_pts) == end_pts
    assert n == (end_pts[-1] + 1 if end_pts else 0) + 4

    filled = [None] * n
    traces = [None] * n

    start = 0
    for end in end_pts + [n - 4, n - 3, n - 2, n - 1]:
        stop = end + 1
        if stop - start == 1:
            filled[start], traces[start] = iup_one_point(sparse_deltas[start])
        else:
            result = iup_contour(sparse_deltas[start:stop], coords[start:stop])
            for local, global_idx in enumerate(range(start, stop)):
                # Rewrite local reference indices back to glyph-global ones.
                trace = result.traces[local]
                for rule in (trace.x_rule, trace.y_rule):
                    if rule is not None:
                        rule.reference_indices = (
                            rule.reference_indices[0] + start,
                            rule.reference_indices[1] + start,
                        )
                filled[global_idx] = result.deltas[local]
                traces[global_idx] = trace
        start = stop
    return filled, traces
