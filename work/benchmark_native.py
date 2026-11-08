"""Opt-in live C/Python parity and timing check; writes a compact report only.

Run from repository root after building the DLL. Does not send game inputs.
Results describe the current loaded scene, not every client or combat workload.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"outputs"/"rs3_reader"))
from telemetry import PythonTelemetry
from native_telemetry import NativeTelemetry


def stats(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(ordered), 4),
            "p95_ms": round(ordered[int((len(ordered)-1)*.95)], 4),
            "max_ms": round(max(ordered), 4)}


def comparable(sample):
    if sample["status"] == "unavailable" or (sample.get("npcs") or {}).get("status") != "available":
        return None
    raw = sample["raw_variables"]
    return {"variables": raw,
            "npcs": sorted([(e["runtime_id"], e["config_id"], e["name"], e["health"]["current"], e["health"]["max"])
                            for e in sample["npcs"]["entities"]])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=200)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--output", type=Path, default=ROOT/"outputs"/"rs3_native"/"validation-summary.json")
    args = parser.parse_args()
    if args.pairs<1 or args.seconds<=0:
        parser.error("Use positive pairs and seconds")
    python, native = PythonTelemetry(), NativeTelemetry(worker_hz=0)
    durations_py, durations_c, engine_c = [], [], []
    stable = matched = unstable = 0
    failures = []
    try:
        for _ in range(5):
            python.sample(True); native.sample(True)
        for _ in range(args.pairs):
            start = time.perf_counter_ns(); before = python.sample(True)
            durations_py.append((time.perf_counter_ns()-start)/1e6)
            start = time.perf_counter_ns(); current = native.sample(True)
            durations_c.append((time.perf_counter_ns()-start)/1e6)
            engine_c.append(current["read_duration_ms"])
            after = python.sample(True)
            a, b, c = comparable(before), comparable(current), comparable(after)
            if a is not None and a == c:
                stable += 1
                if a == b:
                    matched += 1
                else:
                    failures.append("Stable pair %d differed" % (stable+unstable))
            else:
                unstable += 1
        # Count one Python sample's remote API reads separately from timings.
        old_read = python.memory.read
        counters = [0, 0]

        def counted(address, size):
            counters[0] += 1
            counters[1] += size
            return old_read(address, size)

        python.memory.read = counted
        python.sample(True)
        python.memory.read = old_read
        info = native.reader.info
        report = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                  "client_version": current["client_version"], "executable_sha256": current["executable_sha256"],
                  "abi_version": 1, "pairs": args.pairs, "stable_pairs": stable,
                  "matched_stable_pairs": matched, "unstable_pairs_excluded": unstable,
                  "mismatches": failures, "loaded_npcs": current["npcs"]["loaded_count"],
                  "comparison": "All 10 raw variables and NPC runtime/config/name/current/max HP, bracketed by Python reads",
                  "python_end_to_end": stats(durations_py), "c_with_python_adapter": stats(durations_c),
                  "c_engine": stats(engine_c), "python_remote_reads": counters[0],
                  "python_remote_bytes": counters[1], "c_remote_reads": info.read_calls,
                  "c_remote_bytes": info.read_bytes, "worker_checks": []}
        report["workloads"]={"python":"Player/NPC reference decoder",
                             "c":"Player/NPC decoder plus inventory, equipment and %d ability definitions/timers" %
                             len((current.get("abilities") or {}).get("entries",[]))}
    finally:
        python.close(); native.close()
    for hz in (100, 500, 1000):
        worker = NativeTelemetry(worker_hz=hz)
        try:
            time.sleep(.1)
            initial = worker.sample()
            start = time.perf_counter_ns()
            reads = lost = 0
            last = initial
            while (time.perf_counter_ns()-start)/1e9 < args.seconds:
                time.sleep(.05)  # Deliberately slower consumer than collector.
                last = worker.sample()
                reads += 1; lost += last["native"]["events_lost"]
            elapsed = (time.perf_counter_ns()-start)/1e9
            delta = last["native"]["sample_sequence"]-initial["native"]["sample_sequence"]
            report["worker_checks"].append({"requested_hz": hz, "elapsed_seconds": round(elapsed, 3),
                "samples": delta, "observed_hz": round(delta/elapsed, 1), "consumer_refreshes": reads,
                "last_read_ms": last["read_duration_ms"], "last_age_ms": last["native"]["snapshot_age_ms"],
                "events_lost": lost, "loaded_npcs": last["npcs"]["loaded_count"]})
        finally:
            worker.close()
    report["library_sha256"] = hashlib.sha256((ROOT/"outputs"/"rs3_native"/"build"/"rs3_native.dll").read_bytes()).hexdigest()
    report["limitations"] = ["Current scene only; full Telos combat load not benchmarked",
                             "This timing run does not establish complete combat hitmark coverage; dedicated live/fixture evidence is separate",
                             "Requested rate is not guaranteed under system load",
                             "Logout/world-hop/restart still need controlled live validation"]
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failures or stable == 0:
        raise SystemExit("Parity validation failed")


if __name__ == "__main__":
    main()
