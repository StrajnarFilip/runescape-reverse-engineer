"""Log every change to the player (varp) and client (varc) variable tables (read-only).

The Telos beams are not NPCs and were not found as world coordinates in heap scans. The
server may instead send the active emitter as a variable. This logger walks both sparse
variable tables of the Compatibility Mode client several times per tick and writes each
changed entry with its received tick and the Telos anima meter (VARP 6363 low bits), so
changes can be correlated with beams starting and stopping.

Values are also decoded as packed coordinates ((plane << 28) | (x << 14) | y) and flagged
when they fall near the player, since a beam origin may be sent that way. Output (ignored
by git): work/variable_logs/<UTC time>.jsonl.
"""

from __future__ import annotations

import argparse
import json
import struct
import time
from datetime import datetime, timezone

from workspace import WORK_DIRECTORY, add_import_paths

add_import_paths(package=True, reference=True)

from reference_memory import MemoryReadError, ProcessMemory  # noqa: E402
from rs3_reader.native import Reader  # noqa: E402

COMPATIBILITY_SHA256 = "7a60c69fcf11eda42a328732721f5e89e1c85a6bc919038ff3451d4643e42dbf"
# Layouts from build/generated/profile_generated.h (Compatibility Mode 6ada3f59-1a86000).
REGISTRY_RVA = 0x16BEF20
REGISTRY_ROOT_OFFSET = 0x10
TREE_KEY_OFFSET = 0x20
TREE_RIGHT_OFFSET = 0x0
TREE_LEFT_OFFSET = 0x8
FUNCTION_POINTER_OFFSET = 0x60
FUNCTION_CAPTURE_OFFSET = 0x8
HANDLER_VTABLE_OFFSET = 16
DOMAINS = {
    "varp": {"id": 0x3D, "table_offset": 0x1C0C0, "handler_rva": 0x4818F0},
    "varc": {"id": 0x39, "table_offset": 0x7308, "handler_rva": 0x4819E0},
}
NODE_KEY_OFFSET = 0x0
NODE_VALUE_OFFSET = 0x8
NODE_TYPE_OFFSET = 0x20
NODE_NEXT_OFFSET = 0x28
NODE_BYTES = 0x30
TYPE_INT32 = 0
TYPE_INT64 = 1
MAX_REGISTRY_DEPTH = 64
MAX_BUCKETS = 1_000_000
MAX_CHAIN_LENGTH = 1024
TELOS_METER_VARP = 6363
METER_MASK = (1 << 21) - 1
COORDINATE_X_SHIFT = 14
COORDINATE_MASK = 0x3FFF
COORDINATE_PLANE_SHIFT = 28
NEAR_PLAYER_TILES = 40
POLL_SECONDS = 0.15
OUTPUT_DIRECTORY = WORK_DIRECTORY / "variable_logs"


def domain_context(memory, domain):
    """Resolve a registered domain context after checking its callback handler."""
    base = memory.module.base
    node = memory.pointer(base + REGISTRY_RVA + REGISTRY_ROOT_OFFSET)
    for _ in range(MAX_REGISTRY_DEPTH):
        if not node:
            break
        key = memory.i32(node + TREE_KEY_OFFSET)
        if key == domain["id"]:
            function_object = memory.pointer(node + FUNCTION_POINTER_OFFSET)
            handler = memory.pointer(memory.pointer(function_object) + HANDLER_VTABLE_OFFSET)
            if handler != base + domain["handler_rva"]:
                raise MemoryReadError("Domain handler does not match the profile")
            return memory.pointer(function_object + FUNCTION_CAPTURE_OFFSET)
        offset = TREE_RIGHT_OFFSET if key < domain["id"] else TREE_LEFT_OFFSET
        node = memory.pointer(node + offset)
    raise MemoryReadError("Domain unavailable")


def read_table(memory, context, table_offset):
    """Return {variable id: value} for every numeric node in one sparse table."""
    buckets, count = memory.unpack(context + table_offset, "<QQ")
    if not buckets or not 1 <= count <= MAX_BUCKETS:
        raise MemoryReadError("Variable table unavailable")
    raw = memory.read(buckets, (count + 1) * 8)
    heads = struct.unpack(f"<{count + 1}Q", raw)
    sentinel = heads[-1]
    values = {}
    for head in heads[:-1]:
        node, steps = head, 0
        while node and node != sentinel and steps < MAX_CHAIN_LENGTH:
            data = memory.read(node, NODE_BYTES)
            key = struct.unpack_from("<I", data, NODE_KEY_OFFSET)[0]
            kind = data[NODE_TYPE_OFFSET]
            if kind == TYPE_INT32:
                values[key] = struct.unpack_from("<i", data, NODE_VALUE_OFFSET)[0]
            elif kind == TYPE_INT64:
                values[key] = struct.unpack_from("<q", data, NODE_VALUE_OFFSET)[0]
            node = struct.unpack_from("<Q", data, NODE_NEXT_OFFSET)[0]
            steps += 1
    return values


def packed_coordinate(value, player_tile):
    """Return (x, y, plane) when value decodes to a coordinate near the player, else None."""
    if player_tile is None or value <= 0 or value >= 1 << 30:
        return None
    x = (value >> COORDINATE_X_SHIFT) & COORDINATE_MASK
    y = value & COORDINATE_MASK
    plane = value >> COORDINATE_PLANE_SHIFT
    near = (
        abs(x - player_tile[0]) <= NEAR_PLAYER_TILES
        and abs(y - player_tile[1]) <= NEAR_PLAYER_TILES
    )
    return (x, y, plane) if near else None


def main():
    """Poll both tables until the duration ends and log every changed entry."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=int, default=600)
    arguments = parser.parse_args()
    OUTPUT_DIRECTORY.mkdir(exist_ok=True)
    path = OUTPUT_DIRECTORY / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".jsonl")
    print(f"Log {path}", flush=True)
    with (
        ProcessMemory(arguments.pid) as memory,
        Reader(pid=arguments.pid, worker_hz=0) as reader,
        path.open("x", encoding="utf-8") as output,
    ):
        if memory.identity()["sha256"] != COMPATIBILITY_SHA256:
            raise SystemExit("Only the Compatibility Mode executable is supported")
        previous = {name: None for name in DOMAINS}
        deadline = time.monotonic() + arguments.duration
        changes = polls = 0
        while time.monotonic() < deadline:
            reader.sample()
            tick = reader.encounter.game_tick
            position = reader.position
            player_tile = (position.x, position.y) if position.valid else None
            row_time = time.monotonic_ns()
            for name, domain in DOMAINS.items():
                try:
                    values = read_table(
                        memory, domain_context(memory, domain), domain["table_offset"]
                    )
                except MemoryReadError:
                    continue
                meter = values.get(TELOS_METER_VARP) if name == "varp" else None
                last = previous[name]
                if last is not None:
                    for key in set(values) | set(last):
                        if values.get(key) != last.get(key):
                            changes += 1
                            record = {
                                "t_ns": row_time,
                                "tick": tick.counter if tick.valid else None,
                                "table": name,
                                "id": key,
                                "old": last.get(key),
                                "new": values.get(key),
                                "player": player_tile,
                                "meter": None if meter is None else meter & METER_MASK,
                            }
                            coordinate = packed_coordinate(values.get(key) or 0, player_tile)
                            if coordinate:
                                record["coordinate"] = coordinate
                            output.write(json.dumps(record) + "\n")
                else:
                    snapshot = {
                        "t_ns": row_time,
                        "table": name,
                        "initial_count": len(values),
                        "coordinates": {
                            key: packed_coordinate(value, player_tile)
                            for key, value in values.items()
                            if packed_coordinate(value, player_tile)
                        },
                    }
                    output.write(json.dumps(snapshot) + "\n")
                    print(json.dumps({"table": name, "entries": len(values)}), flush=True)
                previous[name] = values
            polls += 1
            output.flush()
            time.sleep(POLL_SECONDS)
    print(json.dumps({"polls": polls, "changes": changes}), flush=True)


if __name__ == "__main__":
    main()
