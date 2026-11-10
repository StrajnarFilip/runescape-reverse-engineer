"""Find numeric candidates for the supplied gameplay-variable IDs.

No memory dumps are written. Candidate context is reported as bounded numeric
words only when a health or prayer value also matches the user's calibration.
"""

import json
import struct
import time

from workspace import WORK_DIRECTORY, add_import_paths, first_client_pid

add_import_paths(reference=True)

from reference_memory import MemoryReadError, ProcessMemory  # noqa: E402

# Variable ID -> calibration value of the user's account.
TARGET_VALUES = {13537: 8700, 13538: 8700, 3274: 840}
MEM_PRIVATE = 0x20000
EXECUTABLE_PROTECTION_MASK = 0xF0
CHUNK_BYTES = 1024 * 1024
CHUNK_OVERLAP_BYTES = 128
CONTEXT_BEFORE_BYTES = 32
CONTEXT_AFTER_BYTES = 68
TIME_LIMIT_SECONDS = 45
REPORT_PATH = WORK_DIRECTORY / "var_node_candidates.json"


def scan_chunk(data, chunk_address, counts, results):
    for variable_id, expected_value in TARGET_VALUES.items():
        needle = struct.pack("<I", variable_id)
        position = data.find(needle)
        while position >= 0:
            counts[variable_id] += 1
            left = max(0, position - CONTEXT_BEFORE_BYTES)
            right = min(len(data), position + CONTEXT_AFTER_BYTES)
            if struct.pack("<I", expected_value) in data[left:right]:
                words = [
                    struct.unpack_from("<I", data, index)[0] for index in range(left, right - 3, 4)
                ]
                result = {
                    "id": variable_id,
                    "address": hex(chunk_address + position),
                    "context_start": hex(chunk_address + left),
                    "words": words,
                }
                results.append(result)
                print(json.dumps(result), flush=True)
            position = data.find(needle, position + 4)


def main():
    results = []
    counts = dict.fromkeys(TARGET_VALUES, 0)
    with ProcessMemory(first_client_pid()) as memory:
        private_data_regions = [
            region
            for region in memory.regions()
            if region[2] == MEM_PRIVATE and not region[3] & EXECUTABLE_PROTECTION_MASK
        ]
        private_megabytes = sum(region[1] for region in private_data_regions) // 1048576
        print("Private data MB:", private_megabytes, flush=True)
        started = time.monotonic()
        scanned_bytes = 0
        for base, size, _, _ in private_data_regions:
            for offset in range(0, size, CHUNK_BYTES):
                length = min(size - offset, CHUNK_BYTES + CHUNK_OVERLAP_BYTES)
                try:
                    data = memory.read(base + offset, length)
                except MemoryReadError:
                    continue
                scanned_bytes += length
                scan_chunk(data, base + offset, counts, results)
                if time.monotonic() - started > TIME_LIMIT_SECONDS:
                    break
            if time.monotonic() - started > TIME_LIMIT_SECONDS:
                break
        report = {
            "pid": memory.pid,
            "scanned_mb": scanned_bytes // 1048576,
            "counts": counts,
            "elapsed_s": time.monotonic() - started,
            "candidates": results,
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2))
        summary = {key: value for key, value in report.items() if key != "candidates"}
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
