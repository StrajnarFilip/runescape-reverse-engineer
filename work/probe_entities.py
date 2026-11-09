import sys, struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "work" / "reference"))
from winmem import ProcessMemory, processes, MemoryReadError
from rs3 import RS3

with ProcessMemory(list(processes())[0]) as m:
    r=RS3(m); client=r.domain_capture('client'); state=m.pointer(client+8)
    print('client',hex(client),'state',hex(state))
    roots=[('state',state,0x56000),('client',client,0x40000)]
    # Snapshot the root list so this pass follows only one level of pointers.
    for label, parent, size in list(roots):
        data=m.read(parent,size)
        for off in range(0,len(data),8):
            p=struct.unpack_from('<Q',data,off)[0]
            if 0x10000<=p<0x7fffffffffff:
                roots.append((label+' child+'+hex(off),p,0xb0a8))
    seen=set()
    for name,base,size in roots:
        if base in seen: continue
        seen.add(base)
        try: b=m.read(base,size)
        except OSError: continue
        # Look for plausible registry headers, then verify sampled IDs through the buckets.
        for off in range(0,len(b)-0xb0a4,8):
            count=struct.unpack_from('<I',b,off+0xb0a0)[0]
            table,buckets=struct.unpack_from('<QQ',b,off+0x10)
            if not (1<=count<=1024 and 0x10000<=table<0x7fffffffffff and 1<=buckets<=1000000): continue
            ids=struct.unpack_from('<'+'I'*min(count,8),b,off+0xa0a0)
            if not all(x<32768 for x in ids) or len(set(ids))!=len(ids): continue
            try:
                entities=[]
                for uid in ids:
                    node=m.pointer(table+uid%buckets*8)
                    for step in range(32):
                        if not node:break
                        if m.i32(node)==uid:
                            entities.append((uid,hex(m.pointer(node+16)),hex(m.pointer(node+8))))
                            break
                        node=m.pointer(node+24)
                if len(entities)==len(ids): print('FOUND',name,hex(off),hex(base+off),'count',count,'buckets',buckets,entities)
            except OSError: pass
