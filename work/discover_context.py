"""Investigate the skill-manager offset observed in this client's machine code.

Scans the .data sections for pointers whose target has a mapped skill manager,
then prints candidates whose skill table holds the calibration account's levels.
"""

import bisect
import struct

from workspace import add_import_paths, first_client_pid

add_import_paths(reference=True, vendor=True)

import pefile  # noqa: E402
from reference_memory import MemoryReadError, ProcessMemory  # noqa: E402

SKILLS_MANAGER_OFFSET = 0x72E8
SKILL_ENTRIES_OFFSET = 0x10
SKILL_COUNT = 29
SKILL_STRIDE = 24
SKILL_LEVEL_OFFSET = 20
CHILD_SCAN_BYTES = 0x200
# Calibration account: Constitution and Prayer levels in their known encodings.
EXPECTED_CONSTITUTION_VALUES = (87, 8700, 17400)
EXPECTED_PRAYER_VALUES = (84, 840, 8400)


class MappedRanges:
    def __init__(self, regions):
        self.regions = regions
        self.bases = [region[0] for region in regions]

    def contains(self, address, length=8):
        index = bisect.bisect_right(self.bases, address) - 1
        return index >= 0 and address + length <= self.regions[index][0] + self.regions[index][1]


def global_pointer_candidates(memory, executable, mapped):
    candidates = {}
    for section in executable.sections:
        if not section.Name.startswith(b".data"):
            continue
        start = memory.module.base + section.VirtualAddress
        data = memory.read(start, section.Misc_VirtualSize)
        for offset in range(0, len(data) - 8, 8):
            (pointer,) = struct.unpack_from("<Q", data, offset)
            if mapped.contains(pointer + SKILLS_MANAGER_OFFSET):
                candidates.setdefault(pointer, []).append(section.VirtualAddress + offset)
    return candidates


def child_pointer_candidates(memory, candidates, mapped):
    children = {}
    for parent, rvas in candidates.items():
        try:
            data = memory.read(parent, CHILD_SCAN_BYTES)
        except MemoryReadError:
            continue
        for offset in range(0, len(data), 8):
            (child,) = struct.unpack_from("<Q", data, offset)
            if child not in candidates and mapped.contains(child + SKILLS_MANAGER_OFFSET):
                children.setdefault(child, []).append((rvas[0], offset))
    return children


def report_matching_skill_tables(memory, candidates, mapped):
    for root, path in candidates.items():
        try:
            manager = memory.pointer(root + SKILLS_MANAGER_OFFSET)
            if not manager or not mapped.contains(manager + SKILL_ENTRIES_OFFSET):
                continue
            entries = memory.pointer(manager + SKILL_ENTRIES_OFFSET)
            if not entries or not mapped.contains(entries, SKILL_STRIDE * SKILL_COUNT):
                continue
            data = memory.read(entries, SKILL_STRIDE * SKILL_COUNT)
            levels = [
                struct.unpack_from("<i", data, skill * SKILL_STRIDE + SKILL_LEVEL_OFFSET)[0]
                for skill in range(SKILL_COUNT)
            ]
            if levels[3] in EXPECTED_CONSTITUTION_VALUES or levels[5] in EXPECTED_PRAYER_VALUES:
                print(
                    {
                        "root": hex(root),
                        "path": path,
                        "manager": hex(manager),
                        "entries": hex(entries),
                        "levels": levels,
                    },
                    flush=True,
                )
        except MemoryReadError:
            pass


def main():
    with ProcessMemory(first_client_pid()) as memory:
        executable = pefile.PE(memory.module.path)
        mapped = MappedRanges(list(memory.regions()))
        candidates = global_pointer_candidates(memory, executable, mapped)
        print("Global-pointer candidates", len(candidates), flush=True)
        children = child_pointer_candidates(memory, candidates, mapped)
        print("Child-pointer candidates", len(children), flush=True)
        candidates.update(children)
        report_matching_skill_tables(memory, candidates, mapped)


if __name__ == "__main__":
    main()
