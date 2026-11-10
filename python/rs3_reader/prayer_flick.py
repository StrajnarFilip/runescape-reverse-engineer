"""Observe native prayer state and NPC animations, or replay timing scenarios. No game input.

Usage::

    python -m rs3_reader.prayer_flick --duration 60
    python -m rs3_reader.prayer_flick --replay examples/prayer_replay.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .native import REPOSITORY_ROOT, PRAYER_POINTS_MASK, Reader, Status, Variable, decode_text
from .prayer import Action, Frame, FrameFlags, Planner, Prayer, Threat, ThreatFlags, prayer_id
from .telemetry import combat_record

DEFAULT_PROFILE = REPOSITORY_ROOT / "profiles" / "prayer_flick.json"
NANOSECONDS_PER_MILLISECOND = 1_000_000
DEFAULT_REPLAY_FLAGS = FrameFlags.CONNECTED | FrameFlags.ACTIVE_KNOWN | FrameFlags.POINTS_KNOWN
DEFAULT_PRAYER_POINTS = 840
# Frames older than this, or from a stalled clock, are not connected observations.
MAX_CLOCK_STALL_NS = 150_000_000
OBSERVE_POLL_SECONDS = 0.01
HEARTBEAT_SECONDS = 1
MISSING_CAPABILITIES = ["attack_classification", "prayer_check_timing", "complete_attack_coverage"]


def available_prayer_mask(settings):
    """Return the RS3_PRAYER_BIT() mask of the profile's available prayers."""
    return sum(1 << prayer_id(name) for name in set(settings["available_prayers"]))


def milliseconds_to_ns(milliseconds):
    return milliseconds * NANOSECONDS_PER_MILLISECOND


def replay_frame(row, available_mask):
    now_ns = milliseconds_to_ns(row["time_ms"])
    return Frame(
        now_ns,
        milliseconds_to_ns(row.get("observed_ms", row["time_ms"])),
        row.get("session", 1),
        row["sequence"],
        milliseconds_to_ns(row.get("coverage_until_ms", 0)),
        row.get("flags", DEFAULT_REPLAY_FLAGS),
        prayer_id(row["active_prayer"]),
        available_mask,
        row.get("prayer_points", DEFAULT_PRAYER_POINTS),
    )


def replay_threat(threat):
    return Threat(
        threat["id"],
        threat.get("source", 1),
        milliseconds_to_ns(threat["earliest_ms"]),
        milliseconds_to_ns(threat["latest_ms"]),
        prayer_id(threat["prayer"]),
        ThreatFlags.TIMING_VALIDATED if threat.get("validated", False) else 0,
    )


def replay(path, settings):
    """Print the planner decision for every frame of a synthetic replay file."""
    scenario = json.loads(path.read_text(encoding="utf-8"))
    if scenario.get("schema_version") != 1:
        raise ValueError("Unsupported replay schema")
    available_mask = available_prayer_mask(settings)
    with Planner(settings) as planner:
        for row in scenario["frames"]:
            frame = replay_frame(row, available_mask)
            threats = [replay_threat(threat) for threat in row.get("threats", [])]
            decision = planner.step(frame, threats)
            print(
                json.dumps(
                    {
                        "source": "synthetic_replay",
                        "time_ms": row["time_ms"],
                        "decision": planner.describe(decision),
                        "input_sent": False,
                    }
                )
            )
            if decision.action == Action.SET and row.get("simulate_acceptance", False):
                planner.note_sent(decision.command_id, frame.now_ns)


def observed_frame(reader, info, player, session_id, available_mask):
    """Build a planner frame from a native sample. Coverage stays unknown."""
    active_prayer = reader.active_prayer()
    connected = (
        info.status in (Status.AVAILABLE, Status.PARTIAL)
        and reader.combat.scene_epoch
        and player.clock_stalled_ns < MAX_CLOCK_STALL_NS
    )
    flags = FrameFlags.CONNECTED if connected else FrameFlags(0)
    if active_prayer != Prayer.UNKNOWN:
        flags |= FrameFlags.ACTIVE_KNOWN
    if player.variable_is_valid(Variable.PRAYER_PACKED):
        flags |= FrameFlags.POINTS_KNOWN
    prayer_points = (
        player.raw_variables[Variable.PRAYER_PACKED] & PRAYER_POINTS_MASK
        if flags & FrameFlags.POINTS_KNOWN
        else 0
    )
    return Frame(
        reader.monotonic_ns(),
        info.monotonic_ns,
        session_id,
        info.sequence,
        0,
        flags,
        active_prayer,
        available_mask,
        prayer_points,
    )


def animation_records(reader, info, npcs):
    return [
        {
            "instance_id": npc.instance_id,
            "runtime_id": npc.runtime_id,
            "config_id": npc.config_id,
            "name": decode_text(npc.name),
            "action_sequence": animation.action_sequence if animation.valid else None,
            "movement_sequence": animation.movement_sequence if animation.valid else None,
        }
        for npc, animation in zip(npcs[: info.npc_count], reader.npc_animations[: info.npc_count])
    ]


def observe(arguments, settings):
    """Print planner decisions for live samples until the duration elapses.

    No timing classifier is installed yet: an empty threat list with unknown
    coverage cannot authorize a healing flick or an automatic protection switch.
    """
    available_mask = available_prayer_mask(settings)
    with Reader(pid=arguments.pid, worker_hz=100) as reader, Planner(settings) as planner:
        deadline = time.monotonic() + arguments.duration
        last_signature, last_write = None, 0
        session_key, session_id = None, 0
        while time.monotonic() < deadline:
            info, player, npcs, _ = reader.sample()
            current_session_key = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
            if current_session_key != session_key:
                session_id += 1
                session_key = current_session_key
            frame = observed_frame(reader, info, player, session_id, available_mask)
            payload = {
                "source": "native_memory",
                "pid": info.pid,
                "combat": combat_record(reader),
                "animations": animation_records(reader, info, npcs),
                "decision": planner.describe(planner.step(frame)),
                "input_sent": False,
                "live_flick_ready": False,
                "missing": MISSING_CAPABILITIES,
            }
            signature = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            if signature != last_signature or now - last_write >= HEARTBEAT_SECONDS:
                payload.update(observed_ns=info.monotonic_ns, sequence=info.sequence)
                print(json.dumps(payload), flush=True)
                last_signature, last_write = signature, now
            time.sleep(OBSERVE_POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--pid", type=int, help="Use a specific rs2client.exe process ID")
    parser.add_argument("--duration", type=float, default=60, help="Observation seconds")
    parser.add_argument("--replay", type=Path, help="Replay a synthetic scenario instead")
    arguments = parser.parse_args()
    if arguments.duration <= 0:
        parser.error("duration must be positive")
    settings = json.loads(arguments.profile.read_text(encoding="utf-8"))
    if settings.get("schema_version") != 1 or settings.get("mode") != "observe":
        parser.error("Only schema 1 observe profiles are supported")
    print("Prayer planner: replay/observation only; no game input.", file=sys.stderr)
    if arguments.replay:
        replay(arguments.replay, settings)
    else:
        observe(arguments, settings)


if __name__ == "__main__":
    main()
