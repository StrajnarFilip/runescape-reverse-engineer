"""Read-only inventory calibration with the reference decoder. Records changed slots only."""

import json
import struct
import time

from workspace import add_import_paths, first_client_pid

add_import_paths(reference=True)

from reference_decoder import ReferenceDecoder  # noqa: E402
from reference_memory import ProcessMemory  # noqa: E402

CAPTURE_SECONDS = 1800
POLL_SECONDS = 0.05
WORLD_STATE_POINTER_OFFSET = 8
INVENTORY_MANAGER_OFFSET = 0x199E0
REGISTRY_VECTOR_OFFSET = 8
REGISTRY_ENTRY_BYTES = 72
MAX_REGISTRY_ENTRIES = 4096
SLOT_RECORD_BYTES = 8
MAX_SLOTS = 64
CONTAINER_KEYS = (186, 188)  # Backpack (93) and equipment (94), doubled.


def timestamp_ms():
    return time.time_ns() // 1_000_000


def read_containers(memory, decoder):
    """Return ({container ID: [(item ID, quantity)]}, registry vector) of one read."""
    world_state = memory.pointer(decoder.domain_capture("client") + WORLD_STATE_POINTER_OFFSET)
    manager = memory.pointer(world_state + INVENTORY_MANAGER_OFFSET)
    entries_begin, entries_end = memory.unpack(manager + REGISTRY_VECTOR_OFFSET, "<QQ")
    registry_bytes = entries_end - entries_begin
    if not 0 <= registry_bytes <= REGISTRY_ENTRY_BYTES * MAX_REGISTRY_ENTRIES or (
        registry_bytes % REGISTRY_ENTRY_BYTES
    ):
        raise ValueError("Invalid inventory registry")
    registry = memory.read(entries_begin, registry_bytes)
    containers = {}
    for offset in range(0, len(registry), REGISTRY_ENTRY_BYTES):
        key, container_id = struct.unpack_from("<I12xI", registry, offset)
        if key not in CONTAINER_KEYS:
            continue
        slots_begin, slots_end = struct.unpack_from("<QQ", registry, offset + 24)
        slot_bytes = slots_end - slots_begin
        if not 0 <= slot_bytes <= SLOT_RECORD_BYTES * MAX_SLOTS or slot_bytes % SLOT_RECORD_BYTES:
            raise ValueError("Invalid slot vector")
        slots = memory.read(slots_begin, slot_bytes) if slots_end > slots_begin else b""
        containers[str(container_id)] = list(struct.iter_unpack("<iI", slots))
    return containers, manager, (entries_begin, entries_end)


def main():
    with ProcessMemory(first_client_pid()) as memory:
        decoder = ReferenceDecoder(memory)
        deadline = time.perf_counter() + CAPTURE_SECONDS
        previous_containers = None
        while time.perf_counter() < deadline:
            try:
                containers, manager, registry_vector = read_containers(memory, decoder)
                # Discard a read during which the registry vector moved.
                vector_after = memory.read(manager + REGISTRY_VECTOR_OFFSET, 16)
                if vector_after != struct.pack("<QQ", *registry_vector):
                    continue
                if containers != previous_containers:
                    row = {"timestamp_ms": timestamp_ms(), "containers": containers}
                    print(json.dumps(row), flush=True)
                    previous_containers = containers
            except (OSError, ValueError) as error:
                print(json.dumps({"timestamp_ms": timestamp_ms(), "error": str(error)}), flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
