"""Inspect the statically recovered NPC registry (read-only, numeric fields)."""
import sys, struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "work" / "reference"))
from winmem import ProcessMemory,processes
from rs3 import RS3
with ProcessMemory(list(processes())[0]) as m:
    r=RS3(m);client=r.domain_capture('client');state=m.pointer(client+8)
    manager=m.pointer(state+0x19940);table=m.pointer(manager+16);buckets=m.unpack(manager+24,'<Q')[0]
    count=m.i32(manager+0xb0a0)
    print('count',count,'manager vtable RVA',hex(m.pointer(manager)-m.module.base))
    if not 0<=count<=1024:raise RuntimeError('Invalid count')
    ids=m.unpack(manager+0xa0a0,'<'+'I'*count) if count else []
    for uid in ids:
        node=m.pointer(table+uid%buckets*8)
        for _ in range(128):
            if not node:break
            if m.u32(node)==uid:
                entity=m.pointer(node+16);vtable=m.pointer(entity)
                config=m.pointer(entity+0x10b8) or m.pointer(entity+0x10a8)
                configid=m.i32(config+8) if config else None
                raw=m.read(entity+0x90,24);tag=raw[23]
                length=struct.unpack_from('<Q',raw,8)[0] if tag&128 else 23-tag
                name=(m.read(struct.unpack_from('<Q',raw)[0],length) if tag&128 else raw[:length]).decode('utf8','replace') if 0<length<200 else None
                print(uid,hex(entity),'config',configid,'name',repr(name),
                      'uid',m.unpack(entity+0x88,'<H')[0])
                if '--combat' in sys.argv and name in ('Goblin','Giant spider'):
                    print('stats',m.unpack(entity+0x1150,'<14i'))
                    hit=m.pointer(entity+0xf18)
                    if hit:
                        cap=m.i32(hit+4);count=m.unpack(hit+24,'<Q')[0];data=m.pointer(hit+32)
                        print('hit capacity/count',cap,count,'records',m.unpack(data,'<'+'i'*6*count) if 0<count<32 else [])
                        begin=m.pointer(hit+40);end=m.pointer(hit+48)
                        for slot in range(begin,end,0x1b0):
                            node=m.pointer(slot)
                            for _ in range(32):
                                if node==slot:break
                                config=m.pointer(node+24)
                                print('bar',m.i32(config+8),m.unpack(node+48,'<6i'),'duration',m.i32(config+0x4c))
                                node=m.pointer(node)
                break
            node=m.pointer(node+24)
