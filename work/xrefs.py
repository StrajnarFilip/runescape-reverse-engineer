"""Local static disassembly helpers for the installed executable.

Usage:
    python work/xrefs.py strings TEXT [TEXT ...]
    python work/xrefs.py calls ADDRESS [--context]
    python work/xrefs.py refs ADDRESS [--context]
    python work/xrefs.py disasm ADDRESS [BYTES]
    python work/xrefs.py function ADDRESS
Addresses are hexadecimal virtual addresses.
"""

import bisect
import re
import struct
import sys

from workspace import INSTALLED_CLIENT_PATH, add_import_paths

add_import_paths(vendor=True)

import capstone  # noqa: E402
import pefile  # noqa: E402

EXCEPTION_DIRECTORY = 3
RIP_RELATIVE_LEA = rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]"
LEA_LENGTH = 7
CALL_OPCODE = b"\xe8"
CALL_LENGTH = 5
DEFAULT_DISASSEMBLY_BYTES = 200
MAX_FUNCTION_DISASSEMBLY_BYTES = 1600


class Executable:
    def __init__(self, path):
        self.pe = pefile.PE(str(path), fast_load=True)
        self.pe.parse_data_directories(directories=[EXCEPTION_DIRECTORY])
        self.image_base = self.pe.OPTIONAL_HEADER.ImageBase
        self.code_section = self.pe.sections[0]
        self.code = self.code_section.get_data()
        self.file_bytes = self.pe.__data__[:]
        self.disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.function_starts = [
            entry.struct.BeginAddress for entry in self.pe.DIRECTORY_ENTRY_EXCEPTION
        ]

    def function_containing(self, address):
        """Return (start address, size) of the runtime function containing an address or RVA."""
        rva = address - self.image_base if address >= self.image_base else address
        index = bisect.bisect_right(self.function_starts, rva) - 1
        entry = self.pe.DIRECTORY_ENTRY_EXCEPTION[index].struct
        return self.image_base + entry.BeginAddress, entry.EndAddress - entry.BeginAddress

    def disassemble(self, address, size):
        data = self.pe.get_data(address - self.image_base, size)
        for instruction in self.disassembler.disasm(data, address):
            print(hex(instruction.address), instruction.mnemonic, instruction.op_str)

    def lea_references(self, target_rva):
        """Yield the virtual addresses of RIP-relative LEA instructions that load target_rva."""
        section_rva = self.code_section.VirtualAddress
        for match in re.finditer(RIP_RELATIVE_LEA, self.code):
            offset = match.start()
            displacement = struct.unpack_from("<i", self.code, offset + 3)[0]
            if section_rva + offset + LEA_LENGTH + displacement == target_rva:
                yield self.image_base + section_rva + offset

    def call_sites(self, target_rva):
        section_rva = self.code_section.VirtualAddress
        for match in re.finditer(CALL_OPCODE, self.code):
            offset = match.start()
            if offset + CALL_LENGTH > len(self.code):
                continue
            displacement = struct.unpack_from("<i", self.code, offset + 1)[0]
            if section_rva + offset + CALL_LENGTH + displacement == target_rva:
                yield self.image_base + section_rva + offset


def print_string_references(executable, terms):
    for term in terms:
        needle = term.encode() + b"\0"
        for match in re.finditer(re.escape(needle), executable.file_bytes):
            file_offset = match.start()
            if file_offset and executable.file_bytes[file_offset - 1] != 0:
                continue
            rva = executable.pe.get_rva_from_offset(file_offset)
            references = [
                {"xref": hex(address), "function": hex(executable.function_containing(address)[0])}
                for address in executable.lea_references(rva)
            ]
            print(term, hex(executable.image_base + rva), references)


def main():
    executable = Executable(INSTALLED_CLIENT_PATH)
    mode = sys.argv[1]
    show_context = "--context" in sys.argv
    if mode == "strings":
        print_string_references(executable, sys.argv[2:])
    elif mode == "calls":
        target_rva = int(sys.argv[2], 16) - executable.image_base
        for address in executable.call_sites(target_rva):
            print("CALL", hex(address), "FUNCTION", hex(executable.function_containing(address)[0]))
            if show_context:
                executable.disassemble(address - 100, 140)
    elif mode == "disasm":
        size = int(sys.argv[3], 0) if len(sys.argv) > 3 else DEFAULT_DISASSEMBLY_BYTES
        executable.disassemble(int(sys.argv[2], 16), size)
    elif mode == "function":
        start, size = executable.function_containing(int(sys.argv[2], 16))
        print(hex(start), hex(size))
        executable.disassemble(start, min(size, MAX_FUNCTION_DISASSEMBLY_BYTES))
    elif mode == "refs":
        target_rva = int(sys.argv[2], 16) - executable.image_base
        for address in executable.lea_references(target_rva):
            print("REF", hex(address), "FUNCTION", hex(executable.function_containing(address)[0]))
            if show_context:
                executable.disassemble(address, 100)


if __name__ == "__main__":
    main()
