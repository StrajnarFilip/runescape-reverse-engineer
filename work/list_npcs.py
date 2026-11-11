"""Inspect the statically recovered NPC registry (read-only, numeric fields).

Usage: python work/list_npcs.py [--combat]
With --combat, statistics and hit-bar records of Goblins and Giant spiders are printed.
"""

import struct
import sys

from workspace import add_import_paths, first_client_pid

add_import_paths(reference=True)

from reference_decoder import ReferenceDecoder  # noqa: E402
from reference_memory import ProcessMemory  # noqa: E402

WORLD_STATE_POINTER_OFFSET = 8
NPC_MANAGER_OFFSET = 0x19940
NPC_COUNT_OFFSET = 0xB0A0
NPC_INDICES_OFFSET = 0xA0A0
NPC_CONFIG_OFFSET = 0x10A8
NPC_CONFIG_OVERRIDE_OFFSET = 0x10B8
NPC_NAME_OFFSET = 0x90
NPC_RUNTIME_ID_OFFSET = 0x88
NPC_STATISTICS_OFFSET = 0x1150
NPC_HITMARKS_OFFSET = 0xF18
HIT_BAR_SLOT_STRIDE = 0x1B0
MAX_CHAIN_LENGTH = 128
MAX_BAR_CHAIN_LENGTH = 32
COMBAT_NAMES = ("Goblin", "Giant spider")


def read_name(memory, actor):
    data = memory.read(actor + NPC_NAME_OFFSET, 24)
    tag = data[23]
    heap_backed = bool(tag & 0x80)
    length = struct.unpack_from("<Q", data, 8)[0] if heap_backed else 23 - tag
    if not 0 < length < 200:
        return None
    if heap_backed:
        raw_name = memory.read(struct.unpack_from("<Q", data)[0], length)
    else:
        raw_name = data[:length]
    return raw_name.decode("utf8", "replace")


def print_combat_details(memory, actor):
    print("stats", memory.unpack(actor + NPC_STATISTICS_OFFSET, "<14i"))
    ring = memory.pointer(actor + NPC_HITMARKS_OFFSET)
    if not ring:
        return
    capacity = memory.i32(ring + 4)
    count = memory.unpack(ring + 24, "<Q")[0]
    records_address = memory.pointer(ring + 32)
    records = memory.unpack(records_address, "<" + "i" * 6 * count) if 0 < count < 32 else []
    print("hit capacity/count", capacity, count, "records", records)
    bars_begin = memory.pointer(ring + 40)
    bars_end = memory.pointer(ring + 48)
    for bar_slot in range(bars_begin, bars_end, HIT_BAR_SLOT_STRIDE):
        node = memory.pointer(bar_slot)
        for _ in range(MAX_BAR_CHAIN_LENGTH):
            if node == bar_slot:
                break
            bar_config = memory.pointer(node + 24)
            print(
                "bar",
                memory.i32(bar_config + 8),
                memory.unpack(node + 48, "<6i"),
                "duration",
                memory.i32(bar_config + 0x4C),
            )
            node = memory.pointer(node)


def main():
    show_combat = "--combat" in sys.argv
    with ProcessMemory(first_client_pid()) as memory:
        decoder = ReferenceDecoder(memory)
        client = decoder.domain_capture("client")
        world_state = memory.pointer(client + WORLD_STATE_POINTER_OFFSET)
        manager = memory.pointer(world_state + NPC_MANAGER_OFFSET)
        table = memory.pointer(manager + 16)
        bucket_count = memory.unpack(manager + 24, "<Q")[0]
        count = memory.i32(manager + NPC_COUNT_OFFSET)
        manager_vtable_rva = memory.pointer(manager) - memory.module.base
        print("count", count, "manager vtable RVA", hex(manager_vtable_rva))
        if not 0 <= count <= 1024:
            raise RuntimeError("Invalid count")
        runtime_ids = []
        if count:
            runtime_ids = memory.unpack(manager + NPC_INDICES_OFFSET, "<" + "I" * count)
        for runtime_id in runtime_ids:
            node = memory.pointer(table + runtime_id % bucket_count * 8)
            for _ in range(MAX_CHAIN_LENGTH):
                if not node:
                    break
                if memory.u32(node) == runtime_id:
                    actor = memory.pointer(node + 16)
                    config = memory.pointer(actor + NPC_CONFIG_OVERRIDE_OFFSET) or memory.pointer(
                        actor + NPC_CONFIG_OFFSET
                    )
                    config_id = memory.i32(config + 8) if config else None
                    name = read_name(memory, actor)
                    print(
                        runtime_id,
                        hex(actor),
                        "config",
                        config_id,
                        "name",
                        repr(name),
                        "uid",
                        memory.unpack(actor + NPC_RUNTIME_ID_OFFSET, "<H")[0],
                    )
                    if show_combat and name in COMBAT_NAMES:
                        print_combat_details(memory, actor)
                    break
                node = memory.pointer(node + 24)


if __name__ == "__main__":
    main()
