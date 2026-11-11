"""Run one explicitly authorized, bounded prayer-input calibration in phase 1.

The player starts the encounter and casts Devotion. This experiment holds Melee
for a one-damage control, then tests one delayed return from Soul Split. It does
not enable the production flicker or claim a server-side prayer deadline.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from workspace import add_import_paths

add_import_paths(package=True)

from capture_prayer import (  # noqa: E402
    action_event_record,
    animation_records,
    devotion_cooldown_record,
    player_hit_event_record,
)
from rs3_reader.native import LOGIC_TICK_MS, Reader, Status, Variable  # noqa: E402
from rs3_reader.prayer import Prayer  # noqa: E402
from rs3_reader.telemetry import combat_record  # noqa: E402
from rs3_reader.telos_prayer import ManualPrayerKeys  # noqa: E402

MILLISECOND_NS = 1_000_000
MAX_AGE_NS = 150 * MILLISECOND_NS
ACK_TIMEOUT_NS = 1_200 * MILLISECOND_NS
HIT_TIMEOUT_NS = 1_300 * MILLISECOND_NS
MAX_DISPATCH_LATENESS_NS = 100 * MILLISECOND_NS
MAX_DEVOTION_AGE_MS = 8000
MELEE_AUTO_SEQUENCE = 28929
ACTIVE_TELOS_ID = 22891
DORMANT_TELOS_ID = 22892
MELEE_DAMAGE_TYPE = 133
MISS_TYPE = 482
PRAYER_KEYS = {Prayer.SOUL_SPLIT: 0x72, Prayer.MELEE: 0x73}
CTRL_MODIFIER = 1
MAX_TEST_DELAY_MS = 600
MAX_CALIBRATION_DURATION_SECONDS = 600
DEFAULT_CALIBRATION_DURATION_SECONDS = 300
MAX_ENRAGE_LABEL = 4000


class PrayerCheckTrial:
    """One controlled input experiment; send_prayer returns native acceptance time."""

    def __init__(self, delay_ms, send_prayer):
        self.delay_ns = delay_ms * MILLISECOND_NS
        self.send_prayer = send_prayer
        self.state = "waiting"
        self.session = None
        self.instance = None
        self.saw_dormant = False
        self.baseline_timer = None
        self.fresh_devotion = False
        self.pending = None
        self.sent_ns = 0
        self.pending_sequence = 0
        self.cue = None
        self.hit = None
        self.finished = False

    def command(self, prayer, frame, output):
        if frame["prayer"] == prayer:
            return True
        try:
            accepted_ns = self.send_prayer(prayer)
        except RuntimeError as error:
            output.append({"event": "input_rejected", "reason": str(error)})
            return False
        self.pending = prayer
        self.sent_ns = accepted_ns
        self.pending_sequence = frame["sequence"]
        output.append(
            {
                "event": "input_accepted",
                "prayer": prayer.name.lower(),
                "accepted_ns": accepted_ns,
                "sample_sequence": frame["sequence"],
                "requested_after_cue_ms": (
                    self.delay_ns / MILLISECOND_NS
                    if prayer == Prayer.MELEE and self.state == "delay"
                    else None
                ),
                "accepted_after_cue_ms": (
                    (accepted_ns - self.cue["observed_ns"]) / MILLISECOND_NS
                    if self.cue and self.state == "delay"
                    else None
                ),
            }
        )
        return True

    def abort(self, frame, output, reason):
        output.append({"event": "aborted", "reason": reason})
        # Never send a guessed toggle on a stale or foreign-window frame.
        trustworthy = frame["fresh"] and frame["focused"] and frame["session"] == self.session
        if trustworthy and frame["prayer"] == Prayer.SOUL_SPLIT:
            self.state = "restoring"
            if self.pending == Prayer.MELEE or self.command(Prayer.MELEE, frame, output):
                return output
        elif trustworthy and self.pending == Prayer.SOUL_SPLIT:
            self.state = "restoring"
            return output
        self.finished, self.state = True, "aborted"
        if frame["prayer"] != Prayer.MELEE or (not trustworthy and self.pending is not None):
            output.append({"event": "manual_protection_required"})
        return output

    def step(self, frame, actions, hits, lost=0):
        if self.finished:
            return []
        output = []
        if self.session is None:
            self.session = frame["session"]
            self.baseline_timer = frame["devotion_start"]
        if frame["session"] != self.session:
            if self.state != "waiting":
                return self.abort(frame, output, "process or scene changed")
            self.session = frame["session"]
            self.baseline_timer = frame["devotion_start"]
            self.saw_dormant = False
        if not frame["fresh"] or not frame["focused"] or lost:
            if self.state != "waiting":
                return self.abort(frame, output, "stale frame, focus loss or event gap")
            return output
        if frame["prayer"] == Prayer.UNKNOWN:
            if self.state != "waiting":
                return self.abort(frame, output, "overhead unknown")
            return output
        if self.pending is not None:
            if (
                frame["sequence"] > self.pending_sequence
                and frame["observed_ns"] > self.sent_ns
                and frame["observed_ns"] - self.sent_ns <= ACK_TIMEOUT_NS
                and frame["prayer"] == self.pending
            ):
                output.append(
                    {
                        "event": "acknowledged",
                        "prayer": self.pending.name.lower(),
                        "observed_ns": frame["observed_ns"],
                    }
                )
                self.pending = None
            elif frame["now_ns"] - self.sent_ns >= ACK_TIMEOUT_NS:
                if self.state == "restoring":
                    self.finished, self.state = True, "aborted"
                    return [{"event": "manual_protection_required", "reason": "restore timed out"}]
                return self.abort(frame, output, "prayer acknowledgement timed out")
        if self.state == "restoring":
            if self.pending is None:
                if frame["prayer"] == Prayer.MELEE:
                    self.finished, self.state = True, "aborted"
                    output.append({"event": "protection_restored"})
                elif not self.command(Prayer.MELEE, frame, output):
                    self.finished, self.state = True, "aborted"
                    output.append({"event": "manual_protection_required"})
            return output
        if self.state == "waiting":
            self.saw_dormant |= frame["telos_form"] == DORMANT_TELOS_ID
            if frame["prayer"] != Prayer.MELEE:
                return output
            if self.saw_dormant and frame["telos_form"] == ACTIVE_TELOS_ID:
                if self.pending is not None or frame["prayer"] != Prayer.MELEE:
                    return self.abort(frame, output, "encounter began before Melee confirmation")
                self.instance = frame["telos_instance"]
                self.state = "control"
                output.append({"event": "opening_detected"})
            return output
        if frame["telos_instance"] != self.instance or frame["telos_form"] != ACTIVE_TELOS_ID:
            return self.abort(frame, output, "Telos identity changed")
        if frame["devotion_start"] != self.baseline_timer and frame["devotion_age_ms"] is not None:
            self.fresh_devotion = True
        devotion_valid = (
            self.fresh_devotion
            and frame["devotion_age_ms"] is not None
            and 0 <= frame["devotion_age_ms"] <= MAX_DEVOTION_AGE_MS
        )
        for action in actions:
            if action["instance_id"] != self.instance:
                continue
            if action["action_sequence"] != MELEE_AUTO_SEQUENCE:
                # Opening awakening precedes the first auto; any later action interrupts it.
                if self.cue is not None:
                    return self.abort(frame, output, "special or unclassified action interrupted")
                continue
            if not 0 <= frame["now_ns"] - action["observed_ns"] <= MAX_AGE_NS:
                return self.abort(frame, output, "auto cue is stale or ahead of the current clock")
            if self.cue is not None and self.state not in ("next_cue",):
                return self.abort(frame, output, "new auto before the previous result")
            self.cue = action
            self.hit = None
            if self.state == "next_cue":
                if self.pending is not None or frame["prayer"] != Prayer.SOUL_SPLIT:
                    return self.abort(frame, output, "Soul Split not confirmed before test cue")
                if not devotion_valid:
                    return self.abort(frame, output, "no fresh Devotion control window")
                self.state = "delay"
            output.append(
                {
                    "event": "auto_cue",
                    "cue_sequence": action["sequence"],
                    "cue_ns": action["observed_ns"],
                    "stage": self.state,
                }
            )
        if self.cue is not None:
            for hit in hits:
                delay_ns = hit["observed_ns"] - self.cue["observed_ns"]
                if (
                    self.hit is None
                    and -100 * MILLISECOND_NS <= delay_ns <= HIT_TIMEOUT_NS
                    and hit["hit"]["type_id"] in (MELEE_DAMAGE_TYPE, MISS_TYPE)
                ):
                    self.hit = hit
                    output.append(
                        {
                            "event": "paired_hit",
                            "stage": self.state,
                            "cue_sequence": self.cue["sequence"],
                            "hit": hit,
                        }
                    )
            if self.state == "control" and self.hit is not None:
                if (
                    not devotion_valid
                    or self.hit["hit"]["type_id"] != MELEE_DAMAGE_TYPE
                    or self.hit["hit"]["amount"] != 1
                    or frame["prayer"] != Prayer.MELEE
                ):
                    return self.abort(
                        frame, output, "opening did not establish a one-damage control"
                    )
                self.state = "next_cue"
                if not self.command(Prayer.SOUL_SPLIT, frame, output):
                    return self.abort(frame, output, "Soul Split input failed")
            elif self.state == "delay":
                if frame["prayer"] != Prayer.SOUL_SPLIT:
                    return self.abort(
                        frame, output, "unexpected prayer change before test dispatch"
                    )
                if self.hit is not None:
                    return self.abort(frame, output, "test hit arrived before scheduled dispatch")
                due_ns = self.cue["observed_ns"] + self.delay_ns
                if frame["now_ns"] >= due_ns:
                    if (
                        not devotion_valid
                        or self.hit is not None
                        or frame["now_ns"] - due_ns > MAX_DISPATCH_LATENESS_NS
                    ):
                        return self.abort(
                            frame, output, "test dispatch was late or outside control"
                        )
                    if not self.command(Prayer.MELEE, frame, output):
                        return self.abort(frame, output, "Melee input failed")
                    self.state = "result"
            elif self.state == "result" and self.hit is not None and self.pending is None:
                if frame["prayer"] != Prayer.MELEE:
                    return self.abort(frame, output, "Melee was not restored")
                output.append(
                    {
                        "event": "completed",
                        "hit_amount": self.hit["hit"]["amount"],
                        "hit_type": self.hit["hit"]["type_id"],
                        "devotion_buff_from_memory": None,
                        "prayer_check_deadline_validated": False,
                    }
                )
                self.finished = True
                self.state = "completed"
            if (
                self.state not in ("next_cue",)
                and self.hit is None
                and frame["now_ns"] - self.cue["observed_ns"] > HIT_TIMEOUT_NS
            ):
                return self.abort(frame, output, "no unambiguous hit before timeout")
        return output


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--controlled-input", action="store_true")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--delay-ms", type=int, required=True)
    parser.add_argument("--duration", type=float, default=DEFAULT_CALIBRATION_DURATION_SECONDS)
    parser.add_argument("--enrage-label", type=int, help="User-reported enrage; unknown if omitted")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        not args.controlled_input
        or not 0 <= args.delay_ms <= MAX_TEST_DELAY_MS
        or not 0 < args.duration <= MAX_CALIBRATION_DURATION_SECONDS
        or (args.enrage_label is not None and not 0 <= args.enrage_label <= MAX_ENRAGE_LABEL)
    ):
        parser.error("Explicit --controlled-input, delay 0..600ms and duration 0..600s required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    focus = ManualPrayerKeys()
    with (
        Reader(pid=args.pid, worker_hz=100) as reader,
        args.output.open("x", encoding="utf-8") as out,
    ):

        def send_prayer(prayer):
            reader.check_result(
                reader.library.rs3_set_prayer(
                    reader.snapshot, prayer, PRAYER_KEYS[prayer], CTRL_MODIFIER
                )
            )
            return reader.monotonic_ns()

        trial = PrayerCheckTrial(args.delay_ms, send_prayer)
        print(
            "Controlled calibration armed; select Melee, then start the dormant Telos encounter.",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + args.duration
        previous, last_write = None, 0
        while not trial.finished:
            info, player, npcs, _ = reader.sample()
            now_ns = reader.monotonic_ns()
            session = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
            animations = animation_records(reader, info, npcs)
            telos = [
                actor for actor in animations if actor["name"].partition(",")[0].lower() == "telos"
            ]
            actor = telos[0] if len(telos) == 1 else {}
            timer = devotion_cooldown_record(reader)
            elapsed = (
                ((info.logic_tick - timer["start_tick"]) & 0xFFFFFFFF) * LOGIC_TICK_MS
                if timer.get("timer_valid")
                else None
            )
            frame = {
                "now_ns": now_ns,
                "observed_ns": info.monotonic_ns,
                "sequence": info.sequence,
                "session": session,
                "fresh": info.status in (Status.AVAILABLE, Status.PARTIAL)
                and bool(session[2])
                and 0 <= now_ns - info.monotonic_ns <= MAX_AGE_NS
                and player.clock_stalled_ns <= MAX_AGE_NS
                and actor.get("valid", False),
                "focused": bool(focus.game_window(session)),
                "prayer": Prayer(reader.active_prayer()),
                "telos_form": actor.get("config_id"),
                "telos_instance": actor.get("instance_id"),
                "devotion_start": timer.get("start_tick"),
                "devotion_age_ms": elapsed,
            }
            actions, action_lost = reader.drain_npc_action_events()
            hits, hit_lost = reader.drain_player_hit_events()
            action_records = [
                action_event_record(event)
                for event in actions
                if (event.pid, event.process_creation_time, event.scene_epoch) == session
            ]
            hit_records = [
                player_hit_event_record(event)
                for event in hits
                if (event.pid, event.process_creation_time, event.scene_epoch) == session
            ]
            frame["now_ns"] = reader.monotonic_ns()
            frame["fresh"] = frame["fresh"] and (
                0 <= frame["now_ns"] - info.monotonic_ns <= MAX_AGE_NS
            )
            if time.monotonic() >= deadline and trial.state != "restoring":
                updates = trial.abort(frame, [], "calibration duration elapsed")
            else:
                updates = trial.step(frame, action_records, hit_records, action_lost + hit_lost)
            payload = {
                "status": info.status,
                "pid": info.pid,
                "process_creation_time": info.process_creation_time,
                "combat": combat_record(reader),
                "animations": animations,
                "hp": player.variable(Variable.HEALTH),
                "devotion_cooldown": timer,
                "trial_state": trial.state,
                "requested_delay_ms": args.delay_ms,
                "enrage_user_label": args.enrage_label,
            }
            signature = json.dumps(payload, sort_keys=True)
            if (
                updates
                or action_records
                or hit_records
                or signature != previous
                or time.monotonic() - last_write >= 1
            ):
                payload.update(
                    observed_ns=info.monotonic_ns,
                    sequence=info.sequence,
                    timestamp_ns=info.timestamp_ns,
                    client_logic_tick=info.logic_tick,
                    executable_sha256=info.executable_sha256.decode("ascii"),
                    npc_action_events=action_records,
                    npc_action_events_lost=action_lost,
                    player_hit_events=hit_records,
                    player_hit_events_lost=hit_lost,
                    calibration_updates=updates,
                    input_mode="controlled_calibration",
                    automatic_flick_ready=False,
                )
                print(json.dumps(payload), file=out, flush=True)
                for update in updates:
                    print(json.dumps(update), file=sys.stderr, flush=True)
                previous, last_write = signature, time.monotonic()
            time.sleep(0.002)
        print("Controlled calibration stopped; no further inputs.", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
