"""Find x64 instructions that access selected member offsets in the installed executable.

Usage: python work/offset_refs.py OFFSET [OFFSET ...] [--all]
Without --all, only instructions in the 0xc00000..0xc80000 RVA range are listed.
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
CHAINED_UNWIND_FLAG = 4
MAX_UNWIND_CHAIN = 10
MAX_INSTRUCTION_BYTES = 15
MAX_DISPLACEMENT_LOOKBEHIND = 10
FOCUS_RVA_RANGE = range(0xC00000, 0xC80000)


class Executable:
    def __init__(self, path):
        self.pe = pefile.PE(str(path), fast_load=True)
        self.pe.parse_data_directories(directories=[EXCEPTION_DIRECTORY])
        self.image_base = self.pe.OPTIONAL_HEADER.ImageBase
        self.code_section = self.pe.sections[0]
        self.code = self.code_section.get_data()
        self.runtime_functions = self.pe.DIRECTORY_ENTRY_EXCEPTION
        self.function_starts = [entry.struct.BeginAddress for entry in self.runtime_functions]
        self.disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.disassembler.detail = True

    def function_containing(self, rva):
        """Return the primary function start, following chained unwind information."""
        index = bisect.bisect_right(self.function_starts, rva) - 1
        entry = self.runtime_functions[index].struct
        start, unwind = entry.BeginAddress, entry.UnwindData
        for _ in range(MAX_UNWIND_CHAIN):
            unwind_header = self.pe.get_data(unwind, 4)
            if not unwind_header or not unwind_header[0] >> 3 & CHAINED_UNWIND_FLAG:
                break
            chain = unwind + 4 + ((unwind_header[2] + 1) & ~1) * 2
            start, _end, unwind = struct.unpack("<III", self.pe.get_data(chain, 12))
        return hex(self.image_base + start)

    def instructions_with_displacement(self, displacement, include_all):
        found = {}
        encoded = re.escape(struct.pack("<i", displacement))
        section_rva = self.code_section.VirtualAddress
        for match in re.finditer(encoded, self.code):
            displacement_offset = match.start()
            first_candidate = max(0, displacement_offset - MAX_DISPLACEMENT_LOOKBEHIND)
            for start in range(first_candidate, displacement_offset):
                instruction = next(
                    self.disassembler.disasm(
                        self.code[start : start + MAX_INSTRUCTION_BYTES],
                        self.image_base + section_rva + start,
                        count=1,
                    ),
                    None,
                )
                if (
                    instruction
                    and instruction.disp_size == 4
                    and start + instruction.disp_offset == displacement_offset
                    and instruction.disp == displacement
                ):
                    rva = section_rva + start
                    if rva in FOCUS_RVA_RANGE or include_all:
                        found[instruction.address] = (
                            self.function_containing(rva),
                            f"{instruction.mnemonic} {instruction.op_str}",
                        )
        return found


def main():
    include_all = "--all" in sys.argv
    executable = Executable(INSTALLED_CLIENT_PATH)
    for argument in (argument for argument in sys.argv[1:] if not argument.startswith("--")):
        found = executable.instructions_with_displacement(int(argument, 0), include_all)
        print(argument, len(found))
        for address, (function_start, assembly) in found.items():
            print(hex(address), function_start, assembly)


if __name__ == "__main__":
    main()
