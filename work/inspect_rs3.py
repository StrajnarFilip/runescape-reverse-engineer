"""Read-only PE metadata and string triage; this is not a decompiler."""
import hashlib
import json
import re
import struct
from pathlib import Path

client = Path(r"C:\ProgramData\Jagex\launcher\rs2client.exe")
data = client.read_bytes()
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0"
machine, count, timestamp, _, _, opt_size, characteristics = struct.unpack_from(
    "<HHIIIHH", data, pe + 4
)
optional = pe + 24
magic = struct.unpack_from("<H", data, optional)[0]
sections = []
for i in range(count):
    off = optional + opt_size + i * 40
    name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
    virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", data, off + 8)
    sections.append(dict(name=name, rva=rva, virtual_size=virtual_size,
                         raw_offset=raw_offset, raw_size=raw_size))

def raw_from_rva(rva):
    for section in sections:
        if section["rva"] <= rva < section["rva"] + max(section["virtual_size"], section["raw_size"]):
            return section["raw_offset"] + rva - section["rva"]
    raise ValueError("RVA not mapped to a section")

def cstring(offset):
    return data[offset:data.index(b"\0", offset)].decode("ascii", "replace")

directories = optional + (112 if magic == 0x20B else 96)
imports_rva, imports_size = struct.unpack_from("<II", data, directories + 8)
imports = []
if imports_rva:
    off = raw_from_rva(imports_rva)
    while any(data[off:off + 20]):
        name_rva = struct.unpack_from("<I", data, off + 12)[0]
        imports.append(cstring(raw_from_rva(name_rva)))
        off += 20

patterns = {
    "combat_candidates": re.compile(r"telos|adrenalin|prayer|hitpoint|life.?point|health|global.?cooldown|cooldown", re.I),
    "plugin_api_candidates": re.compile(r"VarTransmit|ServerLogic|GetLevelGameplay|NPCEntity|dev_plugins|lua[A-Z_]|lua_[a-z]", re.I),
}
matches = {name: [] for name in patterns}
counts = {name: 0 for name in patterns}
for match in re.finditer(rb"[\x20-\x7e]{5,}", data):
    value = match.group().decode("ascii")
    if len(value) > 1000:
        continue
    for group, pattern in patterns.items():
        if pattern.search(value):
            counts[group] += 1
            if len(matches[group]) < 100:
                matches[group].append(dict(file_offset=hex(match.start()), text=value))

report = dict(
    scope="Read-only static PE metadata and ASCII string triage; no decompilation or live telemetry",
    client_path=str(client), size=len(data), sha256=hashlib.sha256(data).hexdigest(),
    architecture=hex(machine), pe_magic=hex(magic), sections=sections,
    imported_libraries=imports, string_match_counts=counts, string_candidates=matches,
    caution="String matches are leads only. They do not confirm callable APIs, variable IDs, offsets, units or live values.",
    runtime_values={"telos_action": None, "global_cooldown": None,
                    "adrenaline": None, "prayer_points": None, "health": None},
)
destination = Path(__file__).resolve().parent.parent / "outputs" / "rs3-static-inspection.json"
destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
