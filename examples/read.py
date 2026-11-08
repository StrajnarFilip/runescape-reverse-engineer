"""Minimal Python consumer. Run from anywhere after building rs3_native."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python" / "rs3_reader"))
from native import Reader

with Reader(worker_hz=100) as reader:
    for _ in range(100):
        info, player, npcs, hits = reader.sample()
        events, lost = reader.drain_events()
        hp = player.raw_variables[0] if player.valid_variables & 1 else None
        print("sample=%d status=%d HP=%s NPCs=%d new_hits=%d lost=%d read=%.3fms" %
              (info.sequence, info.status, hp, info.npc_count, len(events), lost, info.sample_duration_ns/1e6))
        time.sleep(.05)
