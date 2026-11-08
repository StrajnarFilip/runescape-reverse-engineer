"""Local static disassembly helpers for the installed executable."""
import sys
from pathlib import Path
import re
import struct
import bisect
sys.path.insert(0, str(Path(__file__).parent / "vendor"))
import pefile
import capstone

pe = pefile.PE(r"C:\ProgramData\Jagex\launcher\rs2client.exe", fast_load=True)
pe.parse_data_directories(directories=[3])
base = pe.OPTIONAL_HEADER.ImageBase
section = pe.sections[0]
code = section.get_data()
binary = pe.__data__[:]
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
starts = [e.struct.BeginAddress for e in pe.DIRECTORY_ENTRY_EXCEPTION]

def function(address):
    rva = address - base if address >= base else address
    entry = pe.DIRECTORY_ENTRY_EXCEPTION[bisect.bisect_right(starts, rva) - 1].struct
    return base + entry.BeginAddress, entry.EndAddress - entry.BeginAddress

def disasm(address, size):
    for ins in cs.disasm(pe.get_data(address-base, size), address):
        print(hex(ins.address), ins.mnemonic, ins.op_str)

mode = sys.argv[1]
if mode == "strings":
    for term in sys.argv[2:]:
        needle = term.encode() + b"\0"
        for match in re.finditer(re.escape(needle), binary):
            off = match.start()
            if off and binary[off-1] != 0:
                continue
            rva = pe.get_rva_from_offset(off)
            refs = []
            for m in re.finditer(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", code):
                k = m.start()
                target = section.VirtualAddress + k + 7 + struct.unpack_from("<i", code, k+3)[0]
                if target == rva:
                    address = base + section.VirtualAddress + k
                    refs.append({"xref": hex(address), "function": hex(function(address)[0])})
            print(term, hex(base+rva), refs)
elif mode == "calls":
    target = int(sys.argv[2], 16) - base
    for m in re.finditer(b"\xe8", code):
        o = m.start()
        if o+5 <= len(code) and section.VirtualAddress+o+5+struct.unpack_from("<i",code,o+1)[0] == target:
            address = base + section.VirtualAddress + o
            print("CALL",hex(address),"FUNCTION",hex(function(address)[0]))
            if "--context" in sys.argv:
                disasm(address-100,140)
elif mode == "disasm":
    address = int(sys.argv[2],16)
    disasm(address, int(sys.argv[3],0) if len(sys.argv)>3 else 200)
elif mode == "function":
    address = int(sys.argv[2],16)
    start,size = function(address)
    print(hex(start),hex(size))
    disasm(start, min(size, 1600))
elif mode == "refs":
    target = int(sys.argv[2], 16) - base
    for match in re.finditer(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", code):
        offset = match.start()
        if section.VirtualAddress + offset + 7 + struct.unpack_from("<i", code, offset+3)[0] == target:
            address = base + section.VirtualAddress + offset
            print("REF", hex(address), "FUNCTION", hex(function(address)[0]))
            if "--context" in sys.argv:
                disasm(address, 100)
