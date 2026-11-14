"""D7 trip scenarios use synthetic mapped controls; no pointer backend is loaded."""

import io
import json
import hashlib
import os
import unittest
from pathlib import Path

from rs3_reader.bot.intents import Interact, MoveTo
from rs3_reader.bot.replay import replay
from rs3_reader.bot.trip_loop import TripLoop, validate_profile
from rs3_reader.bot.world_state import Observation, WorldState

ROOT = Path(__file__).resolve().parents[2]
MS = 1_000_000


def profile():
    p = json.loads((ROOT / "profiles/trip.json").read_text())
    p.update(
        configured=True,
        source="synthetic fixture",
        practice=True,
        preset_id=1,
        expected_inventory=[{"slot": 0, "item_id": 100, "minimum_quantity": 1}],
        expected_equipment=[{"slot": 3, "item_id": 200, "minimum_quantity": 1}],
    )
    p["bank"] = {
        "tile": {"x": 100, "y": 200, "plane": 0},
        "target": {"kind": "object", "instance_id": 99},
        "option": "Bank",
        "validated": True,
        "source": "synthetic target",
    }
    p["entrance"] = {
        "target": {"kind": "object", "instance_id": 199},
        "option": "Enter",
        "validated": True,
        "source": "synthetic target",
    }
    p["route"] = [
        {
            "kind": "walk",
            "tile": {"x": 110, "y": 200, "plane": 0},
            "validated": True,
            "source": "synthetic route",
        }
    ]
    p["return_route"] = [
        {
            "kind": "walk",
            "tile": {"x": 100, "y": 200, "plane": 0},
            "validated": True,
            "source": "synthetic route",
        }
    ]
    p["loot_policy"] = {"choice": "claim", "validated": True, "source": "synthetic policy"}
    return p


def control(role, value=None):
    return {
        "role": role,
        "value": value,
        "validated": True,
        "visible": True,
        "source": "synthetic_P5",
        "target": {"kind": "interface", "instance_id": 300 + len(role)},
        "option": role,
    }


def panel(kind, controls=()):
    return {
        "kind": kind,
        "open": True,
        "visible": True,
        "reason": None,
        "source": "synthetic_P5",
        "controls": list(controls),
    }


class Harness:
    def __init__(self, configuration=None, validators=True):
        self.clock = [0]
        self.controller = TripLoop(
            configuration or profile(),
            path_validator=(
                (lambda *_: Observation(True, 0, "synthetic_A2")) if validators else None
            ),
            interaction_validator=(
                (lambda *_: Observation(True, 0, "synthetic_A2")) if validators else None
            ),
            wall_clock=lambda: self.clock[0],
        )
        self.record = {
            "schema_version": 1,
            "source": "synthetic_fixture",
            "status": "available",
            "pid": 1,
            "process_creation_time": 2,
            "executable_sha256": "a" * 64,
            "monotonic_ms": 1000,
            "native": {"sample_sequence": 1, "snapshot_age_ms": 0},
            "combat": {"scene_epoch": 3},
            "health": {"current": 10000, "max": 10000},
            "prayer": {"current": 990, "max": 990},
            "position": {"x": 100, "y": 200, "plane": 0},
            "inventory": {
                "status": "available",
                "slots": [
                    {
                        "slot": s,
                        "item_id": 100 if s == 0 else None,
                        "quantity": 2 if s == 0 else 0,
                        "empty": s != 0,
                    }
                    for s in range(28)
                ],
            },
            "equipment": {
                "status": "available",
                "slots": [{"slot": 3, "item_id": 200, "quantity": 1}],
            },
            "ui": {
                "status": "partial",
                "open": [],
                "required": [
                    {"kind": "bank_preset", "open": False, "reason": None, "source": "synthetic_P5"}
                ],
            },
            "instance_setup": {
                "value": {"enrage": 0, "practice": True, "validated": True},
                "age_ms": 0,
                "source": "synthetic_P5",
            },
        }
        self.value = None

    def step(self, advance_ms=10):
        self.record["native"]["sample_sequence"] += 1
        self.record["monotonic_ms"] += advance_ms
        self.value = WorldState.from_telemetry(self.record)
        return self.controller.step(self.value)

    def accept(self):
        proposed = self.step()
        assert len(proposed) == 1, (self.controller.last_report, proposed)
        self.controller.note_sent(proposed[0].intent_id, self.value.now_ns)
        return proposed[0]

    def ui(self, kind=None, controls=()):
        self.record["ui"]["open"] = [panel(kind, controls)] if kind else []
        self.record["ui"]["required"] = [
            {
                "kind": "bank_preset",
                "open": kind == "bank_preset",
                "reason": None,
                "source": "synthetic_P5",
            }
        ]


class TripTests(unittest.TestCase):
    def test_clock_regression_stops_without_another_action(self):
        h = Harness()
        h.step()
        h.record["monotonic_ms"] = 800
        self.assertEqual(h.step(), ())
        self.assertEqual(h.controller.last_report.fault, "clock_or_sample_regressed")

    def test_real_capture_with_unconfigured_route_has_a_golden_no_input_log(self):
        path = (
            Path(os.environ.get("RS3_LOCAL_CAPTURE_ROOT", str(ROOT / "work/prayer_research")))
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
            replay(stream, output, TripLoop())
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "f2c9a0cfceb6535913e930fe99d9314ea5a734a5355845087eabfaceb046e54b",
        )

    def test_new_outbound_leg_does_not_reuse_the_previous_route_index(self):
        h = Harness()
        h.controller.state = "CLOSE_BANK"
        h.controller.route_index = 1
        h.step()
        self.assertEqual(h.controller.state, "TRAVEL")
        self.assertEqual(h.controller.route_index, 0)
        self.assertIsInstance(h.step()[0].action, MoveTo)

    def test_continue_requires_fresh_next_enrage_streak_and_supplies(self):
        p = profile()
        p["maximum_kills"] = 3
        p["maximum_streak"] = 2
        p["loot_policy"]["choice"] = "continue_if_ready"
        h = Harness(p)
        h.controller.state = "LOOT"
        h.controller.kills = 1
        h.controller._result_id = "kill1"
        h.record["telos"] = {"streak": 1}
        h.record["next_instance_enrage"] = {
            "value": {"result_id": "kill1", "enrage": 340, "validated": True},
            "age_ms": 0,
            "source": "synthetic_P5",
        }
        h.ui("loot", [control("continue"), control("claim")])
        offer = h.accept()
        self.assertEqual(offer.action.option, "continue")
        h.record["telos"].update(phase=1, enrage=340)
        h.step()
        self.assertEqual(h.controller.state, "FIGHT")
        h = Harness(p)
        h.controller.state = "LOOT"
        h.controller.kills = 1
        h.controller._result_id = "kill1"
        h.ui("loot", [control("continue"), control("claim")])
        self.assertEqual(h.accept().action.option, "claim")

    def test_recovery_waits_for_a_result_specific_postcondition(self):
        p = profile()
        p["death_policy"] = {
            "action": "recover",
            "validated": True,
            "source": "synthetic calibration",
        }
        h = Harness(p)
        h.controller.state = "DEATH"
        h.controller._result_id = "death1"
        h.ui("death", [control("recover")])
        h.accept()
        self.assertEqual(h.step(), ())
        h.record["recovered"] = {
            "value": {"result_id": "death1", "complete": True, "validated": True},
            "age_ms": 0,
            "source": "synthetic_P5",
        }
        h.step()
        self.assertEqual(h.controller.state, "BANK")

    def test_delayed_snapshot_does_not_refresh_instance_setup_age(self):
        h = Harness()
        h.controller.state = "ENRAGE"
        h.ui("enrage", [control("set_enrage", 300)])
        h.record["instance_setup"]["age_ms"] = 100
        value = WorldState.from_telemetry(h.record, now_ns=1100 * MS)
        self.assertEqual(h.controller.step(value), ())

    def test_duplicate_result_id_does_not_count_a_second_kill(self):
        h = Harness()
        h.controller.state = "FIGHT"
        h.record["encounter_result"] = {
            "value": {"kind": "kill", "id": "kill1", "validated": True},
            "age_ms": 0,
            "source": "synthetic_result",
        }
        h.step()
        self.assertEqual(h.controller.kills, 1)
        h.controller.state = "FIGHT"
        h.step()
        self.assertEqual(h.controller.kills, 1)

    def test_default_unconfigured_profile_proposes_no_route_or_loot_guess(self):
        h = Harness()
        self.assertEqual(TripLoop().step(WorldState.from_telemetry(h.record)), ())

    def test_normal_trip_advances_only_through_observed_postconditions(self):
        h = Harness()
        h.ui("bank_preset", [control("load_preset", 1), control("close")])
        h.record["inventory"]["slots"][0]["item_id"] = 101
        h.step()
        self.assertEqual(h.controller.state, "PRESET")
        self.assertIsInstance(h.accept().action, Interact)
        h.record["inventory"]["slots"][0]["item_id"] = 100
        h.step()
        self.assertEqual(h.controller.state, "CLOSE_BANK")
        h.accept()
        h.ui()
        h.step()
        self.assertEqual(h.controller.state, "TRAVEL")
        self.assertIsInstance(h.accept().action, MoveTo)
        h.record["position"]["x"] = 110
        h.step()
        h.step()
        h.step()
        self.assertEqual(h.controller.state, "INSTANCE")
        h.accept()
        h.ui("enrage", [control("set_enrage", 300), control("enter")])
        h.step()
        self.assertEqual(h.controller.state, "ENRAGE")
        h.accept()
        h.record["instance_setup"]["value"]["enrage"] = 300
        h.step()
        h.step()
        self.assertEqual(h.controller.state, "ENTER")
        h.accept()
        h.record["telos"] = {"phase": 1, "enrage": 300}
        h.record["combat"]["scene_epoch"] = 4
        h.step()
        self.assertEqual(h.controller.state, "FIGHT")
        h.record["encounter_result"] = {
            "value": {"kind": "kill", "id": "kill1", "validated": True},
            "age_ms": 0,
            "source": "synthetic_result",
        }
        h.step()
        self.assertEqual(h.controller.state, "LOOT")
        h.ui("loot", [control("claim")])
        h.record["telos"]["streak"] = 1
        h.accept()
        h.record["loot_claimed"] = {
            "value": {"result_id": "kill1", "complete": True, "validated": True},
            "age_ms": 0,
            "source": "synthetic_P5",
        }
        h.step()
        h.step()
        self.assertTrue(h.controller.last_report.stopped)
        self.assertEqual(h.controller.last_report.reasons, ("kill_limit",))
        self.assertIsNone(h.controller.last_report.fault)

    def test_preset_that_did_not_load_times_out_without_reclicking(self):
        h = Harness()
        h.controller.state = "PRESET"
        h.ui("bank_preset", [control("load_preset", 1)])
        h.record["inventory"]["slots"][0]["item_id"] = 101
        h.accept()
        self.assertEqual(h.step(), ())
        h.step(6000)
        self.assertEqual(h.controller.last_report.fault, "postcondition_timeout:supplies")

    def test_dialog_that_did_not_open_stops_instead_of_clicking_blindly(self):
        h = Harness()
        h.controller.state = "INSTANCE"
        h.accept()
        h.step(6000)
        self.assertEqual(h.controller.last_report.fault, "postcondition_timeout:instance_open")

    def test_death_is_not_treated_as_a_kill_or_automatic_recovery(self):
        h = Harness()
        h.controller.state = "FIGHT"
        h.record["encounter_result"] = {
            "value": {"kind": "death", "id": "death1", "validated": True},
            "age_ms": 0,
            "source": "synthetic_result",
        }
        h.step()
        self.assertEqual(h.controller.state, "DEATH")
        self.assertEqual(h.step(), ())
        self.assertEqual(h.controller.last_report.fault, "player_death")
        self.assertEqual(h.controller.kills, 0)

    def test_each_policy_stop_condition_is_observed_not_assumed(self):
        h = Harness()
        h.clock[0] = 1800
        h.step()
        self.assertEqual(h.controller.last_report.reasons, ("wall_time_limit",))
        h = Harness()
        h.record["telos"] = {"enrage": 4001}
        h.step()
        self.assertEqual(h.controller.last_report.fault, "enrage_limit")
        h = Harness()
        h.record["telos"] = {"streak": 1}
        h.step()
        self.assertEqual(h.controller.last_report.fault, "streak_limit")

    def test_root_open_without_visible_controls_cannot_load_a_preset(self):
        h = Harness()
        h.controller.state = "PRESET"
        h.record["inventory"]["slots"][0]["item_id"] = 101
        h.ui("bank_preset", [control("load_preset", 1)])
        h.record["ui"]["open"][0]["visible"] = None
        self.assertEqual(h.step(), ())

    def test_missing_a2_and_partial_inventory_do_not_authorize_entry(self):
        h = Harness(validators=False)
        h.controller.state = "TRAVEL"
        self.assertEqual(h.step(), ())
        h = Harness()
        h.controller.state = "ENTER"
        h.record["inventory"]["status"] = "partial"
        self.assertEqual(h.step(), ())
        self.assertEqual(h.controller.last_report.fault, "preset_changed_before_enter")

    def test_unknown_instance_setup_does_not_default_to_target_enrage(self):
        h = Harness()
        h.controller.state = "ENRAGE"
        h.record.pop("instance_setup")
        h.ui("enrage", [control("set_enrage", 300)])
        self.assertEqual(h.step(), ())

    def test_unexpected_scene_change_does_not_resume_the_trip(self):
        h = Harness()
        h.step()
        h.record["combat"]["scene_epoch"] = 4
        self.assertEqual(h.step(), ())
        self.assertEqual(h.controller.last_report.fault, "unexpected_session_or_build_change")

    def test_profile_rejects_bad_bounds_and_inventory_slots(self):
        p = profile()
        p["maximum_enrage"] = 299
        with self.assertRaises(ValueError):
            validate_profile(p)
        p = profile()
        p["expected_inventory"][0]["slot"] = 28
        with self.assertRaises(ValueError):
            validate_profile(p)

    def test_replay_with_unconfigured_profile_has_no_input_proposals(self):
        h = Harness()
        output = io.StringIO()
        replay(io.StringIO(json.dumps(h.record) + "\n"), output, TripLoop())
        self.assertEqual(json.loads(output.getvalue())["intents"], [])


if __name__ == "__main__":
    unittest.main()
