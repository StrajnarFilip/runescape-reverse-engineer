"""Summarize real C inventory calibration observations without publishing raw captures.

Reads work/inventory_native_validation.jsonl and the catalog manifest beside the
DLL; writes docs/validation/inventory-validation-summary.json.
"""

import hashlib
import json
from datetime import datetime, timezone

from workspace import PROFILES_DIRECTORY, VALIDATION_DIRECTORY, WORK_DIRECTORY, add_import_paths

add_import_paths(package=True)

from rs3_reader.native import library_path  # noqa: E402

SOURCE_PATH = WORK_DIRECTORY / "inventory_native_validation.jsonl"
# Items handled during the calibration: Wicked hood and two restore potions.
CALIBRATION_ITEM_IDS = (22332, 3028, 3030)
CALIBRATION_ABILITIES = ("Flurry", "Hurricane", "Resonance")
HAND_SLOTS = (3, 5)
COMPACT_ITEM_FIELDS = (
    "container_id",
    "slot",
    "slot_number",
    "item_id",
    "quantity",
    "name",
    "family_id",
    "doses",
    "tracking_token",
)


def compact_item(item):
    return {field: item[field] for field in COMPACT_ITEM_FIELDS} if item else None


def is_calibration_change(change):
    return any(
        item and (item.get("item_id") in CALIBRATION_ITEM_IDS or item.get("doses") is not None)
        for item in (change.get("before"), change.get("after"))
    )


def timer_is_running(cooldown):
    return cooldown["remaining_ms"] is not None and cooldown["remaining_ms"] > 0


class CalibrationSummary:
    def __init__(self):
        self.item_examples = []
        self.ability_examples = []
        self.timer_adjustments = []
        self.previous_timers = {}
        # Capture rows omit unchanged gear, so keep the last recorded equipment list.
        self.equipment = []

    def observe_item_changes(self, changes):
        for change in changes:
            if not is_calibration_change(change):
                continue
            self.item_examples.append(
                {
                    "observed_at_ms": change["observed_at_ms"],
                    "kind": change["kind"],
                    "before": compact_item(change.get("before")),
                    "after": compact_item(change.get("after")),
                }
            )

    def observe_ability(self, row, ability):
        cooldown = ability["cooldown"]
        running = timer_is_running(cooldown)
        if running and ability["equipment_match"] == "allowed":
            equipped_hands = [
                {field: item[field] for field in ("slot", "item_id", "name")}
                for item in self.equipment
                if not item["empty"] and item["slot"] in HAND_SLOTS
            ]
            self.ability_examples.append(
                {
                    "observed_at_ms": row["timestamp_ms"],
                    "name": ability["name"],
                    "struct_id": ability["struct_id"],
                    "configuration": row["equipment_configuration"],
                    "requirements": ability["requirements"],
                    "cooldown": cooldown,
                    "equipped": equipped_hands,
                }
            )
        endpoints = (cooldown["start_tick"], cooldown["end_tick"])
        previous = self.previous_timers.get(ability["name"])
        # Track endpoints that changed while a timer kept running across observations.
        if previous:
            previous_endpoints, previous_running = previous
            endpoints_known = all(tick is not None for tick in (*previous_endpoints, *endpoints))
            if previous_running and running and endpoints_known and previous_endpoints != endpoints:
                self.timer_adjustments.append(
                    {
                        "name": ability["name"],
                        "observed_at_ms": row["timestamp_ms"],
                        "before_start_end_ticks": previous_endpoints,
                        "after_start_end_ticks": endpoints,
                        "remaining_ms": cooldown["remaining_ms"],
                        "interpretation": "Client adjusted its cooldown pair; cause not identified",
                    }
                )
        self.previous_timers[ability["name"]] = (endpoints, running)

    def observe(self, row):
        if "equipment" in row:
            self.equipment = row["equipment"] or []
        self.observe_item_changes(row["inventory_changes"]["newly_observed"])
        for ability in row["timer_transitions"]:
            if ability["name"] in CALIBRATION_ABILITIES:
                self.observe_ability(row, ability)


def main():
    data = SOURCE_PATH.read_bytes()
    rows = [json.loads(line) for line in data.decode().splitlines() if line]
    summary = CalibrationSummary()
    for row in rows:
        summary.observe(row)
    manifest = json.loads(library_path().with_name("rs3_catalog.manifest.json").read_text())
    bindings = json.loads((PROFILES_DIRECTORY / "ability_bindings_951_3.json").read_text())
    client_timer_mappings = sum(
        (ability["timer_source"] or "").startswith("client_symbol_pair")
        for ability in bindings["abilities"]
    )
    first_timestamp = datetime.fromtimestamp(rows[0]["timestamp_ms"] / 1000, timezone.utc)
    report = {
        "captured_at_utc": first_timestamp.isoformat(),
        "source": "Actual C collector, 100 Hz; Python consumer approximately 20 Hz",
        "capture_sha256": hashlib.sha256(data).hexdigest(),
        "capture_rows": len(rows),
        "manual_confirmation": (
            "User confirmed Hurricane, Flurry, Resonance, Wicked hood movement "
            "and another restore dose"
        ),
        "catalog": manifest,
        "ability_definitions": len(bindings["abilities"]),
        "client_timer_mappings": client_timer_mappings,
        "inventory_examples": summary.item_examples,
        "ability_examples": summary.ability_examples,
        "observed_timer_adjustments": summary.timer_adjustments,
        "inventory_events_lost": sum(row["inventory_changes"]["events_lost"] for row in rows),
        "final_library_sha256": hashlib.sha256(library_path().read_bytes()).hexdigest(),
        "validation_limits": [
            "Only Hurricane, Flurry and Resonance were exercised in this live calibration",
            "Other symbolic timer pairs are not individually combat-validated; "
            "missing/unmapped values remain null",
            "Bone Shield substitution and full cast eligibility "
            "(runes, unlocks, target, level, adrenaline) remain unevaluated",
            "Identical duplicate items have no exposed unique serial number; "
            "tracking identity is inferred",
            "Logical inventory slots are exact; screen pixel positions are not measured",
            "Visual UI inspection was not performed; window creation and logs checked",
        ],
    }
    destination = VALIDATION_DIRECTORY / "inventory-validation-summary.json"
    destination.write_bytes((json.dumps(report, indent=2) + "\n").encode())
    assert any(
        example["kind"] == "doses_changed"
        and example["before"]["tracking_token"] == example["after"]["tracking_token"]
        for example in summary.item_examples
    )
    assert {example["name"] for example in summary.ability_examples} == set(CALIBRATION_ABILITIES)
    print(
        "Report:",
        destination,
        "item examples",
        len(summary.item_examples),
        "ability examples",
        len(summary.ability_examples),
        "timer adjustments",
        len(summary.timer_adjustments),
    )


if __name__ == "__main__":
    main()
