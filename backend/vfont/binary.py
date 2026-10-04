"""Minimal big-endian binary reader used by the hand-written table parsers.

Nothing here knows anything about fonts; it just tracks an offset into a
``bytes`` buffer and unpacks struct-format values.
"""
from __future__ import annotations

import struct


class VariationEngineError(ValueError):
    """Raised for any font that the limited engine cannot process."""


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def read(self, fmt: str):
        size = struct.calcsize(fmt)
        (value,) = struct.unpack_from(">" + fmt, self.data, self.pos)
        self.pos += size
        return value

    def read_struct(self, fmt: str):
        size = struct.calcsize(fmt)
        values = struct.unpack_from(">" + fmt, self.data, self.pos)
        self.pos += size
        return values

    def bytes(self, length: int) -> bytes:
        out = self.data[self.pos : self.pos + length]
        self.pos += length
        return out

    def seek(self, pos: int) -> None:
        self.pos = pos


def read_sfnt_tables(blob: bytes) -> dict[str, bytes]:
    """Parse an SFNT (TTF) table directory and return ``{tag: raw_bytes}``.

    Only uncompressed ``sfntVersion`` 0x00010000 / 'true' / 'typ1' fonts are
    supported.  WOFF/WOFF2 containers are explicitly rejected.
    """
    r = Reader(blob)
    sfnt_version = r.read("I")
    if sfnt_version in (0x74727565, 0x74797031):  # 'true', 'typ1'
        pass
    elif sfnt_version == 0x00010000:
        pass
    elif sfnt_version in (0x774F4646, 0x774F4632):  # 'wOFF', 'wOF2'
        raise ValueError("WOFF/WOFF2 containers are not supported; pass a raw TTF")
    else:
        raise ValueError(f"unsupported sfntVersion 0x{sfnt_version:08X}")
    num_tables = r.read("H")
    r.pos += 6  # searchRange, entrySelector, rangeShift

    tables: dict[str, bytes] = {}
    for _ in range(num_tables):
        tag = r.bytes(4).decode("latin-1")
        _checksum, offset, length = r.read_struct("III")
        tables[tag] = blob[offset : offset + length]
    return tables
