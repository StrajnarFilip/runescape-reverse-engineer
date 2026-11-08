"""Find x64 instructions accessing selected member offsets in the local PE."""
import sys,struct,bisect,re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'vendor'))
import pefile,capstone
p=pefile.PE(r'C:\ProgramData\Jagex\launcher\rs2client.exe',fast_load=True)
p.parse_data_directories(directories=[3]);base=p.OPTIONAL_HEADER.ImageBase
s=p.sections[0];code=s.get_data();entries=p.DIRECTORY_ENTRY_EXCEPTION
starts=[e.struct.BeginAddress for e in entries]
cs=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);cs.detail=True
def function(rva):
    e=entries[bisect.bisect_right(starts,rva)-1].struct
    start,end,unwind=e.BeginAddress,e.EndAddress,e.UnwindData
    for _ in range(10):
        b=p.get_data(unwind,4)
        if not b or not b[0]>>3&4:break
        chain=unwind+4+((b[2]+1)&~1)*2
        start,end,unwind=struct.unpack('<III',p.get_data(chain,12))
    return hex(base+start)
for arg in (arg for arg in sys.argv[1:] if not arg.startswith('--')):
    disp=int(arg,0);found={}
    for match in re.finditer(re.escape(struct.pack('<i',disp)),code):
        o=match.start()
        for start in range(max(0,o-10),o):
            ins=next(cs.disasm(code[start:start+15],base+s.VirtualAddress+start,count=1),None)
            if ins and ins.disp_size==4 and start+ins.disp_offset==o and ins.disp==disp:
                addr=s.VirtualAddress+start
                if 0xc00000<=addr<0xc80000 or '--all' in sys.argv:
                    found[ins.address]=(function(addr),ins.mnemonic+' '+ins.op_str)
    print(arg,len(found))
    for address,(fn,asm) in found.items():print(hex(address),fn,asm)
