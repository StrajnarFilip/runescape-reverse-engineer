"""Record reference snapshots with candidate logic-clock words for 15 minutes.

Usage: python work/capture_clocks.py [OUTPUT.jsonl]
"""

import json
import sys
import time

from workspace import WORK_DIRECTORY, add_import_paths, first_client_pid

add_import_paths(reference=True)

from reference_decoder import ReferenceDecoder  # noqa: E402
from reference_memory import ProcessMemory  # noqa: E402

CAPTURE_SECONDS = 900
POLL_SECONDS = 0.05
WORLD_STATE_POINTER_OFFSET = 8
CANDIDATE_CLOCK_OFFSETS = (0x528, 0x52C, 0x530, 0x548)


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else WORK_DIRECTORY / "combat_clocks.jsonl"
    with ProcessMemory(first_client_pid()) as memory, open(output_path, "w") as output:
        decoder = ReferenceDecoder(memory)
        deadline = time.perf_counter() + CAPTURE_SECONDS
        while time.perf_counter() < deadline:
            snapshot = decoder.snapshot(True)
            client = decoder.domain_capture("client")
            world_state = memory.pointer(client + WORLD_STATE_POINTER_OFFSET)
            snapshot["clocks"] = {
                hex(offset): memory.i32(world_state + offset) for offset in CANDIDATE_CLOCK_OFFSETS
            }
            output.write(json.dumps(snapshot) + "\n")
            output.flush()
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
