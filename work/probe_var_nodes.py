"""Find only numeric candidates for the supplied gameplay-variable IDs.

No memory dumps are written. Candidate context is reported as bounded numeric
words only when a health/prayer value also matches the user's calibration.
"""
import sys
from pathlib import Path
import struct
import json
import time
sys.path.insert(0, str(Path(__file__).parent.parent / "outputs" / "rs3_reader"))
from winmem import ProcessMemory, processes, MemoryReadError

targets = {13537: 8700, 13538: 8700, 3274: 840}
results = []
counts = dict.fromkeys(targets, 0)
with ProcessMemory(list(processes())[0]) as memory:
    regions = [r for r in memory.regions() if r[2] == 0x20000 and not r[3] & 0xF0]
    print("Private data MB:", sum(r[1] for r in regions) // 1048576, flush=True)
    started = time.monotonic()
    total = 0
    for base, size, _, _ in regions:
        for offset in range(0, size, 1024*1024):
            length = min(size-offset, 1024*1024+128)
            try:
                data = memory.read(base+offset, length)
            except MemoryReadError:
                continue
            total += length
            for varid, expected in targets.items():
                needle = struct.pack("<I", varid)
                position = data.find(needle)
                while position >= 0:
                    counts[varid] += 1
                    left, right = max(0, position-32), min(len(data), position+68)
                    area = data[left:right]
                    if struct.pack("<I", expected) in area:
                        words = [struct.unpack_from("<I", data, i)[0] for i in range(left, right-3, 4)]
                        result = {"id": varid, "address": hex(base+offset+position),
                                  "context_start": hex(base+offset+left), "words": words}
                        results.append(result)
                        print(json.dumps(result), flush=True)
                    position = data.find(needle, position+4)
            if time.monotonic()-started > 45:
                break
        if time.monotonic()-started > 45:
            break
    report = {"pid": memory.pid, "scanned_mb": total//1048576, "counts": counts,
              "elapsed_s": time.monotonic()-started, "candidates": results}
    (Path(__file__).parent / "var_node_candidates.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({key:value for key,value in report.items() if key!="candidates"}),flush=True)
