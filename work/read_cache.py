"""Read the installed cache, without modifying it, to verify varbit definitions.

Format reference: skillbert/rsmv a92b892, src/opcodes/cacheindex.json and
src/cache/index.ts (SQLite archive layout). This is an independent small decoder.
"""
import sqlite3
from pathlib import Path
import struct
import zlib

def decompress(blob):
    if not blob.startswith(b"ZLB\x01"):
        raise ValueError("Unsupported cache compression")
    expected, = struct.unpack_from(">I", blob, 4)
    raw = zlib.decompress(blob[8:])
    if len(raw) != expected:
        raise ValueError("Invalid decompressed length")
    return raw

class Buffer:
    def __init__(self, data):
        self.data, self.pos = data, 0
    def read(self, fmt):
        value, = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += struct.calcsize(fmt)
        return value
    def varuint(self):
        if self.data[self.pos] & 0x80:
            return self.read(">I") & 0x7fffffff
        return self.read(">H")

def index(data):
    reader = Buffer(data)
    version = reader.read(">B")
    if version not in (6, 7):
        raise ValueError("Unsupported reference-table version")
    reader.read(">I")
    flags = reader.read(">B")
    count = reader.varuint() if version >= 7 else reader.read(">H")
    ids, value = [], 0
    for _ in range(count):
        value += reader.read(">H")
        ids.append(value)
    reader.pos += count * (4 + (4 if flags & 1 else 0))
    if flags & 4:
        reader.pos += count * 12
    if flags & 2:
        reader.pos += count * 64
    reader.pos += count * 4
    counts = [reader.varuint() for _ in ids]
    groups = {}
    for group, nfiles in zip(ids, counts):
        subids, value = [], 0
        for _ in range(nfiles):
            value += reader.read(">H")
            subids.append(value)
        groups[group] = subids
    if flags & 1:
        reader.pos += sum(counts) * 4
    if reader.pos != len(data):
        raise ValueError("Reference table did not decode completely: %s / %s" % (reader.pos, len(data)))
    return groups

class Cache:
    def __init__(self, archive=2, root=r"C:\ProgramData\Jagex\RuneScape"):
        path = Path(root) / ("js5-%d.jcache" % archive)
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        self.groups = index(decompress(self.db.execute("SELECT DATA FROM cache_index").fetchone()[0]))
    def group(self, group):
        row = self.db.execute("SELECT DATA FROM cache WHERE KEY=?", (group,)).fetchone()
        if row is None:
            raise KeyError(group)
        data = decompress(row[0])
        ids = self.groups[group]
        if len(ids) == 1:
            return {ids[0]: data}
        start, = struct.unpack_from(">I", data, 1)
        if start != 1 + 4 * (len(ids) + 1):
            raise ValueError("Invalid SQLite archive table")
        result = {}
        for i, fileid in enumerate(ids):
            end, = struct.unpack_from(">I", data, 5 + i * 4)
            if not start <= end <= len(data):
                raise ValueError("Invalid file extent")
            result[fileid] = data[start:end]
            start = end
        if start != len(data):
            raise ValueError("Trailing group data")
        return result

if __name__ == "__main__":
    cache = Cache()
    print("groups", {k: len(v) for k, v in cache.groups.items()})
    bits = cache.group(69)
    for fileid in [1668, 16736, 24595, 32640]:
        print("varbit", fileid, bits.get(fileid, b"").hex())
