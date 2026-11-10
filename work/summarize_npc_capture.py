"""Publish selected real NPC observations while keeping complete captures local.

Writes docs/validation/npc-validation-summary.json from work/npc_combat_capture.jsonl.
"""

import hashlib
import json

from workspace import PRIMARY_CLIENT_SHA256, VALIDATION_DIRECTORY, WORK_DIRECTORY

SOURCE_PATH = WORK_DIRECTORY / "npc_combat_capture.jsonl"
CALIBRATION_NPCS = ("Goblin", "Giant spider")
# Hit amounts the user reported during the calibration fight.
USER_REPORTED_AMOUNTS = (224, 550, 1000)
GOBLIN_MAX_HP = 550
GOBLIN_HP_AFTER_HIT = 326
HASH_CHUNK_BYTES = 1024 * 1024
HIT_IDENTITY_FIELDS = ("runtime_id", "config_id", "slot", "type_id", "amount", "end_client_tick")
HIT_EXAMPLE_FIELDS = (
    "name",
    "runtime_id",
    "config_id",
    "amount",
    "type_id",
    "start_client_tick",
    "end_client_tick",
)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


class CaptureSummary:
    def __init__(self):
        self.sample_count = 0
        self.first_observed_at_ms = None
        self.last_observed_at_ms = None
        self.seen_hits = set()
        self.hit_examples = {}
        self.previous_health = {}
        self.transitions = []
        self.full_health_examples = {}

    def observe_entities(self, entities, timestamp_ms):
        for entity in entities:
            if entity.get("name") not in CALIBRATION_NPCS:
                continue
            health = entity["health"]
            key = (entity["runtime_id"], entity["config_id"])
            if health["current"] is None:
                continue
            if health["current"] == health["max"]:
                self.full_health_examples[entity["name"]] = {
                    "config_id": entity["config_id"],
                    "current_hp": health["current"],
                    "max_hp": health["max"],
                }
            was_full = self.previous_health.get(key) == GOBLIN_MAX_HP
            if was_full and health["current"] == GOBLIN_HP_AFTER_HIT:
                self.transitions.append(
                    {
                        "name": entity["name"],
                        "runtime_id": entity["runtime_id"],
                        "config_id": entity["config_id"],
                        "before_hp": GOBLIN_MAX_HP,
                        "after_hp": GOBLIN_HP_AFTER_HIT,
                        "difference": GOBLIN_MAX_HP - GOBLIN_HP_AFTER_HIT,
                        "observed_at_ms": timestamp_ms,
                    }
                )
            self.previous_health[key] = health["current"]

    def observe_hits(self, hits, entities_by_runtime_id, timestamp_ms):
        for hit in hits:
            key = tuple(hit.get(field) for field in HIT_IDENTITY_FIELDS)
            if key in self.seen_hits:
                continue
            self.seen_hits.add(key)
            if hit.get("name") not in CALIBRATION_NPCS:
                continue
            if hit["amount"] not in USER_REPORTED_AMOUNTS:
                continue
            if hit["amount"] in self.hit_examples:
                continue
            entity = entities_by_runtime_id.get(hit["runtime_id"])
            example = {field: hit[field] for field in HIT_EXAMPLE_FIELDS}
            example.update(
                observed_at_ms=timestamp_ms,
                health_in_same_sample=entity["health"] if entity else None,
            )
            self.hit_examples[hit["amount"]] = example

    def observe(self, record):
        self.sample_count += 1
        timestamp_ms = record["timestamp_ms"]
        if self.first_observed_at_ms is None:
            self.first_observed_at_ms = timestamp_ms
        self.last_observed_at_ms = timestamp_ms
        entities = (record.get("npcs") or {}).get("entities") or []
        entities_by_runtime_id = {entity["runtime_id"]: entity for entity in entities}
        self.observe_entities(entities, timestamp_ms)
        hits = (record.get("hit_splats") or {}).get("newly_observed") or []
        self.observe_hits(hits, entities_by_runtime_id, timestamp_ms)


def main():
    summary = CaptureSummary()
    with SOURCE_PATH.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            summary.observe(record)
    report = {
        "schema_version": 1,
        "source": "actual_local_native_memory_capture",
        "local_capture_name": SOURCE_PATH.name,
        "capture_sha256": file_sha256(SOURCE_PATH),
        "client_version": "951-3",
        "client_sha256": PRIMARY_CLIENT_SHA256,
        "sample_count": summary.sample_count,
        "first_observed_at_ms": summary.first_observed_at_ms,
        "last_observed_at_ms": summary.last_observed_at_ms,
        "user_reported_hit_amounts": list(USER_REPORTED_AMOUNTS),
        "full_health_examples": summary.full_health_examples,
        "matching_observed_hits": [
            summary.hit_examples[amount]
            for amount in USER_REPORTED_AMOUNTS
            if amount in summary.hit_examples
        ],
        "observed_hp_transitions_matching_224": summary.transitions,
        "limitations": [
            "Amounts are raw hitmark values; some types represent healing or text marks.",
            "The ring identifies the recipient, not the attacker.",
            "Polling can miss overwritten or removed records.",
            "Raw captures remain local. "
            "Telos phase and animation classification are not validated.",
        ],
    }
    destination = VALIDATION_DIRECTORY / "npc-validation-summary.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "Samples",
        summary.sample_count,
        "matching amounts",
        sorted(summary.hit_examples),
        "matching HP transitions",
        len(summary.transitions),
    )


if __name__ == "__main__":
    main()
