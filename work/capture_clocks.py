import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs'/'rs3_reader'))
from winmem import ProcessMemory, processes
from rs3 import RS3
with ProcessMemory(list(processes())[0]) as m, open(sys.argv[1] if len(sys.argv)>1 else 'work/combat_clocks.jsonl','w') as f:
    r=RS3(m); end=time.perf_counter()+900
    while time.perf_counter()<end:
        s=r.snapshot(True); client=r.domain_capture('client');state=m.pointer(client+8)
        s['clocks']={hex(o):m.i32(state+o) for o in [0x528,0x52c,0x530,0x548]}
        f.write(json.dumps(s)+'\n');f.flush();time.sleep(.05)
