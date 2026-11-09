"""Record Telos prayer timing or try repeated experimental F1/F2 switches."""

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
from prayer import Prayer


MELEE_AUTO_SEQUENCE = 28929
PRAYER_KEYS = {0x70: ("F1", Prayer.MELEE), 0x71: ("F2", Prayer.SOUL_SPLIT)}
TRIAL_KEYS = {prayer: key for key, (_, prayer) in PRAYER_KEYS.items()}
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt and the Windows keys.
POLL_SECONDS = 0.01
MELEE_HOLD_NS = 600_000_000
MAX_AGE_NS = 100_000_000
SOUL_SPLIT_ACK_NS = 1_200_000_000


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

    def game_window(self, session):
        pid, _, scene_epoch = session
        window = self.foreground()
        owner = wintypes.DWORD()
        if (pid and scene_epoch and window and self.window_pid(window, C.byref(owner))
                and owner.value == pid):
            return window
        return None

    def sample(self, session, clock):
        window = self.game_window(session)
        if window is None:
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
        return [{"key": PRAYER_KEYS[key][0], "bound_prayer": PRAYER_KEYS[key][1].name.lower(),
                 "observed_ns": observed_ns} for key in sorted(pressed)]


class PrayerTrial:
    """Re-arm after Soul Split is observed. A key needs a newer sample to acknowledge it."""

    def __init__(self, send):
        self.send = send
        self.target = None
        self.release_ns = 0
        self.pending = None
        self.sent_ns = 0
        self.completed_cycles = 0

    def finish_cycle(self):
        self.target = None
        self.completed_cycles += 1

    def press(self, prayer):
        self.sent_ns = self.send(prayer)
        self.pending = prayer
        return {"action": "sent", "prayer": prayer.name.lower(), "observed_ns": self.sent_ns}

    def step(self, target, active, sampled_ns, now, cue_ns=None, lost=0):
        if self.target is None and (target is None or cue_ns is None):
            return []
        if target is None or (self.target is not None and target != self.target):
            raise RuntimeError("Trial stopped because the Telos instance, scene or foreground window changed.")
        if lost or not 0 <= now - sampled_ns <= MAX_AGE_NS:
            raise RuntimeError("Trial stopped because the event stream or snapshot is stale.")
        if active == Prayer.UNKNOWN:
            raise RuntimeError("Trial stopped because the active overhead is unknown.")

        if cue_ns is not None:
            if not 0 <= now - cue_ns <= MAX_AGE_NS:
                raise RuntimeError("Trial stopped because the melee cue is stale.")
            self.release_ns = cue_ns + MELEE_HOLD_NS

        updates = []
        if self.pending is not None:
            if sampled_ns > self.sent_ns and active == self.pending:
                updates.append({"action": "acknowledged", "prayer": active.name.lower(),
                                "observed_ns": sampled_ns})
                self.pending = None
                if active == Prayer.SOUL_SPLIT:
                    self.finish_cycle()
                    if cue_ns is None:
                        return updates
            else:
                if cue_ns is not None and self.pending == Prayer.SOUL_SPLIT:
                    raise RuntimeError("Trial stopped because a melee cue arrived before Soul Split was acknowledged.")
                deadline = self.release_ns if self.pending == Prayer.MELEE else self.sent_ns + SOUL_SPLIT_ACK_NS
                if now >= deadline:
                    raise RuntimeError(f"Trial stopped because {self.pending.name.lower()} was not acknowledged.")
                return updates

        if self.target is None:
            self.target = target
            if active != Prayer.MELEE:
                updates.append(self.press(Prayer.MELEE))
                return updates

        if now >= self.release_ns:
            if now - self.release_ns > MAX_AGE_NS:
                raise RuntimeError("Trial stopped because the Soul Split return is late.")
            if active == Prayer.SOUL_SPLIT:
                self.finish_cycle()
                updates.append({"action": "already_active", "prayer": "soul_split", "observed_ns": sampled_ns})
            elif active == Prayer.MELEE:
                updates.append(self.press(Prayer.SOUL_SPLIT))
            else:
                raise RuntimeError("Trial stopped because the overhead changed during the melee window.")
        return updates


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


def observe(reader, duration, out, enrage, keys, trial_window=None):
    def send(prayer):
        reader.check(reader.lib.rs3_set_prayer(reader.snapshot, prayer, TRIAL_KEYS[prayer], 0))
        return reader.lib.rs3_monotonic_ns()

    trial = PrayerTrial(send) if trial_window else None
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
        updates = []
        if trial:
            window = trial_window(session)
            target = None
            if window and len(telos) == 1 and telos[0]["action_sequence"] is not None:
                target = (session, window, telos[0]["instance_id"], telos[0]["config_id"])
            active = Prayer[(combat["active_prayer"] or "unknown").upper()]
            cue_ns = max((a["observed_ns"] for a in actions if a["user_labeled_melee_auto"]), default=None)
            updates = trial.step(target, active, info.monotonic_ns, reader.lib.rs3_monotonic_ns(), cue_ns, lost)
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
        if state != previous or actions or lost or presses or updates or started - last_write >= 1:
            row = dict(state, enrage_label=enrage, sample_sequence=info.sequence,
                       sample_started_ns=info.monotonic_ns, sample_duration_ns=info.sample_duration_ns,
                       client_logic_tick=info.logic_tick, telos_actions=actions,
                       npc_action_events_lost=lost, manual_prayer_presses=presses)
            if trial:
                row["trial_updates"] = updates
            print(json.dumps(row), file=out, flush=True)
            previous, last_write = state, started
        time.sleep(max(0, started + POLL_SECONDS - time.monotonic()))
    return trial.completed_cycles if trial else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="RuneScape process ID")
    parser.add_argument("--duration", type=float, default=300, help="Recording duration in seconds")
    parser.add_argument("--enrage", type=int, help="Manually entered enrage for this capture")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record-keys", action="store_true", help="Timestamp manual F1/F2 presses in RuneScape")
    mode.add_argument("--trial", action="store_true", help="Repeat experimental F1/F2 cycles on fresh Telos cues")
    parser.add_argument("--output", type=Path, help="Write a new local JSONL file instead of standard output")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be finite and positive")
    if args.enrage is not None and not 0 <= args.enrage <= 4000:
        parser.error("--enrage must be between 0 and 4000")
    try:
        keys = ManualPrayerKeys() if args.record_keys or args.trial else None
        with Reader(pid=args.pid, worker_hz=0) as reader:
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
            output = args.output.open("x", encoding="utf-8") if args.output else nullcontext(sys.stdout)
            with output as out:
                message = ("Trial armed. Waiting for a fresh 28929 cue in foreground RuneScape."
                           if args.trial else "Recording Telos prayer timing. Switch prayers manually.")
                print(message, file=sys.stderr, flush=True)
                completed = observe(reader, args.duration, out, args.enrage, keys if args.record_keys else None,
                                    keys.game_window if args.trial else None)
                if args.trial:
                    print(f"Trial ended with {completed} completed switch cycle(s).", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        return
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
