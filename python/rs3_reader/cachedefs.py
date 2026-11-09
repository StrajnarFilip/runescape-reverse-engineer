"""Independent, bounded read-only decoder for the installed RS3 JS5 cache.

Only definition data is read. No game code, third-party agent or network access.
Unknown opcodes fail explicitly instead of guessing a name or requirement.
Format leads: skillbert/rsmv a92b8920 (items/structs/enums/cacheindex schemas).
"""
from functools import lru_cache
from pathlib import Path
import sqlite3
import struct
import zlib


class DecodeError(ValueError):
    pass


class Buffer:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n<0 or self.pos+n>len(self.data):
            raise DecodeError("Truncated definition at %d: need %d bytes of %d" % (self.pos,n,len(self.data)))
        value = self.data[self.pos:self.pos+n]
        self.pos += n
        return value

    def uint(self, n=1):
        return int.from_bytes(self.take(n), "big")

    def sint(self, n=4):
        return int.from_bytes(self.take(n), "big", signed=True)

    def string(self):
        end = self.data.find(b"\0", self.pos)
        if end<0 or end-self.pos>65535:
            raise DecodeError("Invalid definition string")
        value = self.take(end-self.pos).decode("cp1252", errors="replace")
        self.take(1)
        return value

    def smart(self):
        if self.pos>=len(self.data):
            raise DecodeError("Truncated smart integer")
        # The high bit marks the four-byte form and is cleared from its value.
        return self.uint(4)&0x7fffffff if self.data[self.pos]&128 else self.uint(2)

    def params(self):
        result = {}
        for _ in range(self.uint()):
            kind, key = self.uint(), self.uint(3)
            if kind not in (0, 1):
                raise DecodeError("Invalid parameter kind")
            result[key] = self.string() if kind else self.sint()
        return result

    def finish(self):
        if self.pos!=len(self.data):
            raise DecodeError("Trailing definition bytes")


def decompress(blob):
    if len(blob)<8 or blob[:4]!=b"ZLB\1":
        raise DecodeError("Unsupported JS5 cache compression")
    size = int.from_bytes(blob[4:8], "big")
    if size>32*1024*1024:
        raise DecodeError("Cache group exceeds bound")
    decoder = zlib.decompressobj()
    # Allow one extra byte so an oversized stream fails the exact-length check.
    value = decoder.decompress(blob[8:], size+1)
    if len(value)!=size or not decoder.eof or decoder.unused_data:
        raise DecodeError("Invalid decompressed group length")
    return value


def reference_table(data):
    b = Buffer(data)
    version = b.uint()
    if version not in (6, 7):
        raise DecodeError("Unsupported reference table")
    b.uint(4)
    flags = b.uint()
    count = b.smart() if version>=7 else b.uint(2)
    if count>65536:
        raise DecodeError("Reference table exceeds bound")
    # Group and file IDs are stored as deltas from their previous IDs.
    ids, current = [], 0
    for _ in range(count):
        current += b.uint(2)
        ids.append(current)
    # Skip the per-group metadata fields to reach the file counts and IDs.
    b.take(count*(4+(4 if flags&1 else 0)))
    if flags&4:
        b.take(count*12)
    if flags&2:
        b.take(count*64)
    b.take(count*4)
    counts = [b.smart() for _ in ids]
    groups = {}
    for group, n in zip(ids, counts):
        subids, current = [], 0
        if n>65536:
            raise DecodeError("Too many definition files")
        for _ in range(n):
            current += b.uint(2)
            subids.append(current)
        groups[group] = subids
    if flags&1:
        b.take(sum(counts)*4)
    b.finish()
    return groups


class Archive:
    def __init__(self, number, root=r"C:\ProgramData\Jagex\RuneScape"):
        path = Path(root).resolve()/ ("js5-%d.jcache" % number)
        self.db = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)
        row = self.db.execute("SELECT DATA FROM cache_index").fetchone()
        if row is None:
            raise DecodeError("Missing cache reference table")
        self.index_bytes = decompress(row[0])
        self.groups = reference_table(self.index_bytes)

    @lru_cache(maxsize=32)
    def group(self, group):
        row = self.db.execute("SELECT DATA FROM cache WHERE KEY=?", (group,)).fetchone()
        if row is None:
            raise KeyError(group)
        data = decompress(row[0])
        ids = self.groups[group]
        if len(ids)==1:
            return {ids[0]: data}
        b = Buffer(data)
        if b.uint()!=1:
            raise DecodeError("Unsupported multi-file cache group")
        # The offset table starts with the payload start, then one end per file.
        start = b.uint(4)
        if start!=1+4*(len(ids)+1):
            raise DecodeError("Invalid group table")
        result = {}
        for fileid in ids:
            end = b.uint(4)
            if not start<=end<=len(data):
                raise DecodeError("Invalid group extent")
            result[fileid] = data[start:end]
            start = end
        if start!=len(data):
            raise DecodeError("Trailing group bytes")
        return result

    def file(self, identifier, shift):
        return self.group(identifier>>shift)[identifier&((1<<shift)-1)]

    def close(self):
        self.group.cache_clear()
        self.db.close()


def decode_struct(data):
    b, params = Buffer(data), {}
    while True:
        op = b.uint()
        if op==0:
            break
        if op!=249:
            raise DecodeError("Unknown struct opcode %d" % op)
        params.update(b.params())
    b.finish()
    return params


def decode_enum(data):
    b, result = Buffer(data), {"values": {}}
    while True:
        op = b.uint()
        if not op:
            break
        if op in (1, 2, 101, 102):
            result["key_type" if op in (1, 101) else "value_type"] = b.uint()
        elif op==3:
            result["default"] = b.string()
        elif op==4:
            result["default"] = b.sint()
        elif op in (5, 6, 7, 8):
            if op in (7, 8):
                b.uint(2)  # Declared array capacity, not entry count.
            for _ in range(b.uint(2)):
                key = b.uint(2) if op in (7, 8) else b.sint()
                result["values"][key] = b.string() if op in (5, 7) else b.sint()
        elif op not in (131, 209):
            raise DecodeError("Unknown enum opcode %d" % op)
    b.finish()
    return result


def decode_item(data):
    b = Buffer(data)
    result = {"name": None, "params": {}, "equip_slot": -1, "hide_slot": -1}
    # Group unused opcodes by payload size so the cursor stays aligned.
    models = {1,23,24,25,26,78,79,90,91,92,93}
    shorts = {4,5,6,7,8,10,18,44,45,94,95,97,98,110,111,112,121,122,139,140,161,162,163}
    bytes_ = {13,14,27,96,113,114,115,134}
    flags = {11,15,16,65,156,157,165,167,168,178}
    while True:
        op = b.uint()
        if not op:
            break
        if op in (2,3,164) or 30<=op<=39:
            value = b.string()
            if op==2:
                result["name"] = value
        elif op in models:
            b.smart()
        elif op in shorts:
            b.uint(2)
        elif op in bytes_:
            value = b.uint()
            if op==13:
                result["equip_slot"] = value
            if op==14:
                result["hide_slot"] = value
        elif op in flags:
            pass
        elif op in (12,43,69):
            b.uint(4)
        elif op==9:
            for _ in range(b.uint()):
                b.smart()
        elif op in (40,41):
            b.take(b.uint()*4)
        elif op==42:
            b.take(b.uint()*2)
        elif 100<=op<=109:
            b.take(4)
        elif op in (125,126,127,128,129,130):
            b.take(3)
        elif op==132:
            b.take(b.uint()*2)  # Revision 951: quest IDs are uint16 entries.
        elif 142<=op<=146 or 150<=op<=154:
            b.take(2)
        elif op==181:
            b.take(8)
        elif op==182 or 201<=op<=208:
            value = b.uint(3)
            if op==201:
                result["note_id"] = value
            if op==202:
                result["note_template"] = value
        elif 190<=op<=199:
            b.take(5)
        elif op==249:
            result["params"].update(b.params())
        else:
            raise DecodeError("Unknown item opcode %d at byte %d" % (op,b.pos-1))
    b.finish()
    return result
