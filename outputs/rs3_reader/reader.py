"""Stream local RS3 telemetry as JSON Lines. Requires 64-bit Windows Python."""
import argparse
import json
from pathlib import Path
import sys
import time

from telemetry import Telemetry

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--hz", type=float, default=20)
    parser.add_argument("--duration", type=float, help="Stop after this many seconds")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--raw", action="store_true", help="Include the decoded variable IDs' raw values")
    parser.add_argument("--output", type=Path, help="Write JSONL to this file instead of stdout")
    args = parser.parse_args()
    if not 0.1 <= args.hz <= 100 or (args.duration is not None and args.duration <= 0):
        parser.error("Use 0.1 <= hz <= 100 and a positive duration")
    output = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    telemetry = Telemetry(args.pid)
    began = deadline = time.perf_counter()
    try:
        while args.duration is None or time.perf_counter()-began < args.duration:
            record = telemetry.sample(args.raw)
            output.write(json.dumps(record, separators=(",", ":"), allow_nan=False)+"\n")
            output.flush()
            if args.once:
                break
            deadline += 1/args.hz
            delay = deadline-time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -1:
                deadline = time.perf_counter()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        telemetry.close()
        if args.output:
            output.close()

if __name__ == "__main__":
    main()
