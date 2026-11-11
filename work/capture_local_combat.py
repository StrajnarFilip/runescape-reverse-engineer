"""Read-only calibration research: local hitmarks and configured prayer key edges.

Uses the independent reference decoder for the local player actor; production
acquisition stays in C. Hitmark amounts are not assumed to be damage. Only the
configured function keys are observed by default, and only in the foreground game
window. A caller may explicitly allow a fixed additional calibration binding.

Usage::

    python work/capture_local_combat.py --pid 1234 --output work/captures/combat.jsonl
"""

import argparse
import ctypes
import json
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path

from workspace import PROFILES_DIRECTORY, add_import_paths

add_import_paths(package=True, reference=True)

from reference_decoder import ReferenceDecoder, parse_number  # noqa: E402
from reference_entities import HIT_RECORD_BYTES, MAX_HITS_PER_NPC, decode_hit  # noqa: E402
from reference_memory import MemoryReadError, ProcessMemory  # noqa: E402
from rs3_reader.native import Reader, Variable  # noqa: E402
from rs3_reader.telemetry import combat_record  # noqa: E402

DEFAULT_DURATION_SECONDS = 900
POLL_SECONDS = 0.005
HEARTBEAT_SECONDS = 1

# Hit ring header layout shared with the NPC rings.
HIT_RING_HEADER_BYTES = 40
HIT_RING_CAPACITY_OFFSET = 4
HIT_RING_COUNT_OFFSET = 24
HIT_RING_COUNT_AND_DATA_BYTES = 16
HIT_RECORD_LAYOUT = "<6i"
SLOT_POINTER_BYTES = 8

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LEFT_WINDOWS, VK_RIGHT_WINDOWS = 0x5B, 0x5C
VK_F1, VK_F12 = 0x70, 0x7B
# Modifier bits use the rs3_set_prayer() encoding: Ctrl 1, Shift 2, Alt 4.
MODIFIER_BITS = ((VK_CONTROL, 1), (VK_SHIFT, 2), (VK_MENU, 4))


class LocalActorLayout:
    """Offsets of the local player actor from the "local_actor" profile object."""

    def __init__(self, profile):
        if "local_actor" not in profile:
            raise RuntimeError("Player-hit research layout is only checked for the primary profile")
        layout = {name: parse_number(value) for name, value in profile["local_actor"].items()}
        self.context_offset = layout["context_offset"]
        self.index_offset = layout["index_offset"]
        self.override_offset = layout["override_offset"]
        self.table_offset = layout["table_offset"]
        self.range_offset = layout["range_offset"]
        self.slot_entity_offset = layout["slot_entity_offset"]
        self.runtime_id_offset = layout["uid_offset"]
        self.vtable_rva = layout["vtable_rva"]
        self.hitmarks_offset = layout["hitmarks_offset"]
        self.game_state_offset = parse_number(profile["game_state_offset"])
        self.in_game_state = profile["in_game_state"]
        self.logic_tick_ms = profile["logic_tick_ms"]


def find_local_actor(memory, layout, world_state):
    """Return (player context, actor, player index), or None when unverified."""
    player_context = memory.pointer(world_state + layout.context_offset)
    if not player_context or memory.pointer(player_context) != world_state:
        return None
    player_index = memory.i32(player_context + layout.index_offset)
    actor = memory.pointer(player_context + layout.override_offset)
    if not actor:
        player_table = memory.pointer(world_state + layout.table_offset)
        slots_begin, slots_end = memory.unpack(player_table + layout.range_offset, "<QQ")
        if not 0 <= player_index < (slots_end - slots_begin) // SLOT_POINTER_BYTES:
            return None
        player_slot = memory.pointer(slots_begin + player_index * SLOT_POINTER_BYTES)
        actor = memory.pointer(player_slot + layout.slot_entity_offset)
    if (
        memory.pointer(actor) != memory.module.base + layout.vtable_rva
        or memory.unpack(actor + layout.runtime_id_offset, "<H")[0] != player_index
    ):
        return None
    return player_context, actor, player_index


def read_hit_ring(memory, ring, logic_tick, tick_ms):
    """Return the decoded records of a hit ring, or None when the ring is torn or invalid."""
    if not ring:
        return []
    header = memory.read(ring, HIT_RING_HEADER_BYTES)
    (capacity,) = struct.unpack_from("<I", header, HIT_RING_CAPACITY_OFFSET)
    count, data = struct.unpack_from("<QQ", header, HIT_RING_COUNT_OFFSET)
    if not count <= capacity <= MAX_HITS_PER_NPC:
        return None
    record_bytes = count * HIT_RECORD_BYTES
    records = memory.read(data, record_bytes) if count else b""
    records_again = memory.read(data, record_bytes) if count else b""
    count_and_data = memory.read(ring + HIT_RING_COUNT_OFFSET, HIT_RING_COUNT_AND_DATA_BYTES)
    header_tail = header[HIT_RING_COUNT_OFFSET:HIT_RING_HEADER_BYTES]
    if records != records_again or count_and_data != header_tail:
        return None
    hits = []
    for slot in range(count):
        values = struct.unpack_from(HIT_RECORD_LAYOUT, records, slot * HIT_RECORD_BYTES)
        hit = decode_hit(values, logic_tick, tick_ms)
        if hit is not None:
            hits.append(dict(hit, slot=slot))
    return hits


def local_hits(memory, layout, world_state, logic_tick):
    """Return the local player's index and hitmarks, or None when unverified or torn."""
    if memory.i32(world_state + layout.game_state_offset) != layout.in_game_state:
        return None
    resolved = find_local_actor(memory, layout, world_state)
    if resolved is None:
        return None
    player_context, actor, player_index = resolved
    ring = memory.pointer(actor + layout.hitmarks_offset)
    hits = read_hit_ring(memory, ring, logic_tick, layout.logic_tick_ms)
    # The actor, its ring and the context must be unchanged after the read.
    if (
        hits is None
        or memory.pointer(actor + layout.hitmarks_offset) != ring
        or memory.pointer(player_context) != world_state
        or memory.unpack(actor + layout.runtime_id_offset, "<H")[0] != player_index
    ):
        return None
    return {"player_index": player_index, "hitmarks": hits}


class PrayerKeys:
    """Sample configured chords in the foreground game, with an explicit key allowlist.

    Function keys are accepted by default. Other keys must be declared by the
    caller in additional_virtual_keys and still match their complete chord.
    """

    def __init__(self, pid, bindings, *, additional_virtual_keys=()):
        self.pid = pid
        self.bindings = bindings
        for binding in bindings.values():
            key = binding["virtual_key"]
            if not VK_F1 <= key <= VK_F12 and key not in additional_virtual_keys:
                raise ValueError(
                    "Calibration records function keys and explicitly allowed keys only"
                )
        self.previous_keys = set()
        self.was_focused = False
        self.last_poll_ns = 0
        user32 = ctypes.WinDLL("user32")
        self.get_foreground_window = user32.GetForegroundWindow
        self.get_foreground_window.restype = wintypes.HWND
        self.get_window_thread_process_id = user32.GetWindowThreadProcessId
        self.get_window_thread_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.get_window_thread_process_id.restype = wintypes.DWORD
        self.get_async_key_state = user32.GetAsyncKeyState
        self.get_async_key_state.argtypes = [ctypes.c_int]
        self.get_async_key_state.restype = ctypes.c_short

    def key_held(self, virtual_key):
        return self.get_async_key_state(virtual_key) < 0

    def game_focused(self):
        window = self.get_foreground_window()
        owner_pid = wintypes.DWORD()
        return bool(
            window
            and self.get_window_thread_process_id(window, ctypes.byref(owner_pid))
            and owner_pid.value == self.pid
        )

    def sample(self, clock):
        """Return the configured chords first pressed since the previous sample."""
        focused = self.game_focused()
        modifiers = sum(bit for key, bit in MODIFIER_BITS if self.key_held(key))
        held_keys = set()
        if focused:
            held_keys = {
                binding["virtual_key"]
                for binding in self.bindings.values()
                if self.key_held(binding["virtual_key"])
            }
        now_ns = clock()
        # A key held when focus is gained is a baseline, not a press.
        pressed_keys = held_keys - self.previous_keys if focused and self.was_focused else set()
        windows_key_held = self.key_held(VK_LEFT_WINDOWS) or self.key_held(VK_RIGHT_WINDOWS)
        presses = [
            {
                "prayer": name,
                "observed_ns": now_ns,
                "previous_poll_ns": self.last_poll_ns,
                "virtual_key": binding["virtual_key"],
                "modifiers": modifiers,
            }
            for name, binding in sorted(self.bindings.items())
            if binding["virtual_key"] in pressed_keys
            and binding["modifiers"] == modifiers
            and not windows_key_held
        ]
        self.previous_keys, self.was_focused, self.last_poll_ns = held_keys, focused, now_ns
        return presses


def load_bindings():
    settings_path = PROFILES_DIRECTORY / "prayer_flick.json"
    return json.loads(settings_path.read_text(encoding="utf-8"))["bindings"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--duration", default=DEFAULT_DURATION_SECONDS, type=float)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with (
        ProcessMemory(arguments.pid) as memory,
        Reader(pid=arguments.pid, worker_hz=100) as reader,
        arguments.output.open("x", encoding="utf-8") as output,
    ):
        decoder = ReferenceDecoder(memory)
        layout = LocalActorLayout(decoder.profile)
        state_pointer_offset = decoder.profile["state_pointer_offset"]
        keys = PrayerKeys(arguments.pid, load_bindings())
        deadline = time.monotonic() + arguments.duration
        previous_signature, last_write = None, 0
        print(
            "Recording local hitmarks and configured prayer keys; no input sent.",
            file=sys.stderr,
            flush=True,
        )
        while time.monotonic() < deadline:
            read_begin_ns = reader.monotonic_ns()
            info, player, _, _ = reader.sample()
            presses = keys.sample(reader.monotonic_ns)
            reason, local_player = None, None
            try:
                client = decoder.domain_capture("client")
                world_state = memory.pointer(client + state_pointer_offset)
                local_player = local_hits(memory, layout, world_state, info.logic_tick)
            except (MemoryReadError, OSError, ValueError, struct.error) as error:
                reason = type(error).__name__
            payload = {
                "pid": info.pid,
                "process_creation_time": info.process_creation_time,
                "scene_epoch": reader.combat.scene_epoch,
                "combat": combat_record(reader),
                "hp": player.variable(Variable.HEALTH),
                "local_player": local_player,
                "reason": reason,
            }
            signature = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            if presses or signature != previous_signature or now - last_write >= HEARTBEAT_SECONDS:
                payload.update(
                    source="reference_decoder_research_and_native_snapshot",
                    read_begin_ns=read_begin_ns,
                    read_end_ns=reader.monotonic_ns(),
                    sample_observed_ns=info.monotonic_ns,
                    sequence=info.sequence,
                    client_logic_tick=info.logic_tick,
                    manual_prayer_keys=presses,
                )
                print(json.dumps(payload), file=output, flush=True)
                previous_signature, last_write = signature, now
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
