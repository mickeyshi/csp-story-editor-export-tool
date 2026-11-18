"""Text layer string/attribute encoding.

TextLayerAttributes is a stream of little-endian TLV records: u32 id, u32 len, data.
Some records describe per-character style runs and must be updated when the
text length changes. Their data is:
    u32 run_count, then per run: u32 start, u32 length, ...run payload...
Offsets/lengths are in UTF-16 code units of the CRLF-normalised string.

Records that hold layout (bounding box 0x2a, position 0x3f, quad 0x40) are left
untouched; Clip Studio recomputes layout from the string and style.
"""
from __future__ import annotations

import difflib
import struct

# TLV ids whose data starts with a style-run table.
RANGE_IDS = {0x0B, 0x0C, 0x0D, 0x12, 0x1A, 0x1D}
# Editing caret: u32 position, u32 selection length. Must stay within the text.
CARET_ID = 0x27


class UnsupportedText(Exception):
    """Raised when a text object's attributes can't be safely rewritten."""


# --- string helpers -------------------------------------------------------

def decode_text(raw: bytes) -> str:
    """CSP stores UTF-8 with CRLF line breaks; return LF-normalised text."""
    return raw.decode("utf-8").replace("\r\n", "\n")


def encode_text(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def u16len(text: str) -> int:
    """Length in UTF-16 code units of the CRLF form (what CSP's runs count)."""
    return len(text.replace("\n", "\r\n").encode("utf-16-le")) // 2


def split_lp(blob: bytes | None) -> list[bytes]:
    """Split a sequence of u32-length-prefixed items."""
    out, off = [], 0
    if not blob:
        return out
    while off < len(blob):
        n, = struct.unpack_from("<I", blob, off)
        out.append(blob[off + 4:off + 4 + n])
        off += 4 + n
    if off != len(blob):
        raise ValueError("bad length-prefixed array")
    return out


def join_lp(items: list[bytes]) -> bytes:
    return b"".join(struct.pack("<I", len(i)) + i for i in items)


# --- TLV ------------------------------------------------------------------

def parse_tlv(blob: bytes) -> list[tuple[int, bytes]]:
    out, off = [], 0
    while off < len(blob):
        if off + 8 > len(blob):
            raise ValueError("truncated TLV header")
        i, n = struct.unpack_from("<II", blob, off)
        if off + 8 + n > len(blob):
            raise ValueError("TLV record overruns blob")
        out.append((i, blob[off + 8:off + 8 + n]))
        off += 8 + n
    return out


def build_tlv(items: list[tuple[int, bytes]]) -> bytes:
    return b"".join(struct.pack("<II", i, len(d)) + d for i, d in items)


# --- position remapping ---------------------------------------------------

def _units(text: str) -> list[str]:
    """Split into UTF-16-unit-sized tokens so diff positions match run offsets."""
    out = []
    for ch in text.replace("\n", "\r\n"):
        out.extend([ch] * (len(ch.encode("utf-16-le")) // 2))
    return out


def make_remap(old: str, new: str):
    ops = difflib.SequenceMatcher(None, _units(old), _units(new), autojunk=False).get_opcodes()

    def remap(pos: int) -> int:
        for tag, i1, i2, j1, j2 in ops:
            if i1 <= pos < i2 or (pos == i2 and tag != "equal"):
                if tag == "equal":
                    return j1 + (pos - i1)
                return j2 if pos == i2 else j1 + min(pos - i1, j2 - j1)
        return ops[-1][4] if ops else 0

    return remap


def _parse_runs(data: bytes):
    """Generic run layout: start, length, payload_size, payload. Returns None if it doesn't fit."""
    count, = struct.unpack_from("<I", data, 0)
    runs, off = [], 4
    for _ in range(count):
        if off + 12 > len(data):
            return None
        s, n, size = struct.unpack_from("<III", data, off)
        end = off + 12 + size
        if end > len(data):
            return None
        runs.append((s, n, data[off + 8:end]))  # keep size+payload verbatim
        off = end
    return runs if off == len(data) else None


def _patch_range_record(rid: int, data: bytes, old_len: int, new_len: int, remap) -> bytes:
    if not data:
        return data
    count, = struct.unpack_from("<I", data, 0)
    if count == 0:
        return data
    if count == 1:
        s, n = struct.unpack_from("<II", data, 4)
        if s != 0 or n != old_len:
            raise UnsupportedText(f"attr {rid:#x}: single run {s}+{n} doesn't span text of length {old_len}")
        return data[:8] + struct.pack("<I", new_len) + data[12:]
    runs = _parse_runs(data)
    if runs is None:
        raise UnsupportedText(f"attr {rid:#x}: mixed styling ({count} runs) in an unknown layout")
    new_runs = []
    for idx, (s, n, rest) in enumerate(runs):
        ns = remap(s)
        ne = new_len if idx == len(runs) - 1 and s + n == old_len else remap(s + n)
        if ne > ns:
            new_runs.append((ns, ne - ns, rest))
    if not new_runs:
        raise UnsupportedText(f"attr {rid:#x}: all style runs vanished")
    return struct.pack("<I", len(new_runs)) + b"".join(
        struct.pack("<II", s, n) + rest for s, n, rest in new_runs)


def rewrite_attributes(blob: bytes, old_text: str, new_text: str) -> bytes:
    old_len, new_len = u16len(old_text), u16len(new_text)
    remap = make_remap(old_text, new_text)
    items = []
    for rid, data in parse_tlv(blob):
        if rid in RANGE_IDS:
            data = _patch_range_record(rid, data, old_len, new_len, remap)
        elif rid == CARET_ID and len(data) == 8:
            pos, sel = struct.unpack("<II", data)
            pos = min(pos, new_len)
            data = struct.pack("<II", pos, sel if pos + sel <= new_len else 0)
        items.append((rid, data))
    out = build_tlv(items)
    assert parse_tlv(out)  # sanity
    return out
