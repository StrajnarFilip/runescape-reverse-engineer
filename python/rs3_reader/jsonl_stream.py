"""Stream local RS3 telemetry as JSON Lines. Requires 64-bit Windows Python.

Usage::

    python -m rs3_reader.jsonl_stream --native-hz 500 --hz 20 --duration 60 --raw --output out.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .telemetry import NativeTelemetry

MIN_OUTPUT_HZ = 0.1
MAX_OUTPUT_HZ = 1000
MIN_NATIVE_HZ = 1
MAX_NATIVE_HZ = 1000
# Reset the schedule after falling this far behind instead of bursting records.
MAX_SCHEDULE_LAG_SECONDS = 1


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", type=int, help="Use a specific rs2client.exe process ID")
    parser.add_argument(
        "--native-hz",
        type=int,
        default=100,
        help="Native worker rate, independent of the JSON output rate",
    )
    parser.add_argument("--hz", type=float, default=20, help="JSON output rate")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds")
    parser.add_argument("--once", action="store_true", help="Write one record and exit")
    parser.add_argument("--raw", action="store_true", help="Include the raw variable values")
    parser.add_argument("--output", type=Path, help="Write JSONL to this file instead of stdout")
    arguments = parser.parse_args()
    if (
        not MIN_OUTPUT_HZ <= arguments.hz <= MAX_OUTPUT_HZ
        or not MIN_NATIVE_HZ <= arguments.native_hz <= MAX_NATIVE_HZ
        or (arguments.duration is not None and arguments.duration <= 0)
    ):
        parser.error("Use 0.1 <= hz <= 1000, native-hz 1..1000 and a positive duration")
    return arguments


def stream(telemetry, output, arguments):
    """Write records at the requested rate until the duration elapses."""
    started = deadline = time.perf_counter()
    while arguments.duration is None or time.perf_counter() - started < arguments.duration:
        record = telemetry.sample(arguments.raw)
        output.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
        output.flush()
        if arguments.once:
            break
        deadline += 1 / arguments.hz
        delay = deadline - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        elif delay < -MAX_SCHEDULE_LAG_SECONDS:
            deadline = time.perf_counter()


def main():
    arguments = parse_arguments()
    output = arguments.output.open("w", encoding="utf-8") if arguments.output else sys.stdout
    telemetry = NativeTelemetry(arguments.pid, arguments.native_hz)
    try:
        stream(telemetry, output, arguments)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        telemetry.close()
        if arguments.output:
            output.close()


if __name__ == "__main__":
    main()
