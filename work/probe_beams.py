"""Snapshot arena coordinates in client memory at operator-marked beam states (read-only).

Telos beams are not NPCs, so their scene container is unmapped. This research tool records,
whenever the operator presses a marker key, every location in the client's private read-write
memory that holds a coordinate near Telos: float fine positions laid out as (x, height, z) and
int32 tile pairs laid out as (x, y). Comparing snapshots taken with the beam visible, gone and
moved isolates the structures that follow the beam. Nothing is written to the client.

Markers are global key presses, so RuneScape may stay focused. The default keys must be
unbound in the game. Output (ignored by git) goes to work/beam_research/<session>/: one
compressed .npz per snapshot and an index.jsonl with the label, time and DLL tiles.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import numpy

from workspace import WORK_DIRECTORY, add_import_paths

add_import_paths(package=True, reference=True)

from reference_memory import MAX_READ_BYTES, ProcessMemory  # noqa: E402
from rs3_reader.native import Reader  # noqa: E402
from rs3_reader.native import SpatialFlags  # noqa: E402

ACTIVE_TELOS = 22891
DORMANT_TELOS = 22892
FINE_UNITS_PER_TILE = 512
# Arena half-width in tiles around Telos; the P1/P2 platform fits well inside this square.
ARENA_RADIUS_TILES = 48
# Plausible fine height range; excludes unrelated floats that happen to match x and z.
MAX_FINE_HEIGHT = 65536.0
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40
KEY_POLL_SECONDS = 0.02
KEY_DOWN_MASK = 0x8000
VIRTUAL_KEYS = {"F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B, "PAUSE": 0x13}
OUTPUT_DIRECTORY = WORK_DIRECTORY / "beam_research"
READ_THREADS = 16
TELOS_METER_VARP = 6363
METER_MASK = (1 << 21) - 1
FLAT_TICKS_TO_STOP = 2
MIN_FEEDING_TICKS = 3
MAX_AUTOMATIC_SNAPSHOTS = 40
# Byte windows are saved around fine positions this far from Telos (the platform edge).
EDGE_MIN_TILES = 8
EDGE_MAX_TILES = 25
CONTEXT_BYTES = 0x300
MAX_CONTEXTS = 30000
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_read_process_memory = _kernel32.ReadProcessMemory
_read_process_memory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_read_process_memory.restype = wintypes.BOOL
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short


def key_down(virtual_key):
    """Return whether the key is currently held, from the global async key state."""
    return bool(_user32.GetAsyncKeyState(virtual_key) & KEY_DOWN_MASK)


def telos_and_player_tiles(reader):
    """Return (Telos tile, player tile) from a fresh DLL sample; either may be None."""
    info, _player, npcs, _ = reader.sample()
    telos_ids = {
        npc.instance_id
        for npc in npcs[: info.npc_count]
        if npc.config_id in (ACTIVE_TELOS, DORMANT_TELOS)
    }
    telos_tile = None
    for pose in reader.npc_spatial[: reader.spatial.npc_count]:
        if pose.instance_id in telos_ids and pose.valid_mask & SpatialFlags.TILE:
            telos_tile = (pose.tile.x, pose.tile.y, pose.tile.plane)
    position = reader.position
    player_tile = (position.x, position.y, position.plane) if position.valid else None
    return telos_tile, player_tile


def meter_and_tick(reader):
    """Return (Telos anima meter, received tick) from the latest sample; either may be None.

    The meter is the low 21 bits of VARP 6363 (bits 21 and up hold the phase). It rises by 3
    per received tick while a beam feeds Telos.
    """
    encounter = reader.encounter
    meter = None
    for raw in encounter.raw[: encounter.raw_count]:
        if raw.varp_id == TELOS_METER_VARP and raw.valid:
            meter = raw.value & METER_MASK
    tick = encounter.game_tick.counter if encounter.game_tick.valid else None
    return meter, tick


class MeterWatcher:
    """Turn per-tick meter readings into beam start/stop markers.

    Feeding starts on the first rising tick; it stops after FLAT_TICKS_TO_STOP flat (or reset)
    ticks, and is reported only after at least MIN_FEEDING_TICKS rises.
    """

    def __init__(self):
        self.last_tick = None
        self.last_meter = None
        self.feeding = False
        self.rising_ticks = 0
        self.flat_ticks = 0

    def update(self, meter, tick):
        """Return 'meter_rising', 'meter_stopped' or None for a new tick."""
        if meter is None or tick is None or tick == self.last_tick:
            return None
        previous = self.last_meter
        self.last_tick, self.last_meter = tick, meter
        if previous is None:
            return None
        if meter > previous:
            self.rising_ticks += 1
            self.flat_ticks = 0
            if not self.feeding:
                self.feeding = True
                return "meter_rising"
            return None
        self.flat_ticks += 1
        if self.feeding and self.flat_ticks >= FLAT_TICKS_TO_STOP:
            fed = self.rising_ticks
            self.feeding = False
            self.rising_ticks = 0
            if fed >= MIN_FEEDING_TICKS:
                return "meter_stopped"
        return None


def writable_private_regions(memory):
    """Yield (base, size) of committed private read-write regions."""
    for base, size, kind, protection in memory.regions():
        if kind == MEM_PRIVATE and protection & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE):
            yield base, size


def in_range(words, low, span):
    """Unsigned range test with one subtraction: low <= word <= low + span."""
    return (words - numpy.uint32(low)) <= numpy.uint32(span)


def float_bit_range(low, high):
    """Positive float32 bounds as (low bits, span); positive floats order like their bits."""
    low_bits = int(numpy.float32(low).view(numpy.uint32))
    high_bits = int(numpy.float32(high).view(numpy.uint32))
    return low_bits, high_bits - low_bits


def scan_chunk(base, data, fine_bounds, tile_bounds):
    """Return (float hits, int hits) as arrays of [address, a, b, c] rows for one chunk.

    Only the first coordinate is tested over the whole chunk; the rest are checked on the
    resulting candidates, which keeps the full-memory pass to a few array operations.
    """
    usable = len(data) - len(data) % 4
    words = numpy.frombuffer(data, dtype="<u4", count=usable // 4)
    floats = words.view("<f4")
    integers = words.view("<i4")
    (x_low, x_span), (z_low, z_span) = (float_bit_range(*bounds) for bounds in fine_bounds)
    float_index = numpy.nonzero(in_range(words[:-2], x_low, x_span))[0]
    float_index = float_index[in_range(words[float_index + 2], z_low, z_span)]
    height = floats[float_index + 1]
    float_index = float_index[(height >= -MAX_FINE_HEIGHT) & (height <= MAX_FINE_HEIGHT)]
    # Adjacent (x, z) float pairs, for layouts without a height between the coordinates.
    pair_index = numpy.nonzero(in_range(words[:-1], x_low, x_span))[0]
    pair_index = pair_index[in_range(words[pair_index + 1], z_low, z_span)]
    (tile_x_low, tile_x_high), (tile_y_low, tile_y_high) = tile_bounds
    int_index = numpy.nonzero(in_range(words[:-1], tile_x_low, tile_x_high - tile_x_low))[0]
    int_index = int_index[in_range(words[int_index + 1], tile_y_low, tile_y_high - tile_y_low)]
    float_rows = numpy.column_stack(
        (
            base + float_index.astype(numpy.uint64) * 4,
            floats[float_index].astype(numpy.float64),
            floats[float_index + 1].astype(numpy.float64),
            floats[float_index + 2].astype(numpy.float64),
        )
    )
    # Pair rows carry NaN in the height column so analysis can tell the two layouts apart.
    pair_rows = numpy.column_stack(
        (
            base + pair_index.astype(numpy.uint64) * 4,
            floats[pair_index].astype(numpy.float64),
            numpy.full(len(pair_index), numpy.nan),
            floats[pair_index + 1].astype(numpy.float64),
        )
    )
    float_rows = numpy.concatenate((float_rows, pair_rows))
    int_rows = numpy.column_stack(
        (
            base + int_index.astype(numpy.uint64) * 4,
            integers[int_index].astype(numpy.float64),
            integers[int_index + 1].astype(numpy.float64),
            numpy.zeros(len(int_index)),
        )
    )
    return float_rows, int_rows


def edge_contexts(address, buffer, float_rows, center):
    """Return (addresses, byte windows) around fine positions at the arena edge.

    The beam emitters are permanent rings at the platform edge, so their objects exist whether
    or not they fire. Saving the bytes around each edge position lets analysis find the field
    that changes when one starts feeding Telos.
    """
    rows = float_rows[~numpy.isnan(float_rows[:, 2])]
    tiles = numpy.column_stack((rows[:, 1], rows[:, 3])) / FINE_UNITS_PER_TILE
    distance = numpy.hypot(tiles[:, 0] - center[0], tiles[:, 1] - center[1])
    rows = rows[(distance >= EDGE_MIN_TILES) & (distance <= EDGE_MAX_TILES)]
    addresses, windows = [], []
    for hit in rows[:, 0].astype(numpy.uint64):
        start = int(hit) - address - CONTEXT_BYTES
        window = numpy.zeros(2 * CONTEXT_BYTES, dtype=numpy.uint8)
        low, high = max(start, 0), min(start + 2 * CONTEXT_BYTES, len(buffer))
        if high > low:
            window[low - start : high - start] = buffer[low:high]
        addresses.append(int(hit))
        windows.append(window)
    return addresses, windows


def snapshot(memory, telos_tile, threads=None, contexts=False):
    """Scan private writable memory once.

    Return (float hits, int hits, context addresses, context windows, bytes read, seconds).
    """
    center_x, center_y = telos_tile[0], telos_tile[1]
    tile_bounds = (
        (center_x - ARENA_RADIUS_TILES, center_x + ARENA_RADIUS_TILES),
        (center_y - ARENA_RADIUS_TILES, center_y + ARENA_RADIUS_TILES),
    )
    fine_bounds = tuple(
        (low * FINE_UNITS_PER_TILE, (high + 1) * FINE_UNITS_PER_TILE) for low, high in tile_bounds
    )
    started = time.perf_counter()
    tasks = [
        (base + offset, min(MAX_READ_BYTES, size - offset))
        for base, size in writable_private_regions(memory)
        for offset in range(0, size, MAX_READ_BYTES)
    ]

    def read_and_scan(task):
        address, length = task
        buffer = numpy.empty(length, dtype=numpy.uint8)
        transferred = ctypes.c_size_t()
        if not _read_process_memory(
            memory.handle, address, buffer.ctypes.data, length, ctypes.byref(transferred)
        ):
            return None
        if transferred.value != length:
            return None
        float_rows, int_rows = scan_chunk(address, buffer, fine_bounds, tile_bounds)
        context = (
            edge_contexts(address, buffer, float_rows, (center_x, center_y))
            if contexts
            else ([], [])
        )
        return length, float_rows, int_rows, context

    # ReadProcessMemory on this client is page-fault bound (~85 MB/s on one thread);
    # parallel reads cover the 2.7 GB private heap in about 4.4 s.
    float_parts, int_parts, bytes_read = [], [], 0
    context_addresses, context_windows = [], []
    with ThreadPoolExecutor(threads or READ_THREADS) as pool:
        for result in pool.map(read_and_scan, tasks):
            if result is None:
                continue
            length, float_rows, int_rows, (addresses, windows) = result
            bytes_read += length
            if len(float_rows):
                float_parts.append(float_rows)
            if len(int_rows):
                int_parts.append(int_rows)
            room = MAX_CONTEXTS - len(context_addresses)
            context_addresses.extend(addresses[:room])
            context_windows.extend(windows[:room])
    empty = numpy.zeros((0, 4))
    float_hits = numpy.concatenate(float_parts) if float_parts else empty
    int_hits = numpy.concatenate(int_parts) if int_parts else empty
    windows = (
        numpy.stack(context_windows)
        if context_windows
        else numpy.zeros((0, 2 * CONTEXT_BYTES), dtype=numpy.uint8)
    )
    return (
        float_hits,
        int_hits,
        numpy.array(context_addresses, dtype=numpy.uint64),
        windows,
        bytes_read,
        time.perf_counter() - started,
    )


def main():
    """Wait for marker keys and record one memory snapshot per press."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=int, default=900, help="Maximum session seconds")
    parser.add_argument("--visible-key", default="F9", choices=sorted(VIRTUAL_KEYS))
    parser.add_argument("--gone-key", default="F10", choices=sorted(VIRTUAL_KEYS))
    parser.add_argument("--standing-key", default="F11", choices=sorted(VIRTUAL_KEYS))
    parser.add_argument("--stop-key", default="PAUSE", choices=sorted(VIRTUAL_KEYS))
    parser.add_argument("--baseline", action="store_true", help="Take one snapshot at start")
    parser.add_argument(
        "--auto-meter",
        action="store_true",
        help="Also snapshot when the Telos anima meter (VARP 6363) starts or stops rising",
    )
    parser.add_argument("--threads", type=int, default=READ_THREADS, help="Parallel readers")
    parser.add_argument(
        "--low-priority",
        action="store_true",
        help="Run below normal priority, e.g. alongside the practice controller",
    )
    parser.add_argument(
        "--edge-contexts",
        action="store_true",
        help="Save byte windows around fine positions 8-25 tiles from Telos",
    )
    arguments = parser.parse_args()
    if arguments.low_priority:
        _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    labels = {
        VIRTUAL_KEYS[arguments.visible_key]: "beam_visible",
        VIRTUAL_KEYS[arguments.gone_key]: "beam_gone",
        VIRTUAL_KEYS[arguments.standing_key]: "standing_in_beam",
    }
    stop_key = VIRTUAL_KEYS[arguments.stop_key]
    if len(set(labels) | {stop_key}) != len(labels) + 1:
        parser.error("Marker and stop keys must be distinct")
    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = OUTPUT_DIRECTORY / session
    directory.mkdir(parents=True, exist_ok=False)
    index_path = directory / "index.jsonl"
    print(f"Session {directory}", flush=True)
    reader = Reader(pid=arguments.pid, worker_hz=0)
    with (
        ProcessMemory(arguments.pid) as memory,
        index_path.open("x", encoding="utf-8") as index,
    ):
        identity = (memory.pid, memory.creation_time)
        pending = ["baseline"] if arguments.baseline else []
        held = set()
        last_center = None
        deadline = time.monotonic() + arguments.duration
        count = 0
        automatic = 0
        watcher = MeterWatcher()
        while time.monotonic() < deadline:
            if key_down(stop_key):
                break
            for virtual_key, label in labels.items():
                if key_down(virtual_key):
                    if virtual_key not in held:
                        held.add(virtual_key)
                        pending.append(label)
                else:
                    held.discard(virtual_key)
            if arguments.auto_meter and not pending and automatic < MAX_AUTOMATIC_SNAPSHOTS:
                reader.sample()
                marker = watcher.update(*meter_and_tick(reader))
                if marker:
                    automatic += 1
                    pending.append(marker)
            if not pending:
                time.sleep(KEY_POLL_SECONDS)
                continue
            label = pending.pop(0)
            if not memory.alive() or (memory.pid, memory.creation_time) != identity:
                raise RuntimeError("Client process changed")
            telos_tile, player_tile = telos_and_player_tiles(reader)
            if telos_tile is None and player_tile is None:
                # A reader attached across a scene load can stay without tiles; reattach once.
                reader.close()
                reader = Reader(pid=arguments.pid, worker_hz=0)
                telos_tile, player_tile = telos_and_player_tiles(reader)
            # The arena square is centred on Telos, else on the player, else the last centre.
            center = telos_tile or player_tile or last_center
            if center is None:
                print(json.dumps({"skipped": label, "reason": "No Telos or player tile"}))
                continue
            last_center = center
            marked_ns = time.monotonic_ns()
            meter, tick = meter_and_tick(reader)
            float_hits, int_hits, context_addresses, context_windows, bytes_read, seconds = (
                snapshot(memory, center, arguments.threads, arguments.edge_contexts)
            )
            after_telos, after_player = telos_and_player_tiles(reader)
            count += 1
            name = f"{count:03d}-{label}"
            numpy.savez_compressed(
                directory / f"{name}.npz",
                floats=float_hits,
                ints=int_hits,
                context_addresses=context_addresses,
                context_windows=context_windows,
            )
            record = {
                "snapshot": name,
                "label": label,
                "marked_monotonic_ns": marked_ns,
                "utc": datetime.now(timezone.utc).isoformat(),
                "scan_center": center,
                "meter": meter,
                "received_tick": tick,
                "telos_tile": telos_tile,
                "player_tile": player_tile,
                "telos_tile_after": after_telos,
                "player_tile_after": after_player,
                "float_hits": len(float_hits),
                "int_hits": len(int_hits),
                "edge_contexts": len(context_addresses),
                "bytes_scanned": bytes_read,
                "scan_seconds": round(seconds, 3),
            }
            index.write(json.dumps(record) + "\n")
            index.flush()
            print(json.dumps(record), flush=True)
    reader.close()
    print(f"Stopped after {count} snapshots", flush=True)


if __name__ == "__main__":
    main()
