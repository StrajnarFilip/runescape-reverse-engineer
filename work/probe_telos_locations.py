"""Read active scene locations around Telos on the pinned Compatibility Mode client."""

from __future__ import annotations

import argparse
import json
import math
import time

from workspace import add_import_paths

add_import_paths(package=True, reference=True)

from reference_decoder import load_matching_profile  # noqa: E402
from reference_memory import MemoryReadError, ProcessMemory  # noqa: E402
from rs3_reader.native import Reader, SpatialFlags  # noqa: E402

SUPPORTED_VERSION = "6ada3f59-1a86000"
TELOS_CONFIGS = frozenset({22891, 22892})
TELOS_STAGE_VARP = 6363
SCENE_MANAGER = 0x199E8
MANAGER_SCENES = 0x58
MANAGER_SCENE_INDEX = 0x70
MAP_BASE = 0x14714
MAP_SQUARE_ROWS = 0x14760
SQUARE_CHUNK_ROWS = 0xEB90
CHUNK_LOCATION_LISTS = (0xC0, 0x308, 0x320)
CHUNK_SECTION_LISTS = (0x100, 0x360)
SECTION_LOCATIONS = 0xA8
SECTION_LOCATION_SIZE = 0xC0
SECTION_LOCATION_POINTER = 0xA8
LOCATION_CONFIG_ID = 8
POSITIONAL_EFFECT_POOL = 0x19A50
EFFECT_POOL_STORAGE = 0x10
EFFECT_ENTRY_SIZE = 0xD0
EFFECT_ENTRY_VTABLE = 0x15B7DF8
EFFECT_CONFIG_VTABLE = 0x15B7D10
EFFECT_CONFIG = 0x20
EFFECT_CONFIG_ID = 0x50
EFFECT_POSITION = 0x5C
EFFECT_KIND = 0xC0
MAX_EFFECT_POOL_SIZE = 256
FINE_UNITS_PER_TILE = 512
LOCATION_LAYER_COUNT = 5
LOCATION_LAYER_STRIDE = 0x50
VECTOR_ROW_SIZE = 0x18
SHARED_REFERENCE_SIZE = 0x10
OBJECT_POINTER = 8
SQUARE_TILES = 64
CHUNK_TILES = 16
ARENA_RADIUS_TILES = 24
POLL_INTERVAL_SECONDS = 0.05

# LocEntity vtable -> (grid coordinate getter, config shared reference, optional
# sequence fields written by the scene location animation handler at RVA 0xCD2550).
LOCATION_LAYOUTS = {0x15B52D8: (0x170, 0xC8, 0x350), 0x15B5470: (0xA0, 0xB0, None)}


def vector(memory, owner, field, stride, capacity):
    """Check a client vector's bounds before following any entry."""
    start, end = memory.unpack(owner + field, "<QQ")
    if end < start or (end - start) % stride or end - start > capacity * stride:
        raise MemoryReadError("Unexpected scene vector bounds")
    return range(start, end, stride)


def loaded_chunks(memory, scene, center):
    """Follow the same map-square and chunk vectors as the client's LocList binding."""
    manager = memory.pointer(scene + SCENE_MANAGER)
    index = memory.i32(manager + MANAGER_SCENE_INDEX)
    if not 0 <= index < 32:
        raise MemoryReadError("Active scene index is unavailable")
    scenes = memory.pointer(manager + MANAGER_SCENES)
    world = memory.pointer(scenes + index * SHARED_REFERENCE_SIZE + OBJECT_POINTER)
    square_base_x, square_base_y = memory.unpack(world + MAP_BASE, "<ii")
    square_rows = vector(memory, world, MAP_SQUARE_ROWS, VECTOR_ROW_SIZE, 64)
    min_x, max_x = center[0] - ARENA_RADIUS_TILES, center[0] + ARENA_RADIUS_TILES
    min_y, max_y = center[1] - ARENA_RADIUS_TILES, center[1] + ARENA_RADIUS_TILES
    for square_x, row in enumerate(square_rows, square_base_x):
        if not min_x // SQUARE_TILES <= square_x <= max_x // SQUARE_TILES:
            continue
        for square_y, entry in enumerate(
            vector(memory, row, 0, VECTOR_ROW_SIZE, 64), square_base_y
        ):
            if not min_y // SQUARE_TILES <= square_y <= max_y // SQUARE_TILES:
                continue
            square = memory.pointer(entry + OBJECT_POINTER)
            if not square:
                continue
            chunk_rows = vector(memory, square, SQUARE_CHUNK_ROWS, VECTOR_ROW_SIZE, 4)
            for chunk_x, chunk_row in enumerate(chunk_rows, square_x * 4):
                if not min_x // CHUNK_TILES <= chunk_x <= max_x // CHUNK_TILES:
                    continue
                chunks = vector(memory, chunk_row, 0, SHARED_REFERENCE_SIZE, 4)
                for chunk_y, chunk_entry in enumerate(chunks, square_y * 4):
                    if min_y // CHUNK_TILES <= chunk_y <= max_y // CHUNK_TILES:
                        chunk = memory.pointer(chunk_entry + OBJECT_POINTER)
                        if chunk:
                            yield chunk


def location_references(memory, chunk):
    """Yield direct and section-backed locations from this chunk."""
    for field in CHUNK_LOCATION_LISTS:
        for entry in vector(memory, chunk, field, SHARED_REFERENCE_SIZE, 1024):
            yield memory.pointer(entry + OBJECT_POINTER)
    for base in CHUNK_SECTION_LISTS:
        for layer in range(LOCATION_LAYER_COUNT):
            field = base + layer * LOCATION_LAYER_STRIDE
            for entry in vector(memory, chunk, field, SHARED_REFERENCE_SIZE, 1024):
                section = memory.pointer(entry + OBJECT_POINTER)
                if not section:
                    continue
                for loc in vector(memory, section, SECTION_LOCATIONS, SECTION_LOCATION_SIZE, 2048):
                    yield memory.pointer(loc + SECTION_LOCATION_POINTER)


def observe_locations(memory, scene_rva, center):
    """Return decoded candidates, with no process addresses in the output."""
    module = memory.module.base
    scene = memory.pointer(module + scene_rva)
    references = {
        location
        for chunk in loaded_chunks(memory, scene, center)
        for location in location_references(memory, chunk)
        if location
    }
    records = set()
    unknown_types = set()
    for location in references:
        class_rva = memory.pointer(location) - module
        layout = LOCATION_LAYOUTS.get(class_rva)
        if layout is None:
            unknown_types.add(class_rva)
            continue
        grid_offset, config_ref, sequence_offset = layout
        plane, x, y = memory.unpack(location + grid_offset, "<iii")
        if plane != center[2] or max(abs(x - center[0]), abs(y - center[1])) > ARENA_RADIUS_TILES:
            continue
        config = memory.pointer(location + config_ref + OBJECT_POINTER)
        if config:
            sequence = memory.i32(location + sequence_offset) if sequence_offset else -1
            config_id = memory.i32(config + LOCATION_CONFIG_ID)
            records.add((class_rva, plane, x, y, config_id, sequence))
    effects = nearby_positional_effects(memory, scene, center)
    if memory.pointer(module + scene_rva) != scene:
        raise MemoryReadError("Scene changed while reading locations")
    return sorted(records), len(unknown_types), effects


def nearby_positional_effects(memory, scene, center):
    """Read the renderer's indexed effect pool as candidates for beam emitters."""
    pool = memory.pointer(scene + POSITIONAL_EFFECT_POOL)
    if not pool:
        return None
    header = memory.unpack(pool + EFFECT_POOL_STORAGE, "<QQQii")
    entries, indices, inverse, count, capacity = header
    if not 0 <= count <= capacity <= MAX_EFFECT_POOL_SIZE:
        raise MemoryReadError("Unexpected effect pool size")
    if not count:
        return []
    module = memory.module.base
    records = []
    for offset, slot in enumerate(memory.unpack(indices, f"<{count}i")):
        if not 0 <= slot < capacity or memory.i32(inverse + 4 * slot) != offset:
            raise MemoryReadError("Effect pool changed while reading active slots")
        effect = entries + EFFECT_ENTRY_SIZE * slot
        if memory.pointer(effect) != module + EFFECT_ENTRY_VTABLE:
            raise MemoryReadError("Unknown positional effect layout")
        fine_x, height, fine_z, plane = memory.unpack(effect + EFFECT_POSITION, "<fffi")
        if plane != center[2] or not all(map(math.isfinite, (fine_x, height, fine_z))):
            continue
        x, y = int(fine_x // FINE_UNITS_PER_TILE), int(fine_z // FINE_UNITS_PER_TILE)
        if max(abs(x - center[0]), abs(y - center[1])) > ARENA_RADIUS_TILES:
            continue
        config = memory.pointer(effect + EFFECT_CONFIG)
        if not config or memory.pointer(config) != module + EFFECT_CONFIG_VTABLE:
            continue
        config_id = memory.i32(config + EFFECT_CONFIG_ID)
        kind = memory.u32(effect + EFFECT_KIND)
        records.append((plane, x, y, config_id, kind))
    if memory.unpack(pool + EFFECT_POOL_STORAGE, "<QQQii") != header:
        raise MemoryReadError("Positional effect pool changed during read")
    return sorted(records)


def capture(pid, duration):
    """Correlate scene state changes with native attempt timestamps and received ticks."""
    with ProcessMemory(pid) as memory, Reader(pid, worker_hz=0) as reader:
        profile = load_matching_profile(memory.identity()["sha256"])
        if not profile or profile["version"] != SUPPORTED_VERSION:
            raise RuntimeError("Probe requires the pinned Compatibility Mode client")
        scene_rva = int(profile["player_position"]["client_ptr_rva"], 0)
        deadline = time.monotonic() + duration
        last_observation = None
        last_tick = None
        while time.monotonic() < deadline:
            info, _, npcs, _ = reader.sample()
            if info.process_creation_time != memory.creation_time:
                raise RuntimeError("Client process identity changed")
            encounter = reader.encounter
            tick = encounter.game_tick
            boss_positions = [
                (pose.tile.x, pose.tile.y, pose.tile.plane)
                for npc, pose in zip(npcs[: info.npc_count], reader.npc_spatial)
                if npc.config_id in TELOS_CONFIGS
                and pose.valid_mask & SpatialFlags.TILE
                and pose.valid_mask & SpatialFlags.SAME_WORLD
            ]
            if tick.valid and tick.counter != last_tick and len(boss_positions) == 1:
                last_tick = tick.counter
                packed = next(
                    (
                        raw.value
                        for raw in encounter.raw[: encounter.raw_count]
                        if raw.varp_id == TELOS_STAGE_VARP and raw.valid
                    ),
                    None,
                )
                try:
                    first = observe_locations(memory, scene_rva, boss_positions[0])
                    second = observe_locations(memory, scene_rva, boss_positions[0])
                    observation = first if first == second else None
                except MemoryReadError:
                    observation = None
                state = (observation, packed, boss_positions[0], reader.combat.scene_epoch)
                if state != last_observation:
                    print(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "game_tick": tick.counter,
                                "sample_ns": info.monotonic_ns,
                                "stage_meter_raw": packed,
                                "boss_tile": boss_positions[0],
                                "locations": observation[0] if observation is not None else None,
                                "unknown_layout_count": (
                                    observation[1] if observation is not None else None
                                ),
                                "positional_effects": (
                                    observation[2] if observation is not None else None
                                ),
                            }
                        ),
                        flush=True,
                    )
                    last_observation = state
            time.sleep(POLL_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--duration", type=int, default=140)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("Duration must be positive")
    capture(args.pid, args.duration)


if __name__ == "__main__":
    main()
