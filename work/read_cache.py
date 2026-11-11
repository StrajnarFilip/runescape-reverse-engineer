"""Read the installed cache, without modifying it, to inspect varbit definitions.

Format reference: skillbert/rsmv a92b892, src/opcodes/cacheindex.json and
src/cache/index.ts (SQLite archive layout). This is an independent small
decoder; the maintained decoder is rs3_reader.cache_definitions.
"""

import sqlite3
import struct
import zlib
from pathlib import Path

DEFAULT_CACHE_ROOT = r"C:\ProgramData\Jagex\RuneScape"
VARBIT_ARCHIVE = 2
VARBIT_GROUP = 69
INSPECTED_VARBITS = (1668, 16736, 24595, 32640)
REFERENCE_HAS_NAMES = 1
REFERENCE_HAS_WHIRLPOOL = 2
REFERENCE_HAS_SIZES = 4


def decompress(blob):
    if not blob.startswith(b"ZLB\x01"):
        raise ValueError("Unsupported cache compression")
    (expected_length,) = struct.unpack_from(">I", blob, 4)
    data = zlib.decompress(blob[8:])
    if len(data) != expected_length:
        raise ValueError("Invalid decompressed length")
    return data


class ByteReader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def read(self, layout):
        (value,) = struct.unpack_from(layout, self.data, self.position)
        self.position += struct.calcsize(layout)
        return value

    def read_smart(self):
        if self.data[self.position] & 0x80:
            return self.read(">I") & 0x7FFFFFFF
        return self.read(">H")


def read_delta_encoded_ids(reader, count):
    identifiers, current = [], 0
    for _ in range(count):
        current += reader.read(">H")
        identifiers.append(current)
    return identifiers


def parse_reference_table(data):
    """Return {group ID: [file IDs]} from a JS5 reference table."""
    reader = ByteReader(data)
    version = reader.read(">B")
    if version not in (6, 7):
        raise ValueError("Unsupported reference-table version")
    reader.read(">I")  # Revision.
    flags = reader.read(">B")
    group_count = reader.read_smart() if version >= 7 else reader.read(">H")
    group_ids = read_delta_encoded_ids(reader, group_count)
    reader.position += group_count * (4 + (4 if flags & REFERENCE_HAS_NAMES else 0))
    if flags & REFERENCE_HAS_SIZES:
        reader.position += group_count * 12
    if flags & REFERENCE_HAS_WHIRLPOOL:
        reader.position += group_count * 64
    reader.position += group_count * 4
    file_counts = [reader.read_smart() for _ in group_ids]
    group_files = {
        group_id: read_delta_encoded_ids(reader, file_count)
        for group_id, file_count in zip(group_ids, file_counts)
    }
    if flags & REFERENCE_HAS_NAMES:
        reader.position += sum(file_counts) * 4
    if reader.position != len(data):
        raise ValueError(
            f"Reference table did not decode completely: {reader.position} / {len(data)}"
        )
    return group_files


class Cache:
    """One js5-N.jcache SQLite archive, opened read-only."""

    def __init__(self, archive_number=VARBIT_ARCHIVE, cache_root=DEFAULT_CACHE_ROOT):
        path = Path(cache_root) / f"js5-{archive_number}.jcache"
        self.database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        reference_blob = self.database.execute("SELECT DATA FROM cache_index").fetchone()[0]
        self.group_files = parse_reference_table(decompress(reference_blob))

    def read_group(self, group_id):
        row = self.database.execute("SELECT DATA FROM cache WHERE KEY=?", (group_id,)).fetchone()
        if row is None:
            raise KeyError(group_id)
        data = decompress(row[0])
        file_ids = self.group_files[group_id]
        if len(file_ids) == 1:
            return {file_ids[0]: data}
        (start,) = struct.unpack_from(">I", data, 1)
        if start != 1 + 4 * (len(file_ids) + 1):
            raise ValueError("Invalid SQLite archive table")
        files = {}
        for index, file_id in enumerate(file_ids):
            (end,) = struct.unpack_from(">I", data, 5 + index * 4)
            if not start <= end <= len(data):
                raise ValueError("Invalid file extent")
            files[file_id] = data[start:end]
            start = end
        if start != len(data):
            raise ValueError("Trailing group data")
        return files


def main():
    cache = Cache()
    print("groups", {group_id: len(file_ids) for group_id, file_ids in cache.group_files.items()})
    varbits = cache.read_group(VARBIT_GROUP)
    for file_id in INSPECTED_VARBITS:
        print("varbit", file_id, varbits.get(file_id, b"").hex())


if __name__ == "__main__":
    main()
