"""Opt-in live C/Python parity and timing check; writes a compact report only.

Run from the repository root after building the DLL. Sends no game input.
Results describe the current loaded scene, not every client or combat workload.
"""

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from workspace import VALIDATION_DIRECTORY, add_import_paths

add_import_paths(package=True, reference=True)

from reference_telemetry import PythonTelemetry  # noqa: E402
from rs3_reader.native import library_path  # noqa: E402
from rs3_reader.telemetry import NativeTelemetry  # noqa: E402

WARM_UP_SAMPLES = 5
WORKER_RATES_HZ = (100, 500, 1000)
# A deliberately slower consumer than the collector.
CONSUMER_PERIOD_SECONDS = 0.05


def timing_statistics(durations_ms):
    ordered = sorted(durations_ms)
    return {
        "median_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 4),
        "max_ms": round(max(ordered), 4),
    }


def comparable_fields(sample):
    """Return the fields both decoders must agree on, or None for an unusable sample."""
    if sample["status"] == "unavailable" or (sample.get("npcs") or {}).get("status") != "available":
        return None
    npcs = sorted(
        (
            entity["runtime_id"],
            entity["config_id"],
            entity["name"],
            entity["health"]["current"],
            entity["health"]["max"],
        )
        for entity in sample["npcs"]["entities"]
    )
    return {"variables": sample["raw_variables"], "npcs": npcs}


def timed_ms(function):
    started = time.perf_counter_ns()
    result = function()
    return result, (time.perf_counter_ns() - started) / 1e6


def count_python_remote_reads(python_telemetry):
    """Return (read calls, bytes) of one Python reference sample."""
    original_read = python_telemetry.memory.read
    counters = {"calls": 0, "bytes": 0}

    def counting_read(address, size):
        counters["calls"] += 1
        counters["bytes"] += size
        return original_read(address, size)

    python_telemetry.memory.read = counting_read
    try:
        python_telemetry.sample(True)
    finally:
        python_telemetry.memory.read = original_read
    return counters["calls"], counters["bytes"]


def compare_decoders(parser, arguments):
    """Bracket each native sample with reference reads to exclude changing scenes."""
    python_telemetry, native_telemetry = PythonTelemetry(), NativeTelemetry(worker_hz=0)
    python_durations, adapter_durations, engine_durations = [], [], []
    stable_pairs = matched_pairs = unstable_pairs = 0
    mismatches = []
    try:
        if native_telemetry.sample()["client_version"] != "951-3":
            parser.error("The combat benchmark requires a verified 951-3 client")
        for _ in range(WARM_UP_SAMPLES):
            python_telemetry.sample(True)
            native_telemetry.sample(True)
        for _ in range(arguments.pairs):
            before, python_ms = timed_ms(lambda: python_telemetry.sample(True))
            current, adapter_ms = timed_ms(lambda: native_telemetry.sample(True))
            python_durations.append(python_ms)
            adapter_durations.append(adapter_ms)
            engine_durations.append(current["read_duration_ms"])
            after = python_telemetry.sample(True)
            before_fields = comparable_fields(before)
            current_fields = comparable_fields(current)
            after_fields = comparable_fields(after)
            if before_fields is not None and before_fields == after_fields:
                stable_pairs += 1
                if before_fields == current_fields:
                    matched_pairs += 1
                else:
                    mismatches.append(f"Stable pair {stable_pairs + unstable_pairs} differed")
            else:
                unstable_pairs += 1
        python_read_calls, python_read_bytes = count_python_remote_reads(python_telemetry)
        info = native_telemetry.reader.info
        report = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "client_version": current["client_version"],
            "executable_sha256": current["executable_sha256"],
            "abi_version": 1,
            "pairs": arguments.pairs,
            "stable_pairs": stable_pairs,
            "matched_stable_pairs": matched_pairs,
            "unstable_pairs_excluded": unstable_pairs,
            "mismatches": mismatches,
            "loaded_npcs": current["npcs"]["loaded_count"],
            "comparison": (
                "All 10 raw variables and NPC runtime/config/name/current/max HP, "
                "bracketed by Python reads"
            ),
            "python_end_to_end": timing_statistics(python_durations),
            "c_with_python_adapter": timing_statistics(adapter_durations),
            "c_engine": timing_statistics(engine_durations),
            "python_remote_reads": python_read_calls,
            "python_remote_bytes": python_read_bytes,
            "c_remote_reads": info.read_calls,
            "c_remote_bytes": info.read_bytes,
            "worker_checks": [],
        }
        ability_count = len((current.get("abilities") or {}).get("entries", []))
        report["workloads"] = {
            "python": "Player/NPC reference decoder",
            "c": (
                "Player/NPC decoder plus inventory, equipment and "
                f"{ability_count} ability definitions/timers"
            ),
        }
    finally:
        python_telemetry.close()
        native_telemetry.close()
    return report, mismatches, stable_pairs


def check_worker_rate(worker_hz, seconds):
    worker = NativeTelemetry(worker_hz=worker_hz)
    try:
        time.sleep(0.1)
        initial = worker.sample()
        started = time.perf_counter_ns()
        consumer_refreshes = events_lost = 0
        last = initial
        while (time.perf_counter_ns() - started) / 1e9 < seconds:
            time.sleep(CONSUMER_PERIOD_SECONDS)
            last = worker.sample()
            consumer_refreshes += 1
            events_lost += last["native"]["events_lost"]
        elapsed_seconds = (time.perf_counter_ns() - started) / 1e9
        samples = last["native"]["sample_sequence"] - initial["native"]["sample_sequence"]
        return {
            "requested_hz": worker_hz,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "samples": samples,
            "observed_hz": round(samples / elapsed_seconds, 1),
            "consumer_refreshes": consumer_refreshes,
            "last_read_ms": last["read_duration_ms"],
            "last_age_ms": last["native"]["snapshot_age_ms"],
            "events_lost": events_lost,
            "loaded_npcs": last["npcs"]["loaded_count"],
        }
    finally:
        worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pairs", type=int, default=200)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument(
        "--output", type=Path, default=VALIDATION_DIRECTORY / "native-validation.json"
    )
    arguments = parser.parse_args()
    if arguments.pairs < 1 or arguments.seconds <= 0:
        parser.error("Use positive pairs and seconds")

    report, mismatches, stable_pairs = compare_decoders(parser, arguments)
    report["worker_checks"] = [
        check_worker_rate(worker_hz, arguments.seconds) for worker_hz in WORKER_RATES_HZ
    ]
    report["library_sha256"] = hashlib.sha256(library_path().read_bytes()).hexdigest()
    report["limitations"] = [
        "Current scene only; full Telos combat load not benchmarked",
        "This timing run does not establish complete combat hitmark coverage; "
        "dedicated live/fixture evidence is separate",
        "Requested rate is not guaranteed under system load",
        "Logout/world-hop/restart still need controlled live validation",
    ]
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if mismatches or stable_pairs == 0:
        raise SystemExit("Parity validation failed")


if __name__ == "__main__":
    main()
