"""Record compact inventory events and ability timer transitions from the C collector."""

import argparse
import json
import time
from pathlib import Path

from workspace import WORK_DIRECTORY, add_import_paths

add_import_paths(package=True)

from rs3_reader.telemetry import NativeTelemetry  # noqa: E402

POLL_SECONDS = 0.05


def timer_transitions(record, previous_timers):
    """Return the abilities whose timer endpoints or readiness changed."""
    changed = []
    for ability in record["abilities"]["entries"]:
        cooldown = ability["cooldown"]
        timer_state = (cooldown["start_tick"], cooldown["end_tick"], cooldown["remaining_ms"] == 0)
        if timer_state != previous_timers.get(ability["struct_id"]):
            changed.append(ability)
            previous_timers[ability["struct_id"]] = timer_state
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=WORK_DIRECTORY / "inventory_native_validation.jsonl"
    )
    parser.add_argument("--duration", type=float, default=900)
    arguments = parser.parse_args()
    telemetry = NativeTelemetry(worker_hz=100)
    previous_timers = {}
    last_inventory = None
    try:
        with arguments.output.open("w", encoding="utf-8") as output:
            deadline = time.perf_counter() + arguments.duration
            while time.perf_counter() < deadline:
                record = telemetry.sample()
                changes = timer_transitions(record, previous_timers)
                inventory = (record["inventory"].get("slots"), record["equipment"].get("slots"))
                inventory_changed = inventory != last_inventory
                if changes or inventory_changed or record["inventory_changes"]["newly_observed"]:
                    row = {
                        "timestamp_ms": record["timestamp_ms"],
                        "native": record.get("native"),
                        "inventory_changes": record["inventory_changes"],
                        "timer_transitions": changes,
                        "equipment_configuration": record["equipment"]["configuration"],
                    }
                    if inventory_changed:
                        row["inventory"], row["equipment"] = inventory
                    output.write(json.dumps(row, separators=(",", ":")) + "\n")
                    output.flush()
                    last_inventory = inventory
                time.sleep(POLL_SECONDS)
    finally:
        telemetry.close()


if __name__ == "__main__":
    main()
