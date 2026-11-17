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
from rs3_reader.bot.rotation import OrderedP1Rotation, RotationEngine, RotationProfiles
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
        "player": {
            "target": {"kind": "npc", "instance_id": 4, "runtime_id": 7, "config_id": 22891}
        },
        "npcs": {
            "status": "available",
            "entities": [
                {
                    "instance_id": 4,
                    "runtime_id": 7,
                    "config_id": 22891,
                    "health": {"current": 100000, "max": 100000},
                }
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

    def test_target_requires_complete_matching_npc_lifetime_runtime_and_config(self):
        for field in ("instance_id", "runtime_id", "config_id"):
            record = row()
            record["player"]["target"].pop(field)
            with self.subTest(missing=field):
                self.assertEqual(self.engine().step(WorldState.from_telemetry(record)), ())
            record = row()
            record["npcs"]["entities"][0][field] += 1
            with self.subTest(changed=field):
                self.assertEqual(self.engine().step(WorldState.from_telemetry(record)), ())

    def test_live_target_switch_cannot_reuse_an_old_offensive_intent(self):
        engine = self.engine()
        intent = engine.step(world())[0]
        record = row(sequence=2, time_ms=10001)
        record["player"]["target"]["instance_id"] = 5
        record["player"]["target"]["runtime_id"] = 8
        record["npcs"]["entities"].append(
            dict(record["npcs"]["entities"][0], instance_id=5, runtime_id=8)
        )
        changed = WorldState.from_telemetry(record)
        self.assertFalse(engine.can_send(intent, changed))
        self.assertTrue(self.engine().step(changed))  # Fresh policy may consider the new target.

    def test_known_intent_id_cannot_authorize_a_replaced_ability(self):
        engine = self.engine()
        value = world(abilities=[ability(), ability(14686, "Overpower")])
        intent = engine.step(value)[0]
        self.assertTrue(engine.can_send(intent, value))
        self.assertFalse(engine.can_send(replace(intent, action=ActivateAbility(14686)), value))

    def test_stale_or_duplicate_exact_target_cannot_authorize_a_rotation(self):
        value = world()
        target = value.npcs.value[0]
        stale = replace(target, config_id=Observation(22891, 151, "fixture"))
        for npcs in ((stale,), (target, target)):
            with self.subTest(npcs=npcs):
                self.assertEqual(
                    self.engine().step(replace(value, npcs=Observation(npcs, 0, "fixture"))), ()
                )

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


class OrderedP1Tests(unittest.TestCase):
    def frame(self, engine, sequence=1, time_ms=10000, *, dormant=None, selected=True):
        record = row(sequence=sequence, time_ms=time_ms)
        dormant = engine.index < 2 if dormant is None else dormant
        if dormant:
            record["telos"] = {"phase": None}
            record["npcs"]["entities"][0]["config_id"] = 22892
        if dormant or not selected:
            record["player"]["target"] = {"kind": "none"}
        return WorldState.from_telemetry(record)

    def ready(self, engine):
        return {
            a["id"]: Observation(True, 0, "synthetic_native_readiness")
            for a in engine.current_step["actions"]
        }

    def send_step(self, engine, sample):
        batch = engine.step(sample, self.ready(engine))
        self.assertIsNotNone(batch, (engine.reasons, engine.fault))
        engine.note_sent(batch, [a["id"] for a in batch.actions], sample.now_ns)
        return batch

    def confirm_step(self, engine, batch, sample):
        for action in batch.actions:
            if action.get("completion") == "attempt":
                continue
            self.assertTrue(
                engine.confirm(
                    action["id"], Observation(True, 0, "synthetic_native_specific_ack"), sample
                )
            )

    def advance_opening(self, engine):
        for sequence in (1, 2):
            batch = self.send_step(engine, self.frame(engine, sequence, 10000 + sequence * 50))
            self.confirm_step(
                engine,
                batch,
                self.frame(engine, sequence + 1, 10000 + (sequence + 1) * 50, dormant=True),
            )
        self.assertEqual(engine.current_step["id"], "wake_target_cycle_vulnerability_barge")

    def test_order_and_equipment_keep_the_requested_distinctions(self):
        engine = OrderedP1Rotation()
        steps = engine.document["steps"]
        self.assertEqual([a["id"] for a in steps[1]["actions"]], ["berserk", "renewal"])
        self.assertTrue(steps[1]["same_tick"])
        self.assertEqual([a["key"] for a in steps[2]["actions"]], ["A", "S", "G"])
        self.assertTrue(steps[2]["same_tick"])
        self.assertFalse(steps[2]["requires_target"])
        self.assertEqual(steps[-3]["actions"][0]["kind"], "weapon_special")
        self.assertEqual(steps[-3]["actions"][0]["required_weapon_id"], 52543)
        self.assertEqual(steps[-1]["actions"][0]["kind"], "eof_special")
        self.assertEqual(steps[-1]["actions"][0]["required_amulet_id"], 51065)
        self.assertEqual(steps[-1]["actions"][0]["stored_weapon_source"], "user")
        self.assertEqual(steps[0]["manual_action"]["lane"], "off_gcd")
        self.assertNotIn("surge", [a["id"] for s in steps for a in s["actions"]])

    def test_opening_accepts_dormant_telos_without_selected_target(self):
        engine = OrderedP1Rotation()
        self.advance_opening(engine)
        self.assertIsNone(
            engine.step(self.frame(engine, 3, 10150, dormant=True), self.ready(engine))
        )
        self.assertEqual(engine.reasons, ("waiting_for_native_wake_candidate",))
        batch = engine.step(
            self.frame(engine, 4, 10200, dormant=False, selected=False), self.ready(engine)
        )
        self.assertEqual(
            [a["id"] for a in batch.actions], ["target_cycle", "vulnerability", "greater_barge"]
        )

    def test_late_attachment_or_missed_wake_does_not_guess_a_delay(self):
        engine = OrderedP1Rotation()
        self.assertIsNone(engine.step(world(), self.ready(engine)))
        self.assertEqual(engine.fault, "dormant_start_not_observed")
        engine = OrderedP1Rotation()
        self.advance_opening(engine)
        self.assertIsNone(engine.step(self.frame(engine, 4, 10200, dormant=False)))
        self.assertIsNone(
            engine.step(self.frame(engine, 5, 10400, dormant=False), self.ready(engine))
        )
        self.assertEqual(engine.fault, "wake_window_missed")

    def test_unknown_readiness_never_skips_a_step(self):
        engine = OrderedP1Rotation()
        self.assertIsNone(engine.step(self.frame(engine)))
        self.assertEqual(engine.reasons, ("action_not_ready:devotion",))
        self.assertIsNone(
            engine.step(self.frame(engine), {"devotion": Observation(True, 151, "stale")})
        )
        self.assertEqual(engine.index, 0)

    def test_input_acceptance_same_frame_and_duplicate_ack_do_not_advance(self):
        engine = OrderedP1Rotation()
        sample = self.frame(engine)
        batch = self.send_step(engine, sample)
        self.assertEqual(engine.index, 0)
        self.assertFalse(engine.confirm("devotion", Observation(True, 0, "native"), sample))
        self.confirm_step(engine, batch, self.frame(engine, 2, 10050))
        self.assertEqual(engine.index, 1)
        self.assertFalse(
            engine.confirm("devotion", Observation(True, 0, "native"), self.frame(engine, 2, 10050))
        )

    def test_berserk_requires_readback_but_renewal_attempt_does_not_require_consumption(self):
        engine = OrderedP1Rotation()
        batch = self.send_step(engine, self.frame(engine))
        self.confirm_step(engine, batch, self.frame(engine, 2, 10050))
        batch = self.send_step(engine, self.frame(engine, 2, 10050))
        sample = self.frame(engine, 3, 10100)
        self.assertTrue(engine.confirm("berserk", Observation(True, 0, "native"), sample))
        self.assertEqual(engine.index, 2)
        self.assertFalse(engine.confirm("renewal", Observation(None, 0, "native"), sample))
        self.assertEqual(engine.current_step["id"], "wake_target_cycle_vulnerability_barge")

    def test_partial_or_reordered_submission_latches_and_does_not_retry(self):
        for accepted in (("berserk",), ("renewal", "berserk")):
            engine = OrderedP1Rotation()
            batch = self.send_step(engine, self.frame(engine))
            self.confirm_step(engine, batch, self.frame(engine, 2, 10050))
            batch = engine.step(self.frame(engine, 2, 10050), self.ready(engine))
            engine.note_sent(batch, accepted, batch.issued_ns)
            self.assertEqual(engine.fault, "partial_or_reordered_batch")
            self.assertIsNone(engine.step(self.frame(engine, 3, 10100), self.ready(engine)))

    def test_target_cycle_ack_requires_the_exact_selected_telos(self):
        engine = OrderedP1Rotation()
        self.advance_opening(engine)
        batch = self.send_step(engine, self.frame(engine, 4, 10200, dormant=False, selected=False))
        missing = self.frame(engine, 5, 10250, dormant=False, selected=False)
        self.assertFalse(engine.confirm("target_cycle", Observation(True, 0, "native"), missing))
        self.confirm_step(engine, batch, self.frame(engine, 5, 10250, dormant=False))
        self.assertEqual(engine.current_step["id"], "meteor_strike")

    def test_session_replacement_and_timeout_do_not_advance(self):
        engine = OrderedP1Rotation()
        self.send_step(engine, self.frame(engine))
        self.assertIsNone(engine.step(self.frame(engine, 2, 13000)))
        self.assertEqual(engine.fault, "acknowledgement_timeout")
        self.assertEqual(engine.index, 0)
        engine = OrderedP1Rotation()
        engine.step(self.frame(engine))
        changed = row(sequence=2, time_ms=10050)
        changed["process_creation_time"] = 3
        self.assertIsNone(engine.step(WorldState.from_telemetry(changed)))
        self.assertEqual(engine.fault, "session_changed")

    def test_all_steps_finish_only_after_individual_newer_native_acknowledgements(self):
        engine = OrderedP1Rotation()
        sequence, offered = 1, []
        while not engine.complete:
            sample = self.frame(engine, sequence, 10000 + sequence * 50)
            batch = self.send_step(engine, sample)
            offered.extend(a["id"] for a in batch.actions)
            sequence += 1
            self.confirm_step(engine, batch, self.frame(engine, sequence, 10000 + sequence * 50))
        self.assertEqual(
            offered[-4:], ["equip_ezk", "ezk_special", "equip_red_eof", "dragon_scimitar_special"]
        )
        self.assertIsNone(engine.current_step)
        self.assertIsNone(engine.step(world(sequence=sequence, time_ms=10000 + sequence * 50)))


if __name__ == "__main__":
    unittest.main()
