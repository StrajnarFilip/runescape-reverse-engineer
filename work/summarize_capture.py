"""Create a compact public validation report from existing local captures.

Only gameplay values and aggregate timing are exported, never process addresses,
account information or full memory/read logs. Run from the repository root.
"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
names = ["combat_capture.jsonl", "combat_clocks.jsonl", "combat_clocks2.jsonl"]
sources = []
starts = {}
for name in names:
    path = root / "work" / name
    if not path.exists():
        continue
    data = path.read_bytes()
    records = [json.loads(line) for line in data.splitlines() if line.strip()]
    good = [r for r in records if r.get("health") and r.get("adrenaline_percent")]
    health = [r["health"]["current"]["value"] for r in good if r["health"]["current"]["value"] is not None]
    adrenaline = [r["adrenaline_percent"]["value"] for r in good if r["adrenaline_percent"]["value"] is not None]
    sources.append({"local_capture_name": name, "sha256": hashlib.sha256(data).hexdigest(),
                    "sample_count": len(records),
                    "health_range": [min(health), max(health)] if health else None,
                    "adrenaline_percent_range": [min(adrenaline), max(adrenaline)] if adrenaline else None,
                    "first_observed_at_ms": good[0]["timestamp_ms"] if good else None,
                    "last_observed_at_ms": good[-1]["timestamp_ms"] if good else None})
    for record in good:
        gcd = record.get("gcd") or {}
        clock = record.get("clocks", {}).get("0x528", gcd.get("clock_raw"))
        start, end = gcd.get("client_start_raw"), gcd.get("client_end_raw")
        if start is None or end is None or clock is None or not 0 <= clock-start <= 5:
            continue
        key = (start, end)
        observation = {"start_client_tick": start, "end_client_tick": end,
                       "clock_at_observation": clock, "duration_ticks": end-start,
                       "duration_ms_at_20ms_per_tick": (end-start)*20,
                       "adrenaline_percent": record["adrenaline_percent"]["value"],
                       "health": record["health"]["current"]["value"]}
        if key not in starts or clock < starts[key]["clock_at_observation"]:
            starts[key] = observation

report = {"schema_version": 1, "source": "actual_local_read_only_memory_captures",
          "client_version": "951-3",
          "client_sha256": "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45",
          "limitations": ["Raw captures remain local and are not included in this repository.",
                          "Overlapping captures must not be summed as independent time periods.",
                          "NPCs, hitsplats and Telos phase/animation decoding are not validated.",
                          "Logout, world-hop and client restart remain to be tested."],
          "captures": sources, "gcd_starts_observed": sorted(starts.values(), key=lambda x:x["start_client_tick"])}
destination = root / "outputs" / "rs3_reader" / "validation-summary.json"
destination.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
print("Wrote", destination.name, "from", len(sources), "captures and", len(starts), "GCD starts")
