"""Record Telos's action cues and prayer changes for manual timing calibration."""

import argparse
from contextlib import nullcontext
import ctypes as C
from ctypes import wintypes
import json
import math
from pathlib import Path
import sys
import time

from native import Reader
from native_telemetry import combat_record


MELEE_AUTO_SEQUENCE = 28929
PRAYER_KEYS = {0x70: ("F1", "melee"), 0x71: ("F2", "soul_split")}
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt and the Windows keys.
POLL_SECONDS = 0.01


class ManualPrayerKeys:
    """Sample F1/F2 down edges while the verified game process is foreground."""

    def __init__(self):
        user32 = C.WinDLL("user32", use_last_error=True)
        self.foreground = user32.GetForegroundWindow
        self.foreground.argtypes = []
        self.foreground.restype = wintypes.HWND
        self.window_pid = user32.GetWindowThreadProcessId
        self.window_pid.argtypes = [wintypes.HWND, C.POINTER(wintypes.DWORD)]
        self.window_pid.restype = wintypes.DWORD
        self.key_state = user32.GetAsyncKeyState
        self.key_state.argtypes = [C.c_int]
        self.key_state.restype = C.c_short
        self.focus = None
        self.down = set()

    def sample(self, session, clock):
        pid, _, scene_epoch = session
        window = self.foreground()
        owner = wintypes.DWORD()
        if not (pid and scene_epoch and window and self.window_pid(window, C.byref(owner))
                and owner.value == pid):
            self.focus = None
            self.down.clear()
            return []

        down = {key for key in PRAYER_KEYS if self.key_state(key) < 0}
        modified = any(self.key_state(key) < 0 for key in MODIFIERS)
        observed_ns = clock()
        if self.foreground() != window:
            self.focus = None
            self.down.clear()
            return []

        focus = (session, window)
        # A held key on focus gain is a baseline. A release never emits a press.
        pressed = down - self.down if self.focus == focus else set()
        self.focus, self.down = focus, down
        if modified:
            return []
        return [{"key": PRAYER_KEYS[key][0], "bound_prayer": PRAYER_KEYS[key][1],
                 "observed_ns": observed_ns} for key in sorted(pressed)]


def telos_actions(events, session, sample_sequence, instances):
    return [
        {
            "event_sequence": event.sequence,
            "observed_ns": event.observed_ns,
            "instance_id": event.instance_id,
            "config_id": event.config_id,
            "action_sequence": event.action_sequence,
            "previous_action_sequence": event.previous_action_sequence,
            "client_logic_tick": event.logic_tick,
            "application_stamp": event.application_stamp if event.flags & 1 else None,
            "user_labeled_melee_auto": event.action_sequence == MELEE_AUTO_SEQUENCE,
        }
        for event in events
        if (event.pid, event.process_creation_time, event.scene_epoch) == session
        and event.sample_sequence == sample_sequence and event.instance_id in instances
    ]


def observe(reader, duration, out, enrage, keys):
    end = time.monotonic() + duration
    previous, last_write = None, 0
    while time.monotonic() < end:
        started = time.monotonic()
        # Synchronous sampling keeps each native action event with its snapshot.
        info, player, npcs, _ = reader.sample()
        combat = combat_record(reader)
        session = (info.pid, info.process_creation_time, combat["scene_epoch"])
        presses = keys.sample(session, reader.lib.rs3_monotonic_ns) if keys else []
        events, lost = reader.drain_npc_action_events()
        telos = []
        for i in range(info.npc_count):
            npc, animation = npcs[i], reader.animations[i]
            if npc.name.decode("utf-8").partition(",")[0].strip().casefold() == "telos":
                telos.append({"instance_id": npc.instance_id, "config_id": npc.config_id,
                              "action_sequence": animation.action_sequence if animation.valid else None})
        actions = telos_actions(events, session, info.sequence, {npc["instance_id"] for npc in telos})
        tile = reader.position
        state = {
            "pid": info.pid,
            "process_creation_time": info.process_creation_time,
            "executable_sha256": info.executable_sha256.decode("ascii"),
            "status": info.status,
            "reason": info.message.decode("utf-8") or None,
            "skipped_npcs": info.skipped_npcs,
            "combat": combat,
            "hp": player.raw_variables[0] if player.valid_variables & 1 else None,
            "telos_raw": player.raw_variables[9] if player.valid_variables & (1 << 9) else None,
            "position": [tile.x, tile.y, tile.plane] if tile.valid else None,
            "telos": telos,
        }
        if state != previous or actions or lost or presses or started - last_write >= 1:
            row = dict(state, enrage_label=enrage, sample_sequence=info.sequence,
                       sample_started_ns=info.monotonic_ns, sample_duration_ns=info.sample_duration_ns,
                       client_logic_tick=info.logic_tick, telos_actions=actions,
                       npc_action_events_lost=lost, manual_prayer_presses=presses)
            print(json.dumps(row), file=out, flush=True)
            previous, last_write = state, started
        time.sleep(max(0, started + POLL_SECONDS - time.monotonic()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="RuneScape process ID")
    parser.add_argument("--duration", type=float, default=300, help="Recording duration in seconds")
    parser.add_argument("--enrage", type=int, help="Manually entered enrage for this capture")
    parser.add_argument("--record-keys", action="store_true", help="Timestamp manual F1/F2 presses in RuneScape")
    parser.add_argument("--output", type=Path, help="Write a new local JSONL file instead of standard output")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be finite and positive")
    if args.enrage is not None and not 0 <= args.enrage <= 4000:
        parser.error("--enrage must be between 0 and 4000")
    try:
        keys = ManualPrayerKeys() if args.record_keys else None
        with Reader(pid=args.pid, worker_hz=0) as reader:
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
            output = args.output.open("x", encoding="utf-8") if args.output else nullcontext(sys.stdout)
            with output as out:
                print("Recording Telos prayer timing. Switch prayers manually.", file=sys.stderr, flush=True)
                observe(reader, args.duration, out, args.enrage, keys)
    except KeyboardInterrupt:
        return
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
