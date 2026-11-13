"""Supervisor watchdogs release a fake device; no game or hotkey is opened."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import patch

from rs3_reader.bot.actuator import RecordingActuator
from rs3_reader.bot.replay import replay
from rs3_reader.bot.supervisor import (
    Supervisor,
    create_shadow_supervisor,
    hotkey_codes,
    load_producers,
)
from rs3_reader.bot.world_state import WorldState

ROOT = Path(__file__).resolve().parents[2]
MS = 1_000_000


def row(sequence=1, time_ms=1000):
    return {
        "schema_version": 1,
        "source": "synthetic_fixture",
        "status": "available",
        "pid": 1,
        "process_creation_time": 2,
        "executable_sha256": "a" * 64,
        "monotonic_ms": time_ms,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": 3},
        "npcs": {"status": "available", "entities": []},
        "npc_action_events": [],
        "npc_action_events_lost": 0,
        "hit_splats": {"newly_observed": [], "events_lost": 0},
        "player_hit_splats": {"newly_observed": [], "events_lost": 0},
        "inventory_changes": {"newly_observed": [], "events_lost": 0},
    }


def world(**kwargs):
    return WorldState.from_telemetry(row(**kwargs))


class HeldRecordingDevice(RecordingActuator):
    def __init__(self):
        super().__init__()
        self.held, self.closes = {"synthetic-held-key"}, 0

    def close(self):
        self.held.clear()
        self.closes += 1
        super().close()


class SupervisorTests(unittest.TestCase):
    def test_delayed_replay_does_not_make_old_result_fresh_again(self):
        record = row()
        record["encounter_result"] = {
            "value": {"kind": "kill", "id": "old", "validated": True},
            "age_ms": 100,
            "source": "synthetic_result",
        }
        value = WorldState.from_telemetry(record, now_ns=1100 * MS)
        controller, device = self.make()
        controller.step(value)
        self.assertEqual(controller.kills, 0)

    def make(self, **kwargs):
        device = HeldRecordingDevice()
        controller = Supervisor(actuator=device, **kwargs)
        self.addCleanup(controller.close)
        return controller, device

    def assert_stopped(self, controller, device, reason):
        self.assertTrue(controller.stopped)
        self.assertIn(reason, controller.reason)
        self.assertFalse(device.held)
        self.assertEqual(device.closes, 1)
        controller.step(world())
        self.assertEqual(device.closes, 1)

    def test_stale_snapshot_stops_and_releases(self):
        controller, device = self.make(native_clock=lambda: 1300 * MS)
        controller.step(world())
        self.assert_stopped(controller, device, "stale")

    def test_all_four_native_queue_gaps_and_unknown_counters_stop(self):
        for section in (
            "npc_action_events_lost",
            "hit_splats",
            "player_hit_splats",
            "inventory_changes",
        ):
            for loss in (None, 1):
                record = row()
                if section == "npc_action_events_lost":
                    record[section] = loss
                else:
                    record[section]["events_lost"] = loss
                controller, device = self.make()
                controller.step(WorldState.from_telemetry(record))
                self.assert_stopped(controller, device, "event_queue_loss")

    def test_process_scene_and_build_changes_stop(self):
        for key in ("pid", "process_creation_time", "epoch", "executable_sha256"):
            controller, device = self.make()
            controller.step(world())
            record = row(2, 1010)
            if key == "epoch":
                record["combat"]["scene_epoch"] = 4
            elif key == "executable_sha256":
                record[key] = "b" * 64
            else:
                record[key] += 1
            controller.step(WorldState.from_telemetry(record))
            self.assert_stopped(controller, device, "changed")

    def test_lost_focus_and_kill_switch_stop(self):
        for options, reason in (
            ({"focus_checker": lambda _: False}, "focus"),
            ({"kill_switch": lambda: True}, "kill_switch"),
        ):
            controller, device = self.make(**options)
            controller.step(world())
            self.assert_stopped(controller, device, reason)

    def test_planner_fault_stops_before_dispatch(self):
        class FaultProducer:
            last_report = None

            def step(self, value):
                self.last_report = SimpleNamespace(fault="planner_fault")
                return ()

        controller, device = self.make(producers={"prayer": FaultProducer()})
        controller.step(world())
        self.assert_stopped(controller, device, "producer_fault:prayer")

    def test_unknown_telos_sequence_never_becomes_safe_by_default(self):
        record = row()
        record["npcs"]["entities"] = [{"instance_id": 4, "config_id": 22891, "name": "Telos"}]
        record["npc_action_events"] = [
            {
                "pid": 1,
                "process_creation_time": 2,
                "scene_epoch": 3,
                "instance_id": 4,
                "action_sequence": 999,
            }
        ]
        controller, device = self.make()
        controller.step(WorldState.from_telemetry(record))
        self.assert_stopped(controller, device, "unknown_telos_sequence")
        controller, device = self.make(attack_validator=lambda *_: True)
        controller.step(WorldState.from_telemetry(record))
        self.assertFalse(controller.stopped)

    def test_arbiter_confirmation_timeout_stops_and_releases(self):
        device = HeldRecordingDevice()

        class FakeArbiter:
            actuator = device

            def step(self, value, proposals):
                return SimpleNamespace(fault="confirmation_timeout")

            def close(self):
                self.actuator.close()

        controller = Supervisor(actuator=device, arbiter=FakeArbiter())
        self.addCleanup(controller.close)
        controller.step(world())
        self.assert_stopped(controller, device, "confirmation_timeout")

    def test_clock_regression_stops(self):
        controller, device = self.make()
        controller.step(world())
        controller.step(world(sequence=2, time_ms=900))
        self.assert_stopped(controller, device, "regressed")

    def test_wall_time_and_verified_kill_caps_are_normal_stops(self):
        clock = [0]
        controller, device = self.make(wall_clock=lambda: clock[0], maximum_seconds=10)
        clock[0] = 10
        controller.step(world())
        self.assert_stopped(controller, device, "wall_time_cap")
        self.assertEqual(controller.watchdog_trips, [])
        controller, device = self.make(maximum_kills=1)
        record = row()
        record["encounter_result"] = {
            "value": {"kind": "kill", "id": "synthetic-kill", "validated": True},
            "age_ms": 0,
            "source": "synthetic_confirmed_result",
        }
        controller.step(WorldState.from_telemetry(record))
        self.assert_stopped(controller, device, "kill_count_cap")
        self.assertEqual(controller.report()["verified_kills"], 1)
        self.assertEqual(controller.report()["kill_reports"][0]["verified"], True)

    def test_npc_zero_health_and_unvalidated_result_do_not_count_kills(self):
        record = row()
        record["npcs"]["entities"] = [{"instance_id": 4, "name": "Telos", "health": {"current": 0}}]
        record["encounter_result"] = {
            "value": {"kind": "kill", "id": "unmapped", "validated": False},
            "age_ms": 0,
            "source": "guide_lead",
        }
        controller, device = self.make()
        controller.step(WorldState.from_telemetry(record))
        self.assertEqual(controller.kills, 0)

    def test_verified_death_and_missing_postconditions_are_not_success(self):
        controller, device = self.make()
        record = row()
        record["encounter_result"] = {
            "value": {"kind": "death", "id": "synthetic-death", "validated": True},
            "age_ms": 0,
            "source": "synthetic_confirmed_result",
        }
        controller.step(WorldState.from_telemetry(record))
        self.assert_stopped(controller, device, "player_death")
        self.assertEqual(controller.deaths, 1)

    def test_assist_full_and_nonrecording_shadow_backends_are_blocked(self):
        with self.assertRaises(ValueError):
            load_producers(("unmapped_producer",))
        for mode in ("assist", "full"):
            with self.assertRaisesRegex(ValueError, "track L"):
                Supervisor(mode=mode)
        with self.assertRaisesRegex(ValueError, "recording"):
            Supervisor(actuator=object())

    def test_hotkey_is_configurable_without_registering_os_input(self):
        self.assertEqual(hotkey_codes("Ctrl+Alt+Pause"), (0x4003, 0x13))
        self.assertEqual(hotkey_codes("Ctrl+Shift+F12"), (0x4006, 0x7B))
        for label in ("F1", "Ctrl+Ctrl+Pause", "Ctrl+Anything"):
            with self.assertRaises(ValueError):
                hotkey_codes(label)

    def test_replay_capture_and_cli_never_import_native_input(self):
        controller, device = self.make()
        output = io.StringIO()
        replay(io.StringIO(json.dumps(row()) + "\n"), output, controller)
        self.assertFalse(json.loads(output.getvalue())["input_sent"])
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "synthetic.jsonl"
            capture.write_text(json.dumps(row()) + "\n")
            script = "import sys; from rs3_reader.bot.supervisor import main; r=main(); assert 'rs3_reader.native' not in sys.modules; raise SystemExit(r)"
            result = subprocess.run(
                [sys.executable, "-c", script, "--capture", str(capture), "--enable", ""],
                env=dict(os.environ, PYTHONPATH=str(ROOT / "python")),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1])["kind"], "session_report")

    def test_shadow_composition_shares_recording_backend_and_keeps_dispatch_disabled(self):
        class FakeArbiter:
            def __init__(self, actuator, *, clock, producers, enabled):
                self.actuator, self.clock = actuator, clock
                self.enabled, self.producers, self.last_log = enabled, producers, None

            def step(self, value, proposals):
                self.clock_seen = self.clock()
                self.last_log = SimpleNamespace(to_record=lambda: {"decisions": []})
                return SimpleNamespace(fault=None)

            def close(self):
                self.actuator.close()

        calls = []
        producer = SimpleNamespace(step=lambda value: calls.append(value) or ())
        fake_module = ModuleType("rs3_reader.bot.arbiter")
        fake_module.Arbiter = FakeArbiter
        with patch.dict(sys.modules, {"rs3_reader.bot.arbiter": fake_module}):
            controller = create_shadow_supervisor(arbitrate=True, producers={"fixture": producer})
        self.addCleanup(controller.close)
        self.assertIs(controller.actuator, controller.arbiter.actuator)
        self.assertIs(controller.arbiter.enabled, False)
        controller.native_clock = lambda: 1001 * MS
        controller.step(world())
        self.assertEqual(len(calls), 1)
        self.assertEqual(controller.arbiter.clock_seen, 1001 * MS)
        self.assertEqual(controller.last_log["arbitration"], {"decisions": []})
        self.assertIs(controller.last_log["dispatch_enabled"], False)
        self.assertIs(controller.report()["shadow_arbitration"], True)
        self.assertFalse(controller.actuator.attempts)

    def test_missing_optional_D6_is_an_explicit_error(self):
        with patch.dict(sys.modules, {"rs3_reader.bot.arbiter": None}):
            with self.assertRaisesRegex(RuntimeError, "D6"):
                create_shadow_supervisor(arbitrate=True)
        with self.assertRaises(ValueError):
            create_shadow_supervisor(arbitrate="yes")
        with self.assertRaises(ValueError):
            create_shadow_supervisor(arbitrate=True, actuator=RecordingActuator())

    def test_encounter_runs_before_consumers_and_forwards_same_frame_state(self):
        observer = SimpleNamespace(state=None)
        calls = []

        def observe(value):
            observer.state = value.sample_sequence
            return ()

        observer.step = observe
        prayer = SimpleNamespace(encounter_provider=None)
        prayer.step = lambda value: calls.append(("prayer", prayer.encounter_provider())) or ()
        survival = SimpleNamespace(
            step=lambda value, encounter=None: calls.append(("survival", encounter)) or (),
            can_send=lambda intent, value: True,
        )
        controller = create_shadow_supervisor(
            producers={
                "survival": survival,
                "prayer": prayer,
                "encounter": observer,
            }
        )
        self.addCleanup(controller.close)
        controller.step(world(sequence=7))
        self.assertEqual(calls, [("survival", 7), ("prayer", 7)])
        self.assertIs(controller.producers["survival"].can_send(None, None), True)

    def test_arbitrated_CLI_is_recording_only_or_reports_missing_D6(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "synthetic.jsonl"
            capture.write_text(json.dumps(row()) + "\n")
            script = (
                "import sys; from rs3_reader.bot.supervisor import main; r=main(); "
                "assert 'rs3_reader.native' not in sys.modules; "
                "assert 'rs3_reader.bot.actuator_sendinput' not in sys.modules; "
                "assert 'rs3_reader.bot.actuator_serial' not in sys.modules; raise SystemExit(r)"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    "--capture",
                    str(capture),
                    "--enable",
                    "",
                    "--arbitrate",
                ],
                env=dict(os.environ, PYTHONPATH=str(ROOT / "python")),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if (ROOT / "python/rs3_reader/bot/arbiter.py").exists():
                self.assertEqual(result.returncode, 0, result.stderr)
                records = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertIs(records[0]["dispatch_enabled"], False)
                self.assertIs(records[-1]["shadow_arbitration"], True)
                self.assertIs(records[-1]["input_sent"], False)
            else:
                self.assertEqual(result.returncode, 2)
                self.assertIn("D6", result.stderr)

    def test_broken_producer_or_kill_switch_releases_keys(self):
        class BrokenProducer:
            def step(self, value):
                raise RuntimeError("synthetic failure")

        controller, device = self.make(producers={"broken": BrokenProducer()})
        controller.step(world())
        self.assert_stopped(controller, device, "supervisor_exception")

    def test_real_capture_watchdogs_and_no_intent_replay_have_a_golden_log(self):
        path = ROOT / "work/prayer_research/telos-session-native-hits-2026-11-10.jsonl"
        if not path.exists():
            self.skipTest("Raw captures intentionally stay local")
        controller, device = self.make()
        output = io.StringIO()
        with path.open() as stream:
            replay(stream, output, controller)
        self.assertFalse(device.attempts)
        self.assertTrue(controller.stopped)  # Legacy capture lacks some queue counters.
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "f2c9a0cfceb6535913e930fe99d9314ea5a734a5355845087eabfaceb046e54b",
        )


if __name__ == "__main__":
    unittest.main()
