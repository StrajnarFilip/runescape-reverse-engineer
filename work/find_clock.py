"""Identify counters advancing at the client's documented 50 logic ticks per second."""

import struct
import time

from workspace import add_import_paths, first_client_pid

add_import_paths(reference=True, vendor=True)

import pefile  # noqa: E402
from reference_decoder import ReferenceDecoder  # noqa: E402
from reference_memory import ProcessMemory  # noqa: E402

CLIENT_SCAN_BYTES = 0x8000
SAMPLE_DELAYS_SECONDS = (0, 0.2, 0.3)
# Expected tick increments for the 0.2 s and 0.3 s gaps at 50 ticks per second.
FIRST_GAP_TICKS = range(8, 13)
SECOND_GAP_TICKS = range(13, 18)


def main():
    with ProcessMemory(first_client_pid()) as memory:
        decoder = ReferenceDecoder(memory)
        client = decoder.domain_capture("client")
        executable = pefile.PE(memory.module.path, fast_load=True)
        data_section = next(
            section for section in executable.sections if section.Name.startswith(b".data")
        )
        blocks = [
            ("client", client, CLIENT_SCAN_BYTES),
            (
                "global",
                memory.module.base + data_section.VirtualAddress,
                data_section.Misc_VirtualSize,
            ),
        ]
        samples = []
        for delay in SAMPLE_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            samples.append([memory.read(base, size) for _, base, size in blocks])
        for block_index, (block_name, base, size) in enumerate(blocks):
            for offset in range(0, size - 4, 4):
                values = [
                    struct.unpack_from("<i", sample[block_index], offset)[0] for sample in samples
                ]
                if (
                    values[1] - values[0] in FIRST_GAP_TICKS
                    and values[2] - values[1] in SECOND_GAP_TICKS
                ):
                    rva = None
                    if block_name == "global":
                        rva = hex(base - memory.module.base + offset)
                    print(
                        {"block": block_name, "offset": hex(offset), "rva": rva, "values": values}
                    )


if __name__ == "__main__":
    main()
