"""Synthetic P4/A2 contracts only; recorded hazard calibration is still pending."""

import io
import json
import hashlib
import os
import unittest
from pathlib import Path

from rs3_reader.bot.intents import Interact, MoveTo, Priority
from rs3_reader.bot.mechanics import MechanicsEngine
from rs3_reader.bot.mechanics.geometry import ArenaGeometry, Rule
from rs3_reader.bot.replay import replay
from rs3_reader.bot.world_state import Observation, Tile, WorldState

MS = 1_000_000


def row(phase=1, kind="uppercut", sequence=1, time_ms=1000):
    return {
        "schema_version": 1,
        "status": "available",
        "source": "synthetic_fixture",
        "pid": 1,
        "process_creation_time": 2,
        "executable_sha256": "a" * 64,
        "monotonic_ms": time_ms,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": 3},
        "telos": {"phase": phase, "enrage": 300},
        "position": {"x": 1000, "y": 2000, "plane": 0},
        "arena": {
            "value": {
                "validated": True,
                "session": {"pid": 1, "process_creation_time": 2, "scene_epoch": 3},
                "anchor": {"x": 1000, "y": 2000, "plane": 0},
                "quarter_turns": 0,
                "bounds": [-5, 5, -5, 5],
                "safe_tiles": [[0, 0], [1, 0], [2, 0]],
            },
            "age_ms": 0,
            "source": "synthetic_P4",
        },
        "hazards": {
            "status": "available",
            "entries": [
                {
                    "id": "synthetic-hazard",
                    "kind": kind,
                    "phase": phase,
                    "validated": True,
                    "active": True,
                    "dangerous": True,
                    "tiles": [[0, 0]],
                    "source": "synthetic_P4",
                }
            ],
        },
    }


def engine(phase=1, kind="uppercut", mode="avoid", option=None, **kwargs):
    return MechanicsEngine(
        [Rule(phase, kind, mode, True, "synthetic calibration", option, "a" * 64, (300, 300))],
        path_validator=kwargs.pop(
            "path_validator", lambda *_: Observation(True, 0, "synthetic_A2")
        ),
        interaction_validator=kwargs.pop(
            "interaction_validator", lambda *_: Observation(True, 0, "synthetic_A2")
        ),
        **kwargs,
    )


class MechanicsTests(unittest.TestCase):
    def test_clock_regression_cannot_reissue_an_old_response(self):
        controller = engine()
        controller.step(WorldState.from_telemetry(row()))
        self.assertEqual(
            controller.step(WorldState.from_telemetry(row(sequence=2, time_ms=900))), ()
        )
        self.assertEqual(controller.last_report.fault, "clock_or_sample_regressed")

    def test_real_capture_without_p4_a2_has_a_golden_no_movement_log(self):
        root = Path(__file__).resolve().parents[2]
        path = (
            Path(os.environ.get("RS3_LOCAL_CAPTURE_ROOT", str(root / "work/prayer_research")))
            / "telos-session-native-hits-2026-11-10.jsonl"
        )
        if not path.exists():
            self.skipTest("Raw captures intentionally stay local")
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "37c9c4c99481db3f73bcb355174523cb7b2aba02a7815fd1f9e243004410af23",
        )
        output = io.StringIO()
        with path.open() as stream:
            replay(stream, output, MechanicsEngine())
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "f2c9a0cfceb6535913e930fe99d9314ea5a734a5355845087eabfaceb046e54b",
        )

    def test_calibrated_rules_do_not_transfer_across_client_or_enrage(self):
        for change in ("client", "enrage"):
            record = row()
            if change == "client":
                record["executable_sha256"] = "b" * 64
            else:
                record["telos"]["enrage"] = 500
            self.assertEqual(engine().step(WorldState.from_telemetry(record)), ())

    def test_delayed_replay_ages_the_arena_envelope_too(self):
        record = row()
        record["arena"]["age_ms"] = 100
        value = WorldState.from_telemetry(record, now_ns=1100 * MS)
        self.assertEqual(engine().step(value), ())

    def test_each_phase_handler_requires_calibrated_strategy_and_geometry(self):
        for phase, kind in (
            (1, "uppercut"),
            (2, "black_beam"),
            (3, "meter_extreme"),
            (4, "anima_bomb"),
            (5, "rock_impact"),
        ):
            value = WorldState.from_telemetry(row(phase, kind))
            self.assertEqual(MechanicsEngine().step(value), ())
            (intent,) = engine(phase, kind).step(value)
            self.assertEqual(intent.action, MoveTo(Tile(1001, 2000, 0)))
            self.assertEqual(intent.priority, Priority.MECHANICS)

    def test_anchor_and_rotation_are_relative_not_hardcoded_world_coordinates(self):
        record = row()
        record["arena"]["value"]["quarter_turns"] = 1
        value = WorldState.from_telemetry(record)
        geometry = ArenaGeometry.from_world(value, 150)
        self.assertEqual(geometry.to_local(geometry.to_world((1, 0))), (1, 0))
        self.assertEqual(engine().step(value)[0].action.tile, Tile(1000, 2001, 0))
        record["arena"]["value"]["anchor"]["x"] = 10000
        record["position"]["x"] = 10000
        self.assertEqual(
            engine().step(WorldState.from_telemetry(record))[0].action.tile, Tile(10000, 2001, 0)
        )

    def test_unknown_hazard_or_partial_collection_never_moves(self):
        for change in ("unknown", "partial", "unvalidated", "missing_danger"):
            record = row()
            if change == "unknown":
                record["hazards"]["entries"][0]["kind"] = "unmapped"
            elif change == "partial":
                record["hazards"]["status"] = "partial"
            elif change == "unvalidated":
                record["hazards"]["entries"][0]["validated"] = False
            else:
                record["hazards"]["entries"][0].pop("dangerous")
            self.assertEqual(engine().step(WorldState.from_telemetry(record)), ())

    def test_missing_stale_or_foreign_arena_blocks(self):
        for change in ("missing", "stale", "foreign"):
            record = row()
            if change == "missing":
                record.pop("arena")
            elif change == "stale":
                record["arena"]["age_ms"] = 200
            else:
                record["arena"]["value"]["session"]["scene_epoch"] = 4
            self.assertEqual(engine().step(WorldState.from_telemetry(record)), ())

    def test_no_path_or_unproven_path_never_becomes_movement(self):
        value = WorldState.from_telemetry(row())
        for validator in (
            None,
            lambda *_: True,
            lambda *_: Observation(False, 0, "A2"),
            lambda *_: Observation(),
        ):
            self.assertEqual(engine(path_validator=validator).step(value), ())

    def test_enter_beam_selects_only_safe_beam_cells(self):
        record = row(kind="green_beam")
        hazard = record["hazards"]["entries"][0]
        hazard.update(dangerous=False, tiles=[[2, 0]])
        value = WorldState.from_telemetry(record)
        self.assertEqual(
            engine(kind="green_beam", mode="enter").step(value)[0].action.tile, Tile(1002, 2000, 0)
        )

    def test_font_interaction_needs_known_entity_option_and_validator(self):
        record = row(4, "font")
        hazard = record["hazards"]["entries"][0]
        hazard.update(
            entity={"kind": "object", "instance_id": 99}, options=["Charge"], dangerous=False
        )
        value = WorldState.from_telemetry(record)
        (intent,) = engine(4, "font", "interact", "Charge").step(value)
        self.assertIsInstance(intent.action, Interact)
        self.assertEqual(engine(4, "font", "interact", "Wrong").step(value), ())
        self.assertEqual(
            engine(4, "font", "interact", "Charge", interaction_validator=None).step(value), ()
        )

    def test_target_switch_requires_a_living_observed_npc(self):
        record = row(3, "golem_target")
        hazard = record["hazards"]["entries"][0]
        hazard.update(entity={"kind": "npc", "instance_id": 7}, options=["Attack"], dangerous=False)
        record["npcs"] = {
            "status": "available",
            "entities": [{"instance_id": 7, "health": {"current": 1000, "max": 1000}}],
        }
        value = WorldState.from_telemetry(record)
        self.assertIsInstance(
            engine(3, "golem_target", "target", "Attack").step(value)[0].action, Interact
        )
        record["npcs"]["entities"][0]["health"]["current"] = None
        self.assertEqual(
            engine(3, "golem_target", "target", "Attack").step(WorldState.from_telemetry(record)),
            (),
        )

    def test_accepted_move_waits_for_newer_native_position(self):
        controller = engine()
        value = WorldState.from_telemetry(row())
        intent = controller.step(value)[0]
        controller.note_sent(intent.intent_id, value.now_ns)
        self.assertEqual(controller.step(value), ())
        record = row(sequence=2, time_ms=1010)
        record["position"]["x"] = 1001
        current = WorldState.from_telemetry(record)
        self.assertTrue(controller.confirm(intent, value, current).value)
        self.assertEqual(controller.step(current), ())

    def test_changed_hazard_invalidates_old_response(self):
        controller = engine()
        value = WorldState.from_telemetry(row())
        intent = controller.step(value)[0]
        self.assertTrue(controller.can_send(intent, value))
        changed = row()
        changed["hazards"]["entries"][0]["active"] = False
        self.assertFalse(controller.can_send(intent, WorldState.from_telemetry(changed)))

    def test_unconfirmed_input_timeout_latches_a_fault(self):
        controller = engine()
        value = WorldState.from_telemetry(row())
        intent = controller.step(value)[0]
        controller.note_sent(intent.intent_id, value.now_ns)
        self.assertEqual(
            controller.step(WorldState.from_telemetry(row(sequence=2, time_ms=3000))), ()
        )
        self.assertEqual(controller.last_report.fault, "confirmation_timeout")

    def test_shadow_replay_with_default_leads_emits_no_guessed_actions(self):
        output = io.StringIO()
        replay(io.StringIO(json.dumps(row()) + "\n"), output, MechanicsEngine())
        self.assertEqual(json.loads(output.getvalue())["intents"], [])


if __name__ == "__main__":
    unittest.main()
