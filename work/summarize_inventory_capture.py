"""Summarize actual C calibration observations without publishing raw captures."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/"work"/"inventory_native_validation.jsonl"
data=source.read_bytes()
rows=[json.loads(line) for line in data.decode().splitlines() if line]
items=[];timers=[];adjustments=[];pairs={};equipment=[]
for row in rows:
    if "equipment" in row:
        equipment=row["equipment"] or []
    for e in row["inventory_changes"]["newly_observed"]:
        before,after=e.get("before"),e.get("after")
        if not any(x and (x.get("item_id") in (22332,3028,3030) or x.get("doses") is not None) for x in (before,after)):
            continue
        def compact(item):
            return {k:item[k] for k in ("container_id","slot","slot_number","item_id","quantity","name","family_id","doses","tracking_token")} if item else None
        items.append({"observed_at_ms":e["observed_at_ms"],"kind":e["kind"],"before":compact(before),"after":compact(after)})
    for a in row["timer_transitions"]:
        if a["name"] not in ("Flurry","Hurricane","Resonance"):
            continue
        cd=a["cooldown"]
        if cd["remaining_ms"] is not None and cd["remaining_ms"]>0 and a["equipment_match"]=="allowed":
            timers.append({"observed_at_ms":row["timestamp_ms"],"name":a["name"],"struct_id":a["struct_id"],
                "configuration":row["equipment_configuration"],"requirements":a["requirements"],"cooldown":cd,
                "equipped":[{k:i[k] for k in ("slot","item_id","name")} for i in equipment if not i["empty"] and i["slot"] in (3,5)]})
        pair=(cd["start_tick"],cd["end_tick"])
        old=pairs.get(a["name"])
        running=cd["remaining_ms"] is not None and cd["remaining_ms"]>0
        if old and old[1] and running and all(x is not None for x in (*old[0],*pair)) and old[0]!=pair:
            adjustments.append({"name":a["name"],"observed_at_ms":row["timestamp_ms"],"before_start_end_ticks":old[0],
                                "after_start_end_ticks":pair,"remaining_ms":cd["remaining_ms"],
                                "interpretation":"Client adjusted its cooldown pair; cause not identified"})
        pairs[a["name"]]=(pair,running)
manifest=json.loads((ROOT/"outputs"/"rs3_native"/"build"/"rs3_catalog.manifest.json").read_text())
bindings=json.loads((ROOT/"outputs"/"rs3_reader"/"ability_bindings_951_3.json").read_text())
report={"captured_at_utc":datetime.fromtimestamp(rows[0]["timestamp_ms"]/1000,timezone.utc).isoformat(),
        "source":"Actual C collector, 100 Hz; Python consumer approximately 20 Hz",
        "capture_sha256":hashlib.sha256(data).hexdigest(),"capture_rows":len(rows),
        "manual_confirmation":"User confirmed Hurricane, Flurry, Resonance, Wicked hood movement and another restore dose",
        "catalog":manifest,"ability_definitions":len(bindings["abilities"]),
        "client_timer_mappings":sum((a["timer_source"] or '').startswith('client_symbol_pair') for a in bindings["abilities"]),
        "inventory_examples":items,"ability_examples":timers,"observed_timer_adjustments":adjustments,
        "inventory_events_lost":sum(row["inventory_changes"]["events_lost"] for row in rows),
        "final_library_sha256":hashlib.sha256((ROOT/"outputs"/"rs3_native"/"build"/"rs3_native.dll").read_bytes()).hexdigest(),
        "validation_limits":["Only Hurricane, Flurry and Resonance were exercised in this live calibration",
            "Other symbolic timer pairs are not individually combat-validated; missing/unmapped values remain null",
            "Bone Shield substitution and full cast eligibility (runes, unlocks, target, level, adrenaline) remain unevaluated",
            "Identical duplicate items have no exposed unique serial number; tracking identity is inferred",
            "Logical inventory slots are exact; screen pixel positions are not measured",
            "Visual UI inspection was not performed; window creation and logs checked"]}
out=ROOT/"outputs"/"rs3_reader"/"inventory-validation-summary.json"
out.write_bytes((json.dumps(report,indent=2)+"\n").encode())
assert any(e["kind"]=="doses_changed" and e["before"]["tracking_token"]==e["after"]["tracking_token"] for e in items)
assert {e["name"] for e in timers}=={"Hurricane","Flurry","Resonance"}
print("Report:",out,"item examples",len(items),"ability examples",len(timers),"timer adjustments",len(adjustments))
