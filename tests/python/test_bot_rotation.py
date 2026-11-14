"""D4 profiles and proposals are tested without native bindings or game input."""

import hashlib
import io
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path

from rs3_reader.bot.intents import ActivateAbility, Priority
from rs3_reader.bot.replay import replay
from rs3_reader.bot.rotation import RotationEngine, RotationProfiles
from rs3_reader.bot.world_state import Observation, WorldState

ROOT = Path(__file__).resolve().parents[2]
MS = 1_000_000
CLIENT = "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45"


def documents():
    return [
        json.loads(path.read_text())
        for path in sorted((ROOT / "profiles/rotations").glob("*.json"))
    ]


def profiles():
    values = documents()
    for profile in values:
        for binding in profile["manual_bindings"].values():
            binding.update(virtual_key=0x74, validated=True, source="synthetic calibration")
    return RotationProfiles(
        values, json.loads((ROOT / "profiles/ability_bindings_951_3.json").read_text())
    )


def ability(identity=14701, name="Fury", **changes):
    result = {
        "struct_id": identity,
        "name": name,
        "in_ability_book": True,
        "castable": True,
        "equipment_match": "allowed",
        "cooldown": {"remaining_ms": 0, "reason": None},
    }
    result.update(changes)
    return result


def row(phase=1, sequence=1, time_ms=10000, *, abilities=None):
    return {
        "schema_version": 1,
        "status": "available",
        "source": "synthetic_fixture",
        "pid": 1,
        "process_creation_time": 2,
        "executable_sha256": CLIENT,
        "monotonic_ms": time_ms,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": 3},
        "telos": {"phase": phase},
        "gcd": {"remaining_ms": 0, "reason": None},
        "adrenaline_percent": 20,
        "abilities": {
            "status": "available",
            "entries": abilities if abilities is not None else [ability()],
        },
        "equipment": {
            "status": "available",
            "configuration": "dual_wield",
            "main_hand": {"item_id": 200, "equipment": {"weapon": True, "style": "melee"}},
        },
        "player": {"target": {"kind": "npc", "instance_id": 4, "runtime_id": 7}},
        "npcs": {
            "status": "available",
            "entities": [
                {"instance_id": 4, "runtime_id": 7, "health": {"current": 100000, "max": 100000}}
            ],
        },
    }


def world(**kwargs):
    return WorldState.from_telemetry(row(**kwargs))


class RotationTests(unittest.TestCase):
    def engine(self):
        return RotationEngine(profiles())

    def test_all_five_default_profiles_validate_but_do_not_invent_keys(self):
        loaded = RotationProfiles.load()
        self.assertEqual(set(loaded.profiles), {1, 2, 3, 4, 5})
        for phase in range(1, 6):
            engine = RotationEngine(loaded)
            self.assertEqual(engine.step(world(phase=phase)), ())
            self.assertFalse(loaded.profiles[phase]["live_validated"])

    def test_each_phase_proposes_one_legal_ready_ability_at_lowest_priority(self):
        for phase in range(1, 6):
            engine = self.engine()
            (intent,) = engine.step(world(phase=phase))
            self.assertEqual(intent.action, ActivateAbility(14701))
            self.assertEqual(intent.priority, Priority.ROTATION)
            self.assertEqual(engine.last_report.rule, "fury_priority")

    def test_gcd_unknown_adrenaline_castability_timer_or_equipment_never_select(self):
        for change in (
            {"gcd": {"remaining_ms": 600}},
            {"gcd": {"remaining_ms": None}},
            {"adrenaline_percent": None},
        ):
            record = row()
            record.update(change)
            self.assertEqual(self.engine().step(WorldState.from_telemetry(record)), ())
        for change in (
            {"castable": None},
            {"castable": False},
            {"equipment_match": "unknown"},
            {"equipment_match": "blocked"},
            {"cooldown": {"remaining_ms": None, "reason": "timer_mapping_pending"}},
            {"cooldown": {"remaining_ms": 100, "reason": None}},
        ):
            self.assertEqual(self.engine().step(world(abilities=[ability(**change)])), ())

    def test_priority_and_adrenaline_policy_do_not_use_raw_cache_costs(self):
        record = row(abilities=[ability(), ability(14686, "Overpower")])
        record["adrenaline_percent"] = 110
        self.assertEqual(
            self.engine().step(WorldState.from_telemetry(record))[0].action, ActivateAbility(14686)
        )
        record["adrenaline_percent"] = 20
        self.assertEqual(
            self.engine().step(WorldState.from_telemetry(record))[0].action, ActivateAbility(14701)
        )

    def test_unknown_buff_is_not_absent_and_known_buffs_enable_the_channel(self):
        record = row(abilities=[ability(), ability(14704, "Assault")])
        record["adrenaline_percent"] = 60
        self.assertEqual(
            self.engine().step(WorldState.from_telemetry(record))[0].action.ability_id, 14701
        )
        record["player"]["effects"] = {
            "status": "partial",
            "entries": [
                {"id": "berserk", "active": True, "reason": None, "source": "fixture"},
                {
                    "id": "bloodlust",
                    "active": True,
                    "stacks": 1,
                    "reason": None,
                    "source": "fixture",
                },
            ],
        }
        self.assertEqual(
            self.engine().step(WorldState.from_telemetry(record))[0].action.ability_id, 14704
        )

    def test_changed_weapon_or_dead_unknown_target_rejects(self):
        record = row()
        record["equipment"]["main_hand"]["equipment"]["style"] = "magic"
        self.assertEqual(self.engine().step(WorldState.from_telemetry(record)), ())
        for current in (None, 0):
            record = row()
            record["npcs"]["entities"][0]["health"]["current"] = current
            self.assertEqual(self.engine().step(WorldState.from_telemetry(record)), ())
        value = world()
        value = replace(value, player=replace(value.player, target=Observation()))
        self.assertEqual(self.engine().step(value), ())

    def test_live_bindings_take_precedence_and_do_not_fall_back_after_bar_changes(self):
        record = row()
        binding = {
            "kind": "ability",
            "ability_struct_id": 14701,
            "key": "Ctrl+F2",
            "reason": None,
            "binding_reason": None,
            "source": "synthetic_P5",
        }
        record["action_bar"] = {"status": "partial", "entries": [binding]}
        engine = self.engine()
        first = engine.step(WorldState.from_telemetry(record))[0]
        selected = next(
            e.observation.value for e in first.evidence if e.field == "rotation.binding"
        )
        self.assertEqual((selected["virtual_key"], selected["modifiers"]), (0x71, 1))
        binding["binding_reason"] = "key_unmapped"
        self.assertEqual(engine.step(WorldState.from_telemetry(record)), ())

    def test_can_send_rechecks_phase_gear_and_binding(self):
        engine = self.engine()
        value = world()
        intent = engine.step(value)[0]
        self.assertTrue(engine.can_send(intent, value))
        self.assertFalse(engine.can_send(intent, world(phase=2)))
        record = row()
        record["action_bar"] = {
            "status": "partial",
            "entries": [
                {
                    "kind": "ability",
                    "ability_struct_id": 14701,
                    "key": "F6",
                    "reason": None,
                    "binding_reason": None,
                    "source": "fixture",
                }
            ],
        }
        self.assertFalse(engine.can_send(intent, WorldState.from_telemetry(record)))

    def test_only_observed_cooldown_acknowledges_accepted_input(self):
        engine = self.engine()
        value = world()
        intent = engine.step(value)[0]
        engine.note_sent(intent.intent_id, value.now_ns)
        self.assertEqual(engine.step(value), ())
        self.assertEqual(engine.step(world(sequence=2, time_ms=10010)), ())
        record = row(sequence=3, time_ms=10020)
        record["abilities"]["entries"][0]["cooldown"]["remaining_ms"] = 500
        self.assertEqual(engine.step(WorldState.from_telemetry(record)), ())
        self.assertIsNone(engine.last_report.pending_intent)

    def test_rejected_input_does_not_advance_and_timeout_faults_until_reset(self):
        engine = self.engine()
        value = world()
        intent = engine.step(value)[0]
        engine.note_rejected(intent.intent_id, "focus_lost", value.now_ns)
        self.assertEqual(engine.step(value), (intent,))
        engine.note_sent(intent.intent_id, value.now_ns)
        self.assertEqual(engine.step(world(sequence=2, time_ms=12000)), ())
        self.assertEqual(engine.last_report.fault, "acknowledgement_timeout")
        engine.reset()
        self.assertTrue(engine.step(world(sequence=3, time_ms=12010)))

    def test_unknown_phase_or_build_and_stale_snapshot_are_not_inferred(self):
        for phase in (None, 0, 6):
            self.assertEqual(self.engine().step(world(phase=phase)), ())
        record = row()
        record["executable_sha256"] = "a" * 64
        self.assertEqual(self.engine().step(WorldState.from_telemetry(record)), ())
        self.assertEqual(
            self.engine().step(WorldState.from_telemetry(row(), now_ns=10300 * MS)), ()
        )

    def test_profiles_reject_unknown_removed_abilities_and_stale_definitions(self):
        definitions = json.loads((ROOT / "profiles/ability_bindings_951_3.json").read_text())
        for patch in (
            {"ability_id": 123456},
            {"ability_id": 14706, "name": "Destroy"},
            {"minimum_adrenaline_percent": -1},
        ):
            values = documents()
            values[0]["rules"][0].update(patch)
            with self.assertRaises(ValueError):
                RotationProfiles(values, definitions)
        values = documents()
        values[0]["ability_bindings_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            RotationProfiles(values, definitions)

    def test_replay_is_deterministic_and_never_sends_input(self):
        capture = json.dumps(row()) + "\n"
        logs = []
        for _ in range(2):
            output = io.StringIO()
            replay(io.StringIO(capture), output, self.engine())
            logs.append(output.getvalue())
        self.assertEqual(logs[0], logs[1])
        self.assertFalse(json.loads(logs[0])["input_sent"])

    def test_real_capture_without_mapped_castability_never_becomes_an_attack(self):
        path = (
            Path(os.environ.get("RS3_LOCAL_CAPTURE_ROOT", str(ROOT / "work/prayer_research")))
            / "telos-session-native-hits-2026-11-10.jsonl"
        )
        if not path.exists():
            self.skipTest("Raw captures intentionally remain local")
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "37c9c4c99481db3f73bcb355174523cb7b2aba02a7815fd1f9e243004410af23",
        )
        output = io.StringIO()
        with path.open() as stream:
            replay(stream, output, RotationEngine())
        self.assertTrue(
            all(not json.loads(line)["intents"] for line in output.getvalue().splitlines())
        )
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "f2c9a0cfceb6535913e930fe99d9314ea5a734a5355845087eabfaceb046e54b",
        )


if __name__ == "__main__":
    unittest.main()
