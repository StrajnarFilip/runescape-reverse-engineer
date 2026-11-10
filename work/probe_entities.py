"""Search the client and world-state objects for an NPC registry layout (read-only).

Candidate headers are verified by resolving sampled runtime IDs through the buckets.
"""

import struct

from workspace import add_import_paths, first_client_pid

add_import_paths(reference=True)

from reference_decoder import ReferenceDecoder  # noqa: E402
from reference_memory import ProcessMemory, is_user_address  # noqa: E402

WORLD_STATE_POINTER_OFFSET = 8
WORLD_STATE_SCAN_BYTES = 0x56000
CLIENT_SCAN_BYTES = 0x40000
CHILD_SCAN_BYTES = 0xB0A8
REGISTRY_COUNT_OFFSET = 0xB0A0
REGISTRY_INDICES_OFFSET = 0xA0A0
REGISTRY_TABLE_OFFSET = 0x10
SAMPLED_IDS = 8
MAX_SAMPLED_RUNTIME_ID = 32767
MAX_CHAIN_LENGTH = 32


def collect_roots(memory, world_state, client):
    """Return the two objects and every object they point to (one level deep)."""
    roots = [("state", world_state, WORLD_STATE_SCAN_BYTES), ("client", client, CLIENT_SCAN_BYTES)]
    for label, parent, size in list(roots):
        data = memory.read(parent, size)
        for offset in range(0, len(data), 8):
            pointer = struct.unpack_from("<Q", data, offset)[0]
            if is_user_address(pointer):
                roots.append((f"{label} child+{hex(offset)}", pointer, CHILD_SCAN_BYTES))
    return roots


def resolve_entities(memory, table, bucket_count, runtime_ids):
    entities = []
    for runtime_id in runtime_ids:
        node = memory.pointer(table + runtime_id % bucket_count * 8)
        for _ in range(MAX_CHAIN_LENGTH):
            if not node:
                break
            if memory.i32(node) == runtime_id:
                actor = hex(memory.pointer(node + 16))
                entities.append((runtime_id, actor, hex(memory.pointer(node + 8))))
                break
            node = memory.pointer(node + 24)
    return entities


def scan_root(memory, name, base, data):
    """Print every plausible registry header inside one object."""
    for offset in range(0, len(data) - REGISTRY_COUNT_OFFSET - 4, 8):
        count = struct.unpack_from("<I", data, offset + REGISTRY_COUNT_OFFSET)[0]
        table, bucket_count = struct.unpack_from("<QQ", data, offset + REGISTRY_TABLE_OFFSET)
        if not (1 <= count <= 1024 and is_user_address(table) and 1 <= bucket_count <= 1000000):
            continue
        sampled = min(count, SAMPLED_IDS)
        indices_offset = offset + REGISTRY_INDICES_OFFSET
        runtime_ids = struct.unpack_from("<" + "I" * sampled, data, indices_offset)
        if not all(runtime_id <= MAX_SAMPLED_RUNTIME_ID for runtime_id in runtime_ids):
            continue
        if len(set(runtime_ids)) != len(runtime_ids):
            continue
        try:
            entities = resolve_entities(memory, table, bucket_count, runtime_ids)
        except OSError:
            continue
        if len(entities) == len(runtime_ids):
            location = (name, hex(offset), hex(base + offset))
            print("FOUND", *location, "count", count, "buckets", bucket_count, entities)


def main():
    with ProcessMemory(first_client_pid()) as memory:
        decoder = ReferenceDecoder(memory)
        client = decoder.domain_capture("client")
        world_state = memory.pointer(client + WORLD_STATE_POINTER_OFFSET)
        print("client", hex(client), "state", hex(world_state))
        scanned_bases = set()
        for name, base, size in collect_roots(memory, world_state, client):
            if base in scanned_bases:
                continue
            scanned_bases.add(base)
            try:
                data = memory.read(base, size)
            except OSError:
                continue
            scan_root(memory, name, base, data)


if __name__ == "__main__":
    main()
