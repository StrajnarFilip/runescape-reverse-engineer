"""Minimal Python consumer. Run from anywhere after building rs3_native."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from rs3_reader.native import Reader, Variable  # noqa: E402

SAMPLE_COUNT = 100
SAMPLE_PERIOD_SECONDS = 0.05


def main():
    with Reader(worker_hz=100) as reader:
        for _ in range(SAMPLE_COUNT):
            info, player, _npcs, _hits = reader.sample()
            events, lost = reader.drain_events()
            print(
                f"sample={info.sequence} status={info.status} "
                f"HP={player.variable(Variable.HEALTH)} NPCs={info.npc_count} "
                f"new_hits={len(events)} lost={lost} read={info.sample_duration_ns / 1e6:.3f}ms"
            )
            time.sleep(SAMPLE_PERIOD_SECONDS)


if __name__ == "__main__":
    main()
