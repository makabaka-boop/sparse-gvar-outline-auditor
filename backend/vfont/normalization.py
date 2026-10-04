"""fvar parsing, avar piecewise-linear mapping and design->normalized conversion.

These are the self-contained equivalents of fontTools' ``fvar``/``avar``
readers and ``normalizeLocation``/``piecewiseLinearMap``.  Behaviour matches
the OpenType variation spec: user coordinates clamp to [min, max], map
linearly to [-1, 0, +1] around the default, then the avar segment map is
applied, and finally the result is quantized to F2Dot14.
"""
from __future__ import annotations

import math

from .binary import Reader

# ---------------------------------------------------------------------------
# Rounding (fontTools.misc.roundTools.otRound)
# ---------------------------------------------------------------------------


def ot_round(value: float) -> int:
    """Round-half-up (towards +Infinity): floor(v + 0.5).

    Note this is *not* symmetric around zero: ot_round(-0.5) == 0.
    """
    return int(math.floor(value + 0.5))


def f2dot14_quantize(value: float) -> float:
    """Quantize a normalized float to the nearest representable F2Dot14."""
    fixed = ot_round(value * 16384.0)
    # Clamp to the signed F2Dot14 range just like struct packing would.
    fixed = max(-32768, min(32767, fixed))
    return fixed / 16384.0


# ---------------------------------------------------------------------------
# fvar
# ---------------------------------------------------------------------------


class Axis:
    __slots__ = ("tag", "min_value", "default_value", "max_value", "flags", "name_id")

    def __init__(self, tag, min_value, default_value, max_value, flags, name_id):
        self.tag = tag
        self.min_value = min_value
        self.default_value = default_value
        self.max_value = max_value
        self.flags = flags
        self.name_id = name_id

    @property
    def triple(self):
        return (self.min_value, self.default_value, self.max_value)


def parse_fvar(data: bytes) -> tuple[list[Axis], list[dict]]:
    r = Reader(data)
    _major, _minor = r.read("H"), r.read("H")
    axes_array_offset = r.read("H")
    _reserved1 = r.read("H")
    axis_count = r.read("H")
    axis_size = r.read("H")
    instance_count = r.read("H")
    instance_size = r.read("H")

    axes: list[Axis] = []
    r.seek(axes_array_offset)
    for _ in range(axis_count):
        axis_record = r.bytes(axis_size)
        ar = Reader(axis_record)
        tag = ar.bytes(4).decode("latin-1")
        # min/default/max are F2Dot14? No -- fvar uses Fixed (16.16).
        min_fixed, default_fixed, max_fixed = ar.read_struct("iii")
        min_value = min_fixed / 65536.0
        default_value = default_fixed / 65536.0
        max_value = max_fixed / 65536.0
        flags = ar.read("H")
        name_id = ar.read("H")
        axes.append(
            Axis(tag, min_value, default_value, max_value, flags, name_id)
        )

    # Named instances are not needed by the interpolation engine; skip them.
    return axes, []


# ---------------------------------------------------------------------------
# avar
# ---------------------------------------------------------------------------


def parse_avar(data: bytes, axis_tags: list[str]) -> dict[str, list[tuple[float, float]]]:
    """Parse an avar (Axis Variations) table.

    Segment maps carry no axis tags; they appear in fvar axis order.
    """
    r = Reader(data)
    _major, _minor, _reserved, axis_count = r.read_struct("HHHH")
    segments: dict[str, list[tuple[float, float]]] = {}
    for tag in axis_tags[:axis_count]:
        pair_count = r.read("H")
        mapping = []
        for _ in range(pair_count):
            from_norm = r.read("h") / 16384.0
            to_norm = r.read("h") / 16384.0
            mapping.append((from_norm, to_norm))
        segments[tag] = mapping
    return segments


def piecewise_linear_map(value: float, mapping: list[tuple[float, float]]) -> float:
    """Apply an avar segment map.

    Exact key hits return the mapped value; outside the declared range the
    outer segment is extrapolated (identity slope), matching the OpenType
    avar spec and fontTools' piecewiseLinearMap.
    """
    if not mapping:
        return value
    keys = [k for k, _ in mapping]
    d = dict(mapping)
    if value in d:
        return d[value]
    lo = keys[0]
    hi = keys[-1]
    if value < lo:
        return value + d[lo] - lo
    if value > hi:
        return value + d[hi] - hi
    for i in range(len(mapping) - 1):
        a, va = mapping[i]
        b, vb = mapping[i + 1]
        if a < value < b:
            return va + (vb - va) * (value - a) / (b - a)
    return value  # pragma: no cover


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_value(value: float, triple: tuple[float, float, float]) -> float:
    """Map a user-space axis value to normalized [-1, 1] space.

    Values outside [min, max] are clamped.  The default maps to 0, min to -1
    and max to +1; each side is linear.
    """
    lower, default, upper = triple
    if not (lower <= default <= upper):
        raise ValueError(f"invalid axis triple {triple}")
    value = max(min(value, upper), lower)
    if value == default or lower == upper:
        return 0.0
    if value < default and lower != default:
        return (value - default) / (default - lower)
    # value > default (the == default case returned 0 above); the degenerate
    # half-range axis (upper == default) also falls into the lower-ratio arm.
    if upper != default:
        return (value - default) / (upper - default)
    return (value - default) / (default - lower)


def normalize_location(
    user_location: dict[str, float],
    axes: list[Axis],
    avar_segments: dict[str, list[tuple[float, float]]] | None = None,
    quantize: bool = True,
) -> dict[str, float]:
    """Full fvar normalization: linear normalize, then avar, then F2Dot14.

    The quantization step is what real shaping engines apply to requested
    coordinates before evaluating gvar scalars.
    """
    avar_segments = avar_segments or {}
    result: dict[str, float] = {}
    for axis in axes:
        if axis.tag in user_location:
            v = normalize_value(float(user_location[axis.tag]), axis.triple)
        else:
            v = 0.0
        mapping = avar_segments.get(axis.tag)
        if mapping:
            v = piecewise_linear_map(v, mapping)
        if quantize:
            v = f2dot14_quantize(v)
        result[axis.tag] = v
    return result


def support_scalar(normalized: dict[str, float], support: dict[str, tuple]) -> float:
    """Evaluate a variation-region "tent" at a normalized location.

    ``support`` maps axis tag to ``(start, peak, end)`` in F2Dot14-normalized
    coordinates.  Axes absent from the support do not participate (factor 1).
    Malformed tents (peak outside [start, end], or straddling zero) have zero
    influence per the OpenType spec.
    """
    scalar = 1.0
    for axis, (start, peak, end) in support.items():
        if peak == 0.0:
            continue
        if start > peak or peak > end:
            return 0.0
        if start < 0.0 < end:
            return 0.0
        v = normalized.get(axis, 0.0)
        if v == peak:
            continue
        if v <= start or end <= v:
            return 0.0
        if v < peak:
            scalar *= (v - start) / (peak - start)
        else:
            scalar *= (v - end) / (peak - end)
    return scalar
