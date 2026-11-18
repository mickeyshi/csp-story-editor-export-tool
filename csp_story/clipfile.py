"""Read/write the CSFCHUNK container used by Clip Studio Paint .clip files.

Layout (all integers big-endian u64):
    "CSFCHUNK" | total_file_size | header_offset(=24)
    then chunks: 8-byte tag ("CHNKHead", "CHNKExta", "CHNKSQLi", "CHNKFoot") | u64 length | data

The SQLite database holding layers (including text) is the CHNKSQLi chunk. It is
followed only by CHNKFoot, so it can be replaced with a DB of a different size
as long as the total size in the file header is updated.
"""
from __future__ import annotations

import os
import sqlite3
import struct
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

MAGIC = b"CSFCHUNK"


@dataclass
class Chunk:
    tag: bytes
    data_offset: int
    length: int


def parse_chunks(data: bytes) -> list[Chunk]:
    if data[:8] != MAGIC:
        raise ValueError("not a Clip Studio .clip file (missing CSFCHUNK header)")
    total, = struct.unpack_from(">Q", data, 8)
    if total != len(data):
        raise ValueError(f"file size mismatch: header says {total}, file is {len(data)}")
    off = 24
    chunks = []
    while off < len(data):
        tag = data[off:off + 8]
        length, = struct.unpack_from(">Q", data, off + 8)
        chunks.append(Chunk(tag, off + 16, length))
        off += 16 + length
    if off != len(data):
        raise ValueError("chunk table overruns file")
    return chunks


class ClipFile:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.chunks = parse_chunks(self.data)
        sql = [c for c in self.chunks if c.tag == b"CHNKSQLi"]
        if len(sql) != 1:
            raise ValueError("expected exactly one CHNKSQLi chunk")
        self.sql_chunk = sql[0]
        tail = self.chunks[self.chunks.index(self.sql_chunk) + 1:]
        if [c.tag for c in tail] != [b"CHNKFoot"]:
            raise ValueError("unexpected chunk order after CHNKSQLi; refusing to rewrite")

    @property
    def sqlite_bytes(self) -> bytes:
        c = self.sql_chunk
        return self.data[c.data_offset:c.data_offset + c.length]

    @contextmanager
    def database(self, writable: bool = False):
        """Yield a sqlite3 connection to a temp copy of the embedded DB.

        If writable, changes are committed and folded back into self.data on exit.
        """
        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self.sqlite_bytes)
            con = sqlite3.connect(tmp)
            try:
                yield con
                if writable:
                    con.commit()
            finally:
                con.close()
            if writable:
                with open(tmp, "rb") as f:
                    self._replace_sqlite(f.read())
        finally:
            for p in (tmp, tmp + "-journal"):
                if os.path.exists(p):
                    os.remove(p)

    def _replace_sqlite(self, db: bytes) -> None:
        c = self.sql_chunk
        head = bytearray(self.data[:c.data_offset - 8])  # up to the length field
        head += struct.pack(">Q", len(db))
        rest = self.data[c.data_offset + c.length:]       # CHNKFoot
        new = head + db + rest
        struct.pack_into(">Q", new, 8, len(new))
        self.data = bytes(new)
        self.chunks = parse_chunks(self.data)
        self.sql_chunk = next(ch for ch in self.chunks if ch.tag == b"CHNKSQLi")

    def save(self, path: str | None = None) -> None:
        path = path or self.path
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.data)
        os.replace(tmp, path)
