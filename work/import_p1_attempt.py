"""Convert a local P1 JSONL capture to lossless, versioned training-data deltas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rs3_reader.bot.telemetry_attempt import TelemetryAttemptLog


def main():
    """Import decoded records only; never overwrite an existing attempt file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    if arguments.source.resolve() == arguments.destination.resolve():
        parser.error("Source and destination must differ")
    arguments.destination.parent.mkdir(parents=True, exist_ok=True)
    with arguments.source.open(encoding="utf-8-sig") as source:
        with arguments.destination.open("x", encoding="utf-8", newline="\n") as destination:
            writer = TelemetryAttemptLog(destination)
            for line in source:
                if line.strip():
                    writer.record(json.loads(line))
    print(f"Imported {writer.sequence} decoded records to {arguments.destination}")


if __name__ == "__main__":
    main()
