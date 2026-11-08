"""Identify counters advancing at the client's documented 50 logic ticks/sec."""
import sys
from pathlib import Path
import struct
import time
sys.path[:0] = [str(Path(__file__).parent / "vendor"), str(Path(__file__).parent.parent / "python" / "rs3_reader")]
from winmem import ProcessMemory, processes
from rs3 import RS3
import pefile

with ProcessMemory(list(processes())[0]) as memory:
    decoder = RS3(memory)
    client = decoder.domain_capture("client")
    pe = pefile.PE(memory.module.path, fast_load=True)
    section = next(s for s in pe.sections if s.Name.startswith(b".data"))
    blocks = [("client", client, 0x8000),
              ("global", memory.module.base + section.VirtualAddress, section.Misc_VirtualSize)]
    samples = []
    for delay in [0, 0.2, 0.3]:
        if delay:
            time.sleep(delay)
        samples.append([memory.read(base, size) for _, base, size in blocks])
    for j, (name, base, size) in enumerate(blocks):
        for offset in range(0, size-4, 4):
            values = [struct.unpack_from("<i", sample[j], offset)[0] for sample in samples]
            if 8 <= values[1]-values[0] <= 12 and 13 <= values[2]-values[1] <= 17:
                print({"block": name, "offset": hex(offset), "rva": hex(base-memory.module.base+offset) if name=="global" else None, "values": values})
