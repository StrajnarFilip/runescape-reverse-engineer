"""Read-only inventory calibration. Records changed slots, never game inputs."""
import json
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python" / "rs3_reader"))
from winmem import ProcessMemory, processes
from rs3 import RS3

with ProcessMemory(list(processes())[0]) as memory:
    decoder = RS3(memory)
    deadline = time.perf_counter()+1800
    previous = None
    while time.perf_counter()<deadline:
        try:
            state = memory.pointer(decoder.domain_capture("client")+8)
            manager = memory.pointer(state+0x199e0)
            begin, end = memory.unpack(manager+8, "<QQ")
            if not 0 <= end-begin <= 72*4096 or (end-begin)%72:
                raise ValueError("Invalid inventory registry")
            data = memory.read(begin, end-begin)
            record = {}
            for offset in range(0, len(data), 72):
                key, container = struct.unpack_from("<I12xI", data, offset)
                if key not in (186, 188):
                    continue
                start, stop = struct.unpack_from("<QQ", data, offset+24)
                if not 0 <= stop-start <= 8*64 or (stop-start)%8:
                    raise ValueError("Invalid slot vector")
                slots = memory.read(start, stop-start) if stop>start else b""
                record[str(container)] = list(struct.iter_unpack("<iI", slots))
            if memory.read(manager+8,16) != struct.pack("<QQ", begin, end):
                continue
            if record != previous:
                print(json.dumps({"timestamp_ms":time.time_ns()//1000000,"containers":record}),flush=True)
                previous = record
        except (OSError, ValueError) as error:
            print(json.dumps({"timestamp_ms":time.time_ns()//1000000,"error":str(error)}),flush=True)
        time.sleep(.05)
