"""Independent, bounded, read-only decoder for the installed RS3 JS5 cache.

Only definition data is read; no game code, third-party agent or network access
is involved. Unknown opcodes fail explicitly instead of guessing a name or a
requirement. Format leads: skillbert/rsmv a92b8920 (items, structs, enums and
cacheindex schemas).
"""

from __future__ import annotations

import sqlite3
import zlib
from functools import lru_cache
from pathlib import Path

DEFAULT_CACHE_ROOT = r"C:\ProgramData\Jagex\RuneScape"
COMPRESSION_MAGIC = b"ZLB\x01"
COMPRESSION_HEADER_BYTES = 8
MAX_GROUP_BYTES = 32 * 1024 * 1024
MAX_STRING_BYTES = 65535
MAX_REFERENCE_ENTRIES = 65536
SUPPORTED_REFERENCE_VERSIONS = (6, 7)
MULTI_FILE_GROUP_FORMAT = 1

# Reference table flags.
REFERENCE_HAS_NAMES = 1
REFERENCE_HAS_WHIRLPOOL = 2
REFERENCE_HAS_SIZES = 4

# Item opcodes grouped by payload so the cursor stays aligned on unused fields.
ITEM_NAME_OPCODE = 2
ITEM_EQUIP_SLOT_OPCODE = 13
ITEM_HIDE_SLOT_OPCODE = 14
ITEM_NOTE_ID_OPCODE = 201
ITEM_NOTE_TEMPLATE_OPCODE = 202
PARAMETERS_OPCODE = 249
ITEM_STRING_OPCODES = frozenset({2, 3, 164, *range(30, 40)})
ITEM_SMART_OPCODES = frozenset({1, 23, 24, 25, 26, 78, 79, 90, 91, 92, 93})
ITEM_SHORT_OPCODES = frozenset(
    {4, 5, 6, 7, 8, 10, 18, 44, 45, 94, 95, 97, 98}
    | {110, 111, 112, 121, 122, 139, 140, 161, 162, 163}
)
ITEM_BYTE_OPCODES = frozenset({13, 14, 27, 96, 113, 114, 115, 134})
ITEM_FLAG_OPCODES = frozenset({11, 15, 16, 65, 156, 157, 165, 167, 168, 178})
ITEM_INT_OPCODES = frozenset({12, 43, 69})
ITEM_THREE_BYTE_OPCODES = frozenset({125, 126, 127, 128, 129, 130})

# Enum opcodes.
ENUM_KEY_TYPE_OPCODES = (1, 101)
ENUM_VALUE_TYPE_OPCODES = (2, 102)
ENUM_STRING_DEFAULT_OPCODE = 3
ENUM_INT_DEFAULT_OPCODE = 4
ENUM_STRING_VALUES_OPCODES = (5, 7)
ENUM_INT_VALUES_OPCODES = (6, 8)
ENUM_SPARSE_VALUE_OPCODES = (7, 8)
ENUM_IGNORED_OPCODES = (131, 209)


class DecodeError(ValueError):
    """A definition or cache structure does not match the expected format."""


class ByteReader:
    """Big-endian reader over a definition buffer with bounds checks."""

    def __init__(self, data):
        self.data = data
        self.position = 0

    def read_bytes(self, count):
        if count < 0 or self.position + count > len(self.data):
            raise DecodeError(
                f"Truncated definition at {self.position}: need {count} bytes of {len(self.data)}"
            )
        value = self.data[self.position : self.position + count]
        self.position += count
        return value

    def read_unsigned(self, byte_count=1):
        return int.from_bytes(self.read_bytes(byte_count), "big")

    def read_signed(self, byte_count=4):
        return int.from_bytes(self.read_bytes(byte_count), "big", signed=True)

    def read_string(self):
        """Read a NUL-terminated cp1252 string."""
        end = self.data.find(b"\0", self.position)
        if end < 0 or end - self.position > MAX_STRING_BYTES:
            raise DecodeError("Invalid definition string")
        value = self.read_bytes(end - self.position).decode("cp1252", errors="replace")
        self.read_bytes(1)
        return value

    def read_smart(self):
        """Read a two-byte value, or a four-byte value when the high bit is set."""
        if self.position >= len(self.data):
            raise DecodeError("Truncated smart integer")
        # The high bit marks the four-byte form and is cleared from its value.
        if self.data[self.position] & 0x80:
            return self.read_unsigned(4) & 0x7FFFFFFF
        return self.read_unsigned(2)

    def read_parameters(self):
        """Read a parameter map of integer or string values keyed by a 24-bit ID."""
        parameters = {}
        for _ in range(self.read_unsigned()):
            value_is_string, key = self.read_unsigned(), self.read_unsigned(3)
            if value_is_string not in (0, 1):
                raise DecodeError("Invalid parameter kind")
            parameters[key] = self.read_string() if value_is_string else self.read_signed()
        return parameters

    def require_end(self):
        if self.position != len(self.data):
            raise DecodeError("Trailing definition bytes")


def decompress(blob):
    """Inflate a "ZLB" cache blob and verify its declared length."""
    if len(blob) < COMPRESSION_HEADER_BYTES or blob[:4] != COMPRESSION_MAGIC:
        raise DecodeError("Unsupported JS5 cache compression")
    size = int.from_bytes(blob[4:COMPRESSION_HEADER_BYTES], "big")
    if size > MAX_GROUP_BYTES:
        raise DecodeError("Cache group exceeds bound")
    decompressor = zlib.decompressobj()
    # Allow one extra byte so an oversized stream fails the exact-length check.
    value = decompressor.decompress(blob[COMPRESSION_HEADER_BYTES:], size + 1)
    if len(value) != size or not decompressor.eof or decompressor.unused_data:
        raise DecodeError("Invalid decompressed group length")
    return value


def _read_delta_encoded_ids(reader, count):
    """Read IDs stored as deltas from their previous ID."""
    identifiers, current = [], 0
    for _ in range(count):
        current += reader.read_unsigned(2)
        identifiers.append(current)
    return identifiers


def parse_reference_table(data):
    """Return {group ID: [file IDs]} from a JS5 reference table."""
    reader = ByteReader(data)
    version = reader.read_unsigned()
    if version not in SUPPORTED_REFERENCE_VERSIONS:
        raise DecodeError("Unsupported reference table")
    reader.read_unsigned(4)  # Revision.
    flags = reader.read_unsigned()
    group_count = reader.read_smart() if version >= 7 else reader.read_unsigned(2)
    if group_count > MAX_REFERENCE_ENTRIES:
        raise DecodeError("Reference table exceeds bound")
    group_ids = _read_delta_encoded_ids(reader, group_count)

    # Skip the per-group metadata to reach the file counts and IDs.
    reader.read_bytes(group_count * (4 + (4 if flags & REFERENCE_HAS_NAMES else 0)))
    if flags & REFERENCE_HAS_SIZES:
        reader.read_bytes(group_count * 12)
    if flags & REFERENCE_HAS_WHIRLPOOL:
        reader.read_bytes(group_count * 64)
    reader.read_bytes(group_count * 4)  # Group versions.
    file_counts = [reader.read_smart() for _ in group_ids]

    group_files = {}
    for group_id, file_count in zip(group_ids, file_counts):
        if file_count > MAX_REFERENCE_ENTRIES:
            raise DecodeError("Too many definition files")
        group_files[group_id] = _read_delta_encoded_ids(reader, file_count)
    if flags & REFERENCE_HAS_NAMES:
        reader.read_bytes(sum(file_counts) * 4)
    reader.require_end()
    return group_files


class Archive:
    """One js5-N.jcache SQLite archive, opened read-only."""

    def __init__(self, archive_number, cache_root=DEFAULT_CACHE_ROOT):
        path = Path(cache_root).resolve() / f"js5-{archive_number}.jcache"
        self.database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        row = self.database.execute("SELECT DATA FROM cache_index").fetchone()
        if row is None:
            raise DecodeError("Missing cache reference table")
        self.reference_table_bytes = decompress(row[0])
        self.group_files = parse_reference_table(self.reference_table_bytes)

    @lru_cache(maxsize=32)
    def read_group(self, group_id):
        """Return {file ID: bytes} of one group."""
        row = self.database.execute("SELECT DATA FROM cache WHERE KEY=?", (group_id,)).fetchone()
        if row is None:
            raise KeyError(group_id)
        data = decompress(row[0])
        file_ids = self.group_files[group_id]
        if len(file_ids) == 1:
            return {file_ids[0]: data}

        reader = ByteReader(data)
        if reader.read_unsigned() != MULTI_FILE_GROUP_FORMAT:
            raise DecodeError("Unsupported multi-file cache group")
        # The offset table starts with the payload start, then one end per file.
        start = reader.read_unsigned(4)
        if start != 1 + 4 * (len(file_ids) + 1):
            raise DecodeError("Invalid group table")
        files = {}
        for file_id in file_ids:
            end = reader.read_unsigned(4)
            if not start <= end <= len(data):
                raise DecodeError("Invalid group extent")
            files[file_id] = data[start:end]
            start = end
        if start != len(data):
            raise DecodeError("Trailing group bytes")
        return files

    def read_file(self, definition_id, group_shift):
        """Return the file of a definition whose group is definition_id >> group_shift."""
        file_mask = (1 << group_shift) - 1
        return self.read_group(definition_id >> group_shift)[definition_id & file_mask]

    def close(self):
        self.read_group.cache_clear()
        self.database.close()


def decode_struct(data):
    """Return the parameter map of a struct definition."""
    reader, parameters = ByteReader(data), {}
    while True:
        opcode = reader.read_unsigned()
        if opcode == 0:
            break
        if opcode != PARAMETERS_OPCODE:
            raise DecodeError(f"Unknown struct opcode {opcode}")
        parameters.update(reader.read_parameters())
    reader.require_end()
    return parameters


def decode_enum(data):
    """Return {"key_type", "value_type", "default", "values"} of an enum definition."""
    reader, result = ByteReader(data), {"values": {}}
    while True:
        opcode = reader.read_unsigned()
        if opcode == 0:
            break
        if opcode in ENUM_KEY_TYPE_OPCODES:
            result["key_type"] = reader.read_unsigned()
        elif opcode in ENUM_VALUE_TYPE_OPCODES:
            result["value_type"] = reader.read_unsigned()
        elif opcode == ENUM_STRING_DEFAULT_OPCODE:
            result["default"] = reader.read_string()
        elif opcode == ENUM_INT_DEFAULT_OPCODE:
            result["default"] = reader.read_signed()
        elif opcode in ENUM_STRING_VALUES_OPCODES or opcode in ENUM_INT_VALUES_OPCODES:
            sparse = opcode in ENUM_SPARSE_VALUE_OPCODES
            if sparse:
                reader.read_unsigned(2)  # Declared array capacity, not the entry count.
            string_values = opcode in ENUM_STRING_VALUES_OPCODES
            for _ in range(reader.read_unsigned(2)):
                key = reader.read_unsigned(2) if sparse else reader.read_signed()
                value = reader.read_string() if string_values else reader.read_signed()
                result["values"][key] = value
        elif opcode not in ENUM_IGNORED_OPCODES:
            raise DecodeError(f"Unknown enum opcode {opcode}")
    reader.require_end()
    return result


def decode_item(data):
    """Return the name, parameters, equipment slots and note links of an item definition."""
    reader = ByteReader(data)
    result = {"name": None, "params": {}, "equip_slot": -1, "hide_slot": -1}
    while True:
        opcode = reader.read_unsigned()
        if opcode == 0:
            break
        if opcode in ITEM_STRING_OPCODES:
            value = reader.read_string()
            if opcode == ITEM_NAME_OPCODE:
                result["name"] = value
        elif opcode in ITEM_SMART_OPCODES:
            reader.read_smart()
        elif opcode in ITEM_SHORT_OPCODES:
            reader.read_unsigned(2)
        elif opcode in ITEM_BYTE_OPCODES:
            value = reader.read_unsigned()
            if opcode == ITEM_EQUIP_SLOT_OPCODE:
                result["equip_slot"] = value
            if opcode == ITEM_HIDE_SLOT_OPCODE:
                result["hide_slot"] = value
        elif opcode in ITEM_FLAG_OPCODES:
            pass
        elif opcode in ITEM_INT_OPCODES:
            reader.read_unsigned(4)
        elif opcode == 9:
            for _ in range(reader.read_unsigned()):
                reader.read_smart()
        elif opcode in (40, 41):
            reader.read_bytes(reader.read_unsigned() * 4)
        elif opcode == 42:
            reader.read_bytes(reader.read_unsigned() * 2)
        elif 100 <= opcode <= 109:
            reader.read_bytes(4)
        elif opcode in ITEM_THREE_BYTE_OPCODES:
            reader.read_bytes(3)
        elif opcode == 132:
            reader.read_bytes(reader.read_unsigned() * 2)  # Revision 951: uint16 quest IDs.
        elif 142 <= opcode <= 146 or 150 <= opcode <= 154:
            reader.read_bytes(2)
        elif opcode == 181:
            reader.read_bytes(8)
        elif opcode == 182 or 201 <= opcode <= 208:
            value = reader.read_unsigned(3)
            if opcode == ITEM_NOTE_ID_OPCODE:
                result["note_id"] = value
            if opcode == ITEM_NOTE_TEMPLATE_OPCODE:
                result["note_template"] = value
        elif 190 <= opcode <= 199:
            reader.read_bytes(5)
        elif opcode == PARAMETERS_OPCODE:
            result["params"].update(reader.read_parameters())
        else:
            raise DecodeError(f"Unknown item opcode {opcode} at byte {reader.position - 1}")
    reader.require_end()
    return result
