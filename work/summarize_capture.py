"""Create a compact public validation report from existing local player captures.

Only gameplay values and aggregate timing are exported, never process
addresses, account information or full memory/read logs. Writes
docs/validation/player-validation.json. Run from the repository root.
"""

import hashlib
import json

from workspace import PRIMARY_CLIENT_SHA256, VALIDATION_DIRECTORY, WORK_DIRECTORY

CAPTURE_NAMES = ("combat_capture.jsonl", "combat_clocks.jsonl", "combat_clocks2.jsonl")
TICK_MS = 20
# A GCD start observed within this many ticks of its start tick is a fresh start.
MAX_START_OBSERVATION_DELAY_TICKS = 5


def value_range(values):
    return [min(values), max(values)] if values else None


def summarize_capture(name, data, records, usable_records):
    health = [
        record["health"]["current"]["value"]
        for record in usable_records
        if record["health"]["current"]["value"] is not None
    ]
    adrenaline = [
        record["adrenaline_percent"]["value"]
        for record in usable_records
        if record["adrenaline_percent"]["value"] is not None
    ]
    return {
        "local_capture_name": name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "sample_count": len(records),
        "health_range": value_range(health),
        "adrenaline_percent_range": value_range(adrenaline),
        "first_observed_at_ms": usable_records[0]["timestamp_ms"] if usable_records else None,
        "last_observed_at_ms": usable_records[-1]["timestamp_ms"] if usable_records else None,
    }


def collect_gcd_starts(usable_records, gcd_starts):
    """Keep the earliest observation of each (start, end) client GCD pair."""
    for record in usable_records:
        gcd = record.get("gcd") or {}
        clock = record.get("clocks", {}).get("0x528", gcd.get("clock_raw"))
        start_tick, end_tick = gcd.get("client_start_raw"), gcd.get("client_end_raw")
        if start_tick is None or end_tick is None or clock is None:
            continue
        if not 0 <= clock - start_tick <= MAX_START_OBSERVATION_DELAY_TICKS:
            continue
        observation = {
            "start_client_tick": start_tick,
            "end_client_tick": end_tick,
            "clock_at_observation": clock,
            "duration_ticks": end_tick - start_tick,
            "duration_ms_at_20ms_per_tick": (end_tick - start_tick) * TICK_MS,
            "adrenaline_percent": record["adrenaline_percent"]["value"],
            "health": record["health"]["current"]["value"],
        }
        key = (start_tick, end_tick)
        if key not in gcd_starts or clock < gcd_starts[key]["clock_at_observation"]:
            gcd_starts[key] = observation


def main():
    captures = []
    gcd_starts = {}
    for name in CAPTURE_NAMES:
        path = WORK_DIRECTORY / name
        if not path.exists():
            continue
        data = path.read_bytes()
        records = [json.loads(line) for line in data.splitlines() if line.strip()]
        usable_records = [
            record
            for record in records
            if record.get("health") and record.get("adrenaline_percent")
        ]
        captures.append(summarize_capture(name, data, records, usable_records))
        collect_gcd_starts(usable_records, gcd_starts)

    report = {
        "schema_version": 1,
        "source": "actual_local_read_only_memory_captures",
        "client_version": "951-3",
        "client_sha256": PRIMARY_CLIENT_SHA256,
        "limitations": [
            "Raw captures remain local and are not included in this repository.",
            "Overlapping captures must not be summed as independent time periods.",
            "NPCs, hitsplats and Telos phase/animation decoding are not validated.",
            "Logout, world-hop and client restart remain to be tested.",
        ],
        "captures": captures,
        "gcd_starts_observed": sorted(
            gcd_starts.values(), key=lambda observation: observation["start_client_tick"]
        ),
    }
    destination = VALIDATION_DIRECTORY / "player-validation.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "Wrote", destination.name, "from", len(captures), "captures and",
        len(gcd_starts), "GCD starts",
    )


if __name__ == "__main__":
    main()
