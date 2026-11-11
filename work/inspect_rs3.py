"""Read-only PE metadata and string triage of the installed client; not a decompiler.

Writes docs/validation/static-inspection.json.
"""

import hashlib
import json
import re
import struct

from workspace import INSTALLED_CLIENT_PATH, VALIDATION_DIRECTORY

PE_SIGNATURE = b"PE\0\0"
PE32_PLUS_MAGIC = 0x20B
SECTION_HEADER_BYTES = 40
IMPORT_DESCRIPTOR_BYTES = 20
MIN_STRING_LENGTH = 5
MAX_STRING_LENGTH = 1000
MAX_CANDIDATES_PER_GROUP = 100
STRING_PATTERNS = {
    "combat_candidates": re.compile(
        r"telos|adrenalin|prayer|hitpoint|life.?point|health|global.?cooldown|cooldown", re.I
    ),
    "plugin_api_candidates": re.compile(
        r"VarTransmit|ServerLogic|GetLevelGameplay|NPCEntity|dev_plugins|lua[A-Z_]|lua_[a-z]", re.I
    ),
}


def read_sections(data, optional_header, optional_header_size, section_count):
    sections = []
    for index in range(section_count):
        offset = optional_header + optional_header_size + index * SECTION_HEADER_BYTES
        name = data[offset : offset + 8].rstrip(b"\0").decode("ascii", "replace")
        virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        sections.append(
            {
                "name": name,
                "rva": rva,
                "virtual_size": virtual_size,
                "raw_offset": raw_offset,
                "raw_size": raw_size,
            }
        )
    return sections


def file_offset_from_rva(sections, rva):
    for section in sections:
        extent = max(section["virtual_size"], section["raw_size"])
        if section["rva"] <= rva < section["rva"] + extent:
            return section["raw_offset"] + rva - section["rva"]
    raise ValueError("RVA not mapped to a section")


def read_c_string(data, offset):
    return data[offset : data.index(b"\0", offset)].decode("ascii", "replace")


def imported_libraries(data, sections, directories):
    imports_rva, _imports_size = struct.unpack_from("<II", data, directories + 8)
    libraries = []
    if imports_rva:
        offset = file_offset_from_rva(sections, imports_rva)
        while any(data[offset : offset + IMPORT_DESCRIPTOR_BYTES]):
            name_rva = struct.unpack_from("<I", data, offset + 12)[0]
            libraries.append(read_c_string(data, file_offset_from_rva(sections, name_rva)))
            offset += IMPORT_DESCRIPTOR_BYTES
    return libraries


def string_candidates(data):
    matches = {group: [] for group in STRING_PATTERNS}
    counts = {group: 0 for group in STRING_PATTERNS}
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % MIN_STRING_LENGTH, data):
        text = match.group().decode("ascii")
        if len(text) > MAX_STRING_LENGTH:
            continue
        for group, pattern in STRING_PATTERNS.items():
            if pattern.search(text):
                counts[group] += 1
                if len(matches[group]) < MAX_CANDIDATES_PER_GROUP:
                    matches[group].append({"file_offset": hex(match.start()), "text": text})
    return counts, matches


def main():
    data = INSTALLED_CLIENT_PATH.read_bytes()
    pe_header = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[:2] == b"MZ" and data[pe_header : pe_header + 4] == PE_SIGNATURE
    machine, section_count, _timestamp, _, _, optional_header_size, _characteristics = (
        struct.unpack_from("<HHIIIHH", data, pe_header + 4)
    )
    optional_header = pe_header + 24
    magic = struct.unpack_from("<H", data, optional_header)[0]
    sections = read_sections(data, optional_header, optional_header_size, section_count)
    directories = optional_header + (112 if magic == PE32_PLUS_MAGIC else 96)
    counts, matches = string_candidates(data)
    report = {
        "scope": (
            "Read-only static PE metadata and ASCII string triage; "
            "no decompilation or live telemetry"
        ),
        "client_path": str(INSTALLED_CLIENT_PATH),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "architecture": hex(machine),
        "pe_magic": hex(magic),
        "sections": sections,
        "imported_libraries": imported_libraries(data, sections, directories),
        "string_match_counts": counts,
        "string_candidates": matches,
        "caution": (
            "String matches are leads only. They do not confirm callable APIs, variable IDs, "
            "offsets, units or live values."
        ),
        "runtime_values": {
            "telos_action": None,
            "global_cooldown": None,
            "adrenaline": None,
            "prayer_points": None,
            "health": None,
        },
    }
    destination = VALIDATION_DIRECTORY / "static-inspection.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
