"""Record C-collected prayer flags, NPC actions, local hitmarks and prayer keys. Sends no input."""

import argparse
import json
import sys
import time
from pathlib import Path

from workspace import add_import_paths

add_import_paths(package=True)

from capture_local_combat import PrayerKeys, load_bindings  # noqa: E402
from rs3_reader.native import NPCActionEvent, Reader, Variable, decode_text  # noqa: E402
from rs3_reader.telemetry import combat_record, hit_record  # noqa: E402

POLL_SECONDS = 0.002
HEARTBEAT_SECONDS = 1


def animation_records(reader, info, npcs):
    return [
        {
            "instance_id": npc.instance_id,
            "runtime_id": npc.runtime_id,
            "config_id": npc.config_id,
            "name": decode_text(npc.name),
            "valid": bool(animation.valid),
            "action_sequence": animation.action_sequence if animation.valid else None,
            "movement_sequence": animation.movement_sequence if animation.valid else None,
        }
        for npc, animation in zip(npcs[: info.npc_count], reader.npc_animations[: info.npc_count])
    ]


def action_event_record(event):
    return {field_name: getattr(event, field_name) for field_name, _ in NPCActionEvent._fields_}


def player_hit_event_record(event):
    record = {
        field_name: getattr(event, field_name)
        for field_name, _ in event._fields_
        if field_name != "hit"
    }
    record["hit"] = hit_record(event.hit)
    return record


def player_hitmarks_record(reader):
    info = reader.player_hit_info
    return {
        "status": info.status,
        "reason": info.reason,
        "player_index": info.player_index,
        "instance_id": info.instance_id,
        "hits": [hit_record(hit) for hit in reader.player_hits[: info.hit_count]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", type=int)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.duration <= 0:
        parser.error("duration must be positive")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)

    with Reader(pid=arguments.pid, worker_hz=100) as reader, arguments.output.open(
        "w", encoding="utf-8"
    ) as output:
        print(
            "Recording native prayer/animation observations; no game input.",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + arguments.duration
        keys = PrayerKeys(reader.info.pid or arguments.pid, load_bindings())
        previous_signature, last_write, written_rows, previous_sequence = None, 0, 0, 0
        while time.monotonic() < deadline:
            info, player, npcs, _ = reader.sample()
            actions, lost = reader.drain_npc_action_events()
            hits, hits_lost = reader.drain_player_hit_events()
            if info.pid:
                keys.pid = info.pid
            presses = keys.sample(reader.monotonic_ns)
            new_observations = actions or lost or hits or hits_lost or presses
            if info.sequence == previous_sequence and not new_observations:
                time.sleep(POLL_SECONDS)
                continue
            previous_sequence = info.sequence
            tile = reader.position
            payload = {
                "status": info.status,
                "pid": info.pid,
                "process_creation_time": info.process_creation_time,
                "combat": combat_record(reader),
                "animations": animation_records(reader, info, npcs),
                "hp": player.variable(Variable.HEALTH),
                "hp_changed_ns": player.hp_changed_ns,
                "prayer_raw": player.variable(Variable.PRAYER_PACKED),
                "telos_raw": player.variable(Variable.TELOS_PACKED),
                "position": [tile.x, tile.y, tile.plane] if tile.valid else None,
                "player_hitmarks": player_hitmarks_record(reader),
            }
            signature = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            changed = new_observations or signature != previous_signature
            if changed or now - last_write >= HEARTBEAT_SECONDS:
                payload.update(
                    sequence=info.sequence,
                    observed_ns=info.monotonic_ns,
                    timestamp_ns=info.timestamp_ns,
                    client_logic_tick=info.logic_tick,
                    sample_duration_ns=info.sample_duration_ns,
                    executable_sha256=info.executable_sha256.decode("ascii"),
                    npc_action_events=[action_event_record(event) for event in actions],
                    npc_action_events_lost=lost,
                    manual_prayer_keys=presses,
                    player_hit_events=[player_hit_event_record(event) for event in hits],
                    player_hit_events_lost=hits_lost,
                )
                output.write(json.dumps(payload) + "\n")
                output.flush()
                written_rows += 1
                previous_signature, last_write = signature, now
            time.sleep(POLL_SECONDS)
        print(
            f"Wrote {written_rows} changed/heartbeat observations to {arguments.output}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
