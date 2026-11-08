"""Investigate the skill-manager offset observed in this client's machine code."""
import sys
from pathlib import Path
import struct
import bisect
import time
sys.path[:0] = [str(Path(__file__).parent / "vendor"), str(Path(__file__).parent.parent / "python" / "rs3_reader")]
import pefile
from winmem import ProcessMemory, processes, MemoryReadError

pid = list(processes())[0]
with ProcessMemory(pid) as mem:
    pe = pefile.PE(mem.module.path)
    regions = list(mem.regions())
    bases = [r[0] for r in regions]
    def mapped(addr, length=8):
        i = bisect.bisect_right(bases, addr) - 1
        return i >= 0 and addr + length <= regions[i][0] + regions[i][1]
    candidates = {}
    for section in pe.sections:
        if not section.Name.startswith(b".data"):
            continue
        start = mem.module.base + section.VirtualAddress
        raw = mem.read(start, section.Misc_VirtualSize)
        for off in range(0, len(raw) - 8, 8):
            p, = struct.unpack_from("<Q", raw, off)
            if mapped(p + 0x72e8):
                candidates.setdefault(p, []).append(section.VirtualAddress + off)
    print("Global-pointer candidates", len(candidates), flush=True)
    children = {}
    for parent, rvas in candidates.items():
        try:
            raw = mem.read(parent, 0x200)
            for off in range(0, len(raw), 8):
                child, = struct.unpack_from("<Q", raw, off)
                if child not in candidates and mapped(child + 0x72e8):
                    children.setdefault(child, []).append((rvas[0], off))
        except MemoryReadError:
            pass
    print("Child-pointer candidates", len(children), flush=True)
    candidates.update({key: value for key, value in children.items()})
    for root, rvas in candidates.items():
        try:
            manager = mem.pointer(root + 0x72e8)
            if not manager or not mapped(manager + 0x10):
                continue
            entries = mem.pointer(manager + 0x10)
            if not entries or not mapped(entries, 24 * 29):
                continue
            raw = mem.read(entries, 24 * 29)
            levels = [struct.unpack_from("<i", raw, i * 24 + 20)[0] for i in range(29)]
            if levels[3] in (87, 8700, 17400) or levels[5] in (84, 840, 8400):
                print({"root": hex(root), "path": rvas, "manager": hex(manager), "entries": hex(entries), "levels": levels}, flush=True)
        except MemoryReadError:
            pass
