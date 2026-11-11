"""Observe the result of a RuneScape control trial through the native reader.

Run from the checkout with ``python work/watch_control.py --duration 15``.
Wait for the baseline, perform one action in the foreground client, and
compare the subsequent changes with the expected result.
"""

from __future__ import annotations

import argparse
import json
import time

from workspace import add_import_paths

add_import_paths(package=True)

from rs3_reader.native import Reader, Status  # noqa: E402
from rs3_reader.prayer import Prayer  # noqa: E402

POLL_SECONDS = 0.05
DEFAULT_DURATION_SECONDS = 15
MAX_DURATION_SECONDS = 120


def observe(reader):
    """Copy the session and visible control outcomes from one native sample."""
    info, *_ = reader.sample()
    identity = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
    if info.status not in (Status.AVAILABLE, Status.PARTIAL) or not all(identity):
        raise RuntimeError("A verified in-game session is required")

    position = reader.position
    tile = (position.x, position.y, position.plane) if position.valid else None
    prayer = Prayer(reader.active_prayer())
    return identity, info.sequence, info.monotonic_ns, tile, prayer


def watch(reader, duration_seconds):
    """Print the baseline and tile or overhead changes in the same session."""
    identity, sequence, baseline_ns, tile, prayer = observe(reader)
    if tile is None and prayer is Prayer.UNKNOWN:
        raise RuntimeError("Player tile and overhead are both unavailable")
    print(json.dumps({"event": "baseline", "tile": tile, "prayer": prayer.name}), flush=True)

    deadline = time.monotonic() + duration_seconds
    changes = 0
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        current_identity, current_sequence, sample_ns, current_tile, current_prayer = observe(
            reader
        )
        if current_identity != identity:
            raise RuntimeError("Client or scene changed during the trial")
        if current_sequence <= sequence:
            continue
        sequence = current_sequence

        if (current_tile, current_prayer) != (tile, prayer):
            record = {
                "event": "change",
                "baseline_elapsed_ms": (sample_ns - baseline_ns) / 1_000_000,
                "tile": current_tile,
                "prayer": current_prayer.name,
            }
            print(json.dumps(record), flush=True)
            tile, prayer = current_tile, current_prayer
            changes += 1

    print(json.dumps({"event": "end", "changes_observed": changes}), flush=True)


def main():
    """Run a bounded trial observer without sending game input."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", type=int, help="Use a specific RuneScape client process ID")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    arguments = parser.parse_args()
    if not 0 < arguments.duration <= MAX_DURATION_SECONDS:
        parser.error(f"Duration must be positive and at most {MAX_DURATION_SECONDS} seconds")

    try:
        with Reader(pid=arguments.pid, worker_hz=0) as reader:
            watch(reader, arguments.duration)
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"Control observation stopped. {error}\n")


if __name__ == "__main__":
    main()
