"""Record compact inventory events and timer transitions from the C collector."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "python" / "rs3_reader"))
from native_telemetry import NativeTelemetry

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output",type=Path,default=ROOT/"work"/"inventory_native_validation.jsonl")
parser.add_argument("--duration",type=float,default=900)
args=parser.parse_args()
telemetry=NativeTelemetry(worker_hz=100)
previous={}
last_inventory=None
try:
    with args.output.open("w",encoding="utf-8") as output:
        end=time.perf_counter()+args.duration
        while time.perf_counter()<end:
            s=telemetry.sample()
            changes=[]
            for a in s["abilities"]["entries"]:
                cd=a["cooldown"];value=(cd["start_tick"],cd["end_tick"],cd["remaining_ms"]==0)
                if value!=previous.get(a["struct_id"]):
                    changes.append(a)
                    previous[a["struct_id"]]=value
            inventory=(s["inventory"].get("slots"),s["equipment"].get("slots"))
            snapshot_changed=inventory!=last_inventory
            if changes or snapshot_changed or s["inventory_changes"]["newly_observed"]:
                record={"timestamp_ms":s["timestamp_ms"],"native":s.get("native"),"inventory_changes":s["inventory_changes"],
                        "timer_transitions":changes,"equipment_configuration":s["equipment"]["configuration"]}
                if snapshot_changed:record["inventory"],record["equipment"]=inventory
                output.write(json.dumps(record,separators=(",",":"))+"\n");output.flush()
                last_inventory=inventory
            time.sleep(.05)
finally:
    telemetry.close()
