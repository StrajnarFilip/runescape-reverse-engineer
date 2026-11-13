"""Record Telos prayer timing using the configured Ctrl+function-key chords.

The trial is opt-in and refuses to start until profiles/prayer_flick.json marks
prayer-check deadlines and attack coverage as validated. Each switch needs a
newer observed sample to acknowledge it.

Usage::

    python -m rs3_reader.telos_prayer --duration 180 --enrage 300 --record-keys --output out.jsonl
    python -m rs3_reader.telos_prayer --duration 180 --enrage 0 --shadow-trial --output out.jsonl
    python -m rs3_reader.telos_prayer --trial
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import time
from contextlib import nullcontext
from ctypes import wintypes
from pathlib import Path

from .native import REPOSITORY_ROOT, NPCActionFlags, Reader, Variable, decode_text
from .prayer import Prayer
from .telemetry import combat_record, player_hit_splats_record

# Action sequence the user labeled as a Telos melee auto-attack.
MELEE_AUTO_SEQUENCE = 28929
VK_F3 = 0x72
VK_F4 = 0x73
VK_CONTROL = 0x11
# Bound keys, all pressed with Ctrl: virtual key -> (label, bound prayer).
PRAYER_KEYS = {VK_F3: ("Ctrl+F3", Prayer.SOUL_SPLIT), VK_F4: ("Ctrl+F4", Prayer.MELEE)}
TRIAL_KEYS = {prayer: virtual_key for virtual_key, (_, prayer) in PRAYER_KEYS.items()}
# RS3_KEY_CTRL from include/rs3/prayer.h.
PRAYER_MODIFIERS = 1
# Shift, Ctrl, Alt and both Windows keys.
MODIFIER_KEYS = (0x10, VK_CONTROL, 0x12, 0x5B, 0x5C)
PRAYER_FLICK_PROFILE = REPOSITORY_ROOT / "profiles" / "prayer_flick.json"
POLL_SECONDS = 0.01
HEARTBEAT_SECONDS = 1
DEFAULT_CAPTURE_SECONDS = 300
MAX_ENRAGE = 4000
MELEE_HOLD_NS = 600_000_000
MAX_AGE_NS = 100_000_000
ACK_TIMEOUT_NS = 1_200_000_000
TELOS_NAME = "telos"


class ManualPrayerKeys:
    """Samples Ctrl+F3/F4 down edges while the verified game process is in the foreground."""

    def __init__(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.get_foreground_window = user32.GetForegroundWindow
        self.get_foreground_window.argtypes = []
        self.get_foreground_window.restype = wintypes.HWND
        self.get_window_thread_process_id = user32.GetWindowThreadProcessId
        self.get_window_thread_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.get_window_thread_process_id.restype = wintypes.DWORD
        self.get_async_key_state = user32.GetAsyncKeyState
        self.get_async_key_state.argtypes = [ctypes.c_int]
        self.get_async_key_state.restype = ctypes.c_short
        self.focused_session_window = None
        self.held_keys = set()

    def game_window(self, session):
        """Return the foreground window when it belongs to the session's process."""
        pid, _, scene_epoch = session
        window = self.get_foreground_window()
        owner_pid = wintypes.DWORD()
        if (
            pid
            and scene_epoch
            and window
            and self.get_window_thread_process_id(window, ctypes.byref(owner_pid))
            and owner_pid.value == pid
        ):
            return window
        return None

    def _lose_focus(self):
        self.focused_session_window = None
        self.held_keys.clear()

    def sample(self, session, clock):
        """Return the Ctrl+F3/F4 presses since the previous sample."""
        window = self.game_window(session)
        if window is None:
            self._lose_focus()
            return []

        held_keys = {key for key in PRAYER_KEYS if self.get_async_key_state(key) < 0}
        other_modifier_held = any(
            self.get_async_key_state(key) < 0 for key in MODIFIER_KEYS if key != VK_CONTROL
        )
        control_held = self.get_async_key_state(VK_CONTROL) < 0
        observed_ns = clock()
        if self.get_foreground_window() != window:
            self._lose_focus()
            return []

        focused_session_window = (session, window)
        # A key held when focus is gained is a baseline. A release never emits a press.
        if self.focused_session_window == focused_session_window:
            pressed_keys = held_keys - self.held_keys
        else:
            pressed_keys = set()
        self.focused_session_window = focused_session_window
        self.held_keys = held_keys
        if other_modifier_held or not control_held:
            return []
        return [
            {
                "key": PRAYER_KEYS[key][0],
                "bound_prayer": PRAYER_KEYS[key][1].name.lower(),
                "observed_ns": observed_ns,
            }
            for key in sorted(pressed_keys)
        ]


class PrayerTrial:
    """Switches to melee protection on a fresh cue, then back to Soul Split.

    A cycle re-arms once Soul Split is observed. Every key needs a newer sample
    to acknowledge it; a missed acknowledgement skips the cycle and waits out
    the acknowledgement timeout before reacting to another cue.
    """

    def __init__(self, send_prayer):
        self.send_prayer = send_prayer
        self.target = None
        self.melee_release_ns = 0
        self.pending_prayer = None
        self.sent_ns = 0
        self.deferred_cue_ns = None
        self.rearm_ns = 0
        self.completed_cycles = 0

    def finish_cycle(self):
        self.target = None
        self.deferred_cue_ns = None
        self.completed_cycles += 1

    def skip_cycle(self, reason, now_ns):
        if self.pending_prayer is not None:
            self.rearm_ns = self.sent_ns + ACK_TIMEOUT_NS
        self.target = None
        self.pending_prayer = None
        self.deferred_cue_ns = None
        return {"action": "cycle_skipped", "reason": reason, "observed_ns": now_ns}

    def press(self, prayer, now_ns):
        # A partial Windows insertion can still reach the game, so a rejected
        # key uses the same cool-down as an unconfirmed one.
        self.pending_prayer = prayer
        self.sent_ns = now_ns
        try:
            self.sent_ns = self.send_prayer(prayer)
        except RuntimeError as error:
            return self.skip_cycle(str(error), now_ns)
        return {"action": "sent", "prayer": prayer.name.lower(), "observed_ns": self.sent_ns}

    def _handle_pending(self, active_prayer, sampled_ns, now_ns, cue_ns, updates):
        """Process an unacknowledged key.

        Returns (finished, cue_ns, melee_acknowledged); finished means the step
        must return updates immediately.
        """
        deadline_ns = self.sent_ns + ACK_TIMEOUT_NS
        if cue_ns is not None:
            if self.pending_prayer == Prayer.MELEE:
                self.melee_release_ns = cue_ns + MELEE_HOLD_NS
            else:
                self.deferred_cue_ns = cue_ns
        if self.sent_ns < sampled_ns <= deadline_ns and active_prayer == self.pending_prayer:
            updates.append(
                {
                    "action": "acknowledged",
                    "prayer": active_prayer.name.lower(),
                    "observed_ns": sampled_ns,
                }
            )
            self.pending_prayer = None
            if active_prayer != Prayer.SOUL_SPLIT:
                return False, cue_ns, True
            # A cue that arrived while Soul Split was pending starts the next cycle.
            cue_ns = self.deferred_cue_ns
            self.finish_cycle()
            if cue_ns is None or now_ns - cue_ns > MAX_AGE_NS:
                return True, cue_ns, False
            return False, cue_ns, False
        if now_ns >= deadline_ns:
            reason = f"{self.pending_prayer.name.lower()} was not observed"
            updates.append(self.skip_cycle(reason, now_ns))
        return True, cue_ns, False

    def step(self, target, active_prayer, sampled_ns, now_ns, cue_ns=None, lost=0):
        """Advance the trial for one sample and return its update records."""
        idle_without_cue = self.target is None and (target is None or cue_ns is None)
        if now_ns < self.rearm_ns or idle_without_cue:
            return []
        if target is None or (self.target is not None and target != self.target):
            return [self.skip_cycle("Telos instance, scene or foreground window changed", now_ns)]
        if lost or not 0 <= now_ns - sampled_ns <= MAX_AGE_NS:
            return [self.skip_cycle("event loss or stale snapshot", now_ns)]
        if cue_ns is not None and not 0 <= now_ns - cue_ns <= MAX_AGE_NS:
            cue_ns = None

        updates = []
        melee_acknowledged = False
        if self.pending_prayer is not None:
            finished, cue_ns, melee_acknowledged = self._handle_pending(
                active_prayer, sampled_ns, now_ns, cue_ns, updates
            )
            if finished:
                return updates

        if active_prayer == Prayer.UNKNOWN:
            if self.target is not None:
                updates.append(self.skip_cycle("active overhead is unknown", now_ns))
            return updates

        if cue_ns is not None:
            self.target = target
            self.melee_release_ns = cue_ns + MELEE_HOLD_NS
            if active_prayer != Prayer.MELEE:
                updates.append(self.press(Prayer.MELEE, now_ns))
                return updates

        if self.target is not None and now_ns >= self.melee_release_ns:
            if now_ns - self.melee_release_ns > MAX_AGE_NS and not melee_acknowledged:
                return [self.skip_cycle("Soul Split return is late", now_ns)]
            if active_prayer == Prayer.SOUL_SPLIT:
                self.finish_cycle()
                updates.append(
                    {"action": "already_active", "prayer": "soul_split", "observed_ns": sampled_ns}
                )
            elif active_prayer == Prayer.MELEE:
                updates.append(self.press(Prayer.SOUL_SPLIT, now_ns))
            else:
                updates.append(self.skip_cycle("overhead changed during the melee window", now_ns))
        return updates


def telos_actions(events, session, sample_sequence, instance_ids):
    """Return the action events of the tracked Telos instances in this exact sample."""
    return [
        {
            "event_sequence": event.sequence,
            "observed_ns": event.observed_ns,
            "instance_id": event.instance_id,
            "config_id": event.config_id,
            "action_sequence": event.action_sequence,
            "previous_action_sequence": event.previous_action_sequence,
            "client_logic_tick": event.logic_tick,
            "application_stamp": (
                event.application_stamp
                if event.flags & NPCActionFlags.HAS_APPLICATION_STAMP
                else None
            ),
            "user_labeled_melee_auto": event.action_sequence == MELEE_AUTO_SEQUENCE,
        }
        for event in events
        if (event.pid, event.process_creation_time, event.scene_epoch) == session
        and event.sample_sequence == sample_sequence
        and event.instance_id in instance_ids
    ]


def is_telos(npc):
    return decode_text(npc.name).partition(",")[0].strip().casefold() == TELOS_NAME


def telos_records(reader, info, npcs):
    records = []
    for index in range(info.npc_count):
        npc, animation = npcs[index], reader.npc_animations[index]
        if is_telos(npc):
            records.append(
                {
                    "instance_id": npc.instance_id,
                    "config_id": npc.config_id,
                    "action_sequence": animation.action_sequence if animation.valid else None,
                }
            )
    return records


def observe(reader, duration, output, enrage, keys, trial_window=None, *, shadow=False):
    """Write changed observations until the duration elapses. Returns completed cycles."""

    def send_prayer(prayer):
        if not shadow:
            result = reader.library.rs3_set_prayer(
                reader.snapshot, prayer, TRIAL_KEYS[prayer], PRAYER_MODIFIERS
            )
            reader.check_result(result)
        return reader.monotonic_ns()

    # In shadow mode, real manual overhead changes may acknowledge a hypothetical
    # input. A shadow update records the decision time without sending a chord.
    trial = PrayerTrial(send_prayer) if trial_window else None
    end_time = math.inf if duration is None else time.monotonic() + duration
    previous_state, last_write = None, 0
    while time.monotonic() < end_time:
        started = time.monotonic()
        # Synchronous sampling keeps each native action event with its snapshot.
        info, player, npcs, _ = reader.sample()
        combat = combat_record(reader)
        session = (info.pid, info.process_creation_time, combat["scene_epoch"])
        presses = keys.sample(session, reader.monotonic_ns) if keys else []
        events, lost = reader.drain_npc_action_events()
        hits, hits_lost = reader.drain_player_hit_events()
        telos = telos_records(reader, info, npcs)
        telos_instance_ids = {npc["instance_id"] for npc in telos}
        actions = telos_actions(events, session, info.sequence, telos_instance_ids)

        updates = []
        if trial:
            window = trial_window(session)
            target = None
            if window and len(telos) == 1 and telos[0]["action_sequence"] is not None:
                target = (session, window, telos[0]["instance_id"], telos[0]["config_id"])
            active_prayer = Prayer[(combat["active_prayer"] or "unknown").upper()]
            cue_ns = max(
                (action["observed_ns"] for action in actions if action["user_labeled_melee_auto"]),
                default=None,
            )
            updates = trial.step(
                target,
                active_prayer,
                info.monotonic_ns,
                reader.monotonic_ns(),
                cue_ns,
                lost + hits_lost,
            )

        tile = reader.position
        state = {
            "pid": info.pid,
            "process_creation_time": info.process_creation_time,
            "executable_sha256": info.executable_sha256.decode("ascii"),
            "status": info.status,
            "reason": decode_text(info.message) or None,
            "skipped_npcs": info.skipped_npcs,
            "combat": combat,
            "hp": player.variable(Variable.HEALTH),
            "telos_raw": player.variable(Variable.TELOS_PACKED),
            "position": [tile.x, tile.y, tile.plane] if tile.valid else None,
            "telos": telos,
            "player_hitmarks": player_hit_splats_record(reader, [], 0),
        }
        changed = (
            state != previous_state or actions or lost or presses or updates or hits or hits_lost
        )
        if changed or started - last_write >= HEARTBEAT_SECONDS:
            row = dict(
                state,
                enrage_label=enrage,
                sample_sequence=info.sequence,
                sample_started_ns=info.monotonic_ns,
                sample_duration_ns=info.sample_duration_ns,
                client_logic_tick=info.logic_tick,
                telos_actions=actions,
                npc_action_events_lost=lost,
                manual_prayer_presses=presses,
            )
            if hits or hits_lost:
                row["player_hitmarks"] = player_hit_splats_record(reader, hits, hits_lost)
            if trial:
                if shadow:
                    row["shadow_updates"] = updates
                    row["input_sent"] = False
                else:
                    row["trial_updates"] = updates
            print(json.dumps(row), file=output, flush=True)
            previous_state, last_write = state, started
        time.sleep(max(0, started + POLL_SECONDS - time.monotonic()))
    return trial.completed_cycles if trial else 0


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", type=int, help="RuneScape process ID")
    parser.add_argument(
        "--duration",
        type=float,
        help="Limit in seconds (default: 300 for a capture, unlimited for a trial)",
    )
    parser.add_argument("--enrage", type=int, help="Manually entered enrage for this capture")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--record-keys",
        action="store_true",
        help="Timestamp manual Ctrl+F3/F4 presses in RuneScape",
    )
    mode.add_argument(
        "--shadow-trial",
        action="store_true",
        help="Log hypothetical melee/Soul Split decisions alongside real manual keys and hits",
    )
    mode.add_argument(
        "--trial",
        action="store_true",
        help="Requires validated prayer-check timing and attack coverage",
    )
    parser.add_argument(
        "--output", type=Path, help="Write a new local JSONL file instead of standard output"
    )
    arguments = parser.parse_args()
    if arguments.duration is not None and (
        not math.isfinite(arguments.duration) or arguments.duration <= 0
    ):
        parser.error("--duration must be finite and positive")
    if arguments.enrage is not None and not 0 <= arguments.enrage <= MAX_ENRAGE:
        parser.error(f"--enrage must be between 0 and {MAX_ENRAGE}")
    if arguments.trial:
        # Checked before any reader opens: unvalidated timing never sends a key.
        profile = json.loads(PRAYER_FLICK_PROFILE.read_text(encoding="utf-8"))
        timing_validated = profile.get("prayer_check_deadline_validated")
        if not profile.get("attack_coverage_validated") or not timing_validated:
            parser.error(
                "Automatic trial is unavailable until prayer-check timing and attack "
                "coverage are validated; use --record-keys"
            )
    return parser, arguments


def main():
    parser, arguments = parse_arguments()
    try:
        keys = (
            ManualPrayerKeys()
            if arguments.record_keys or arguments.trial or arguments.shadow_trial
            else None
        )
        with Reader(pid=arguments.pid, worker_hz=0) as reader:
            if arguments.output:
                arguments.output.parent.mkdir(parents=True, exist_ok=True)
                output_context = arguments.output.open("x", encoding="utf-8")
            else:
                output_context = nullcontext(sys.stdout)
            with output_context as output:
                if arguments.trial:
                    message = "Trial armed. Waiting for a fresh 28929 cue in foreground RuneScape."
                elif arguments.shadow_trial:
                    message = "Shadow trial: recording hypothetical decisions. Sends no game input."
                else:
                    message = "Recording Telos prayer timing. Switch prayers manually."
                print(message, file=sys.stderr, flush=True)
                if arguments.trial or arguments.duration is not None:
                    duration = arguments.duration
                else:
                    duration = DEFAULT_CAPTURE_SECONDS
                completed_cycles = observe(
                    reader,
                    duration,
                    output,
                    arguments.enrage,
                    keys if arguments.record_keys or arguments.shadow_trial else None,
                    keys.game_window if arguments.trial or arguments.shadow_trial else None,
                    shadow=arguments.shadow_trial,
                )
                if arguments.trial:
                    print(
                        f"Trial ended with {completed_cycles} completed switch cycle(s).",
                        file=sys.stderr,
                        flush=True,
                    )
    except KeyboardInterrupt:
        return
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
