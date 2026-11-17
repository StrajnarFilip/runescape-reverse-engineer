"""D2 uses synthetic measured windows; real current metadata never authorizes input."""

import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from rs3_reader.bot.prayer_executor import (
    NativePrayerPlanner,
    PrayerExecutor,
    PythonPrayerPlanner,
    derive_validation_bands,
)
from rs3_reader.bot.replay import replay
from rs3_reader.bot.world_state import Observation, WorldState, to_jsonable

ROOT = Path(__file__).resolve().parents[2]
MS = 1_000_000
CLIENT = "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45"


def settings():
    result = json.loads((ROOT / "profiles/prayer_flick.json").read_text())
    result["timing"]["lead_ms"] = 100
    return result


def catalogue(coverage=True):
    return {
        "schema_version": 1,
        "automatic_flick_ready": coverage,
        "attacks": [
            {
                "phase": 1,
                "enrage_range": [300, 300],
                "client_sha256": CLIENT,
                "sequence": 28929,
                "name": "synthetic auto",
                "validated": True,
                "classification_validated": True,
                "timing_validated": True,
                "sample_count": 2,
                "evidence_kind": "user_labelled_native_capture",
                "timing_evidence_kind": "controlled_protection_trial",
                "required_protection": "melee",
                "cue_to_prayer_check_ms": [400, 500],
            }
        ],
        "coverage": [
            {"phase": 1, "enrage_range": [300, 300], "client_sha256": CLIENT, "complete": coverage}
        ],
    }


def world(time_ms=1350, sequence=1, active="soul_split", phase=1, enrage=300):
    return WorldState.from_telemetry(
        {
            "schema_version": 1,
            "source": "synthetic_fixture",
            "status": "available",
            "pid": 1,
            "process_creation_time": 2,
            "executable_sha256": CLIENT,
            "monotonic_ms": time_ms,
            "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
            "combat": {"scene_epoch": 3, "active_prayer": active},
            "prayer": {"current": 990, "max": 990},
            "telos": {"phase": phase, "enrage": enrage},
        }
    )


def encounter(value, threats=True, coverage=True):
    return SimpleNamespace(
        session=value.session,
        observed_ns=value.observed_ns,
        coverage_until_ns=10000 * MS if coverage else None,
        pending_threats=(
            [
                {
                    "event_id": 1,
                    "source_instance": 4,
                    "sequence": 28929,
                    "observed_ns": 1000 * MS,
                    "classification_validated": True,
                    "check_earliest_ns": 1400 * MS,
                    "check_latest_ns": 1500 * MS,
                    "prayer": 2,
                    "flags": 1,
                }
            ]
            if threats
            else []
        ),
    )


class FakePlanner:
    def __init__(self, action=2, prayer=2):
        self.calls, self.sent, self.resets = [], [], 0
        self.action, self.prayer = action, prayer

    def step(self, frame, threats):
        self.calls.append((frame, threats))
        return {
            "action": self.action,
            "prayer": self.prayer,
            "command_id": 1,
            "threat_id": 1,
            "check_earliest_ns": 1400 * MS if self.prayer == 2 else 0,
            "next_wakeup_ns": 9000 * MS,
            "flags": 0,
            "reason": "synthetic_decision",
        }

    def note_sent(self, command, now):
        self.sent.append((command, now))

    def reset(self):
        self.resets += 1


class PrayerExecutorTests(unittest.TestCase):
    def test_changed_live_prayer_bar_does_not_use_an_old_profile_key(self):
        value = world()
        from dataclasses import replace

        binding = {
            "kind": "prayer",
            "ability_struct_id": 14596,
            "key": "Ctrl+F4",
            "reason": None,
            "binding_reason": None,
            "source": "synthetic_P5",
        }
        value = replace(
            value, action_bar=Observation((binding,), 0, "synthetic_P5", complete=False)
        )
        executor = self.executor()
        self.assertTrue(executor.step(value, encounter(value)))
        binding["key"] = "Ctrl+F1"
        changed = replace(
            value, action_bar=Observation((binding,), 0, "synthetic_P5", complete=False)
        )
        self.assertEqual(executor.step(changed, encounter(changed)), ())

    def test_real_unvalidated_capture_has_a_golden_no_toggle_log(self):
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
            replay(stream, output, PrayerExecutor())
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "f2c9a0cfceb6535913e930fe99d9314ea5a734a5355845087eabfaceb046e54b",
        )

    def executor(self, *, coverage=True, planner=None):
        return PrayerExecutor(settings(), catalogue(coverage), planner=planner or FakePlanner())

    def test_unvalidated_phase_band_never_calls_the_planner(self):
        planner = FakePlanner()
        data = catalogue()
        data["attacks"][0]["timing_validated"] = False
        executor = PrayerExecutor(settings(), data, planner=planner)
        value = world()
        self.assertEqual(executor.step(value, encounter(value)), ())
        self.assertFalse(planner.calls)
        self.assertEqual(executor.last_report.reason, "timing_band_unvalidated")

    def test_guide_or_zero_samples_do_not_validate_a_band(self):
        for patch in (
            {"evidence_kind": "guide_lead"},
            {"sample_count": 0},
            {"cue_to_prayer_check_ms": None},
        ):
            data = catalogue()
            data["attacks"][0].update(patch)
            self.assertFalse(derive_validation_bands(data)[0]["prayer_check_deadline_validated"])

    def test_validated_cue_becomes_a_prayer_intent_with_exact_binding(self):
        value = world()
        executor = self.executor()
        (proposal,) = executor.step(value, encounter(value))
        self.assertEqual(proposal.action.prayer, "melee")
        self.assertLess(proposal.deadline_ns, 1400 * MS)
        self.assertEqual(
            next(
                e.observation.value["binding"]["virtual_key"]
                for e in proposal.evidence
                if e.field == "prayer.planner"
            ),
            115,
        )
        self.assertEqual(executor.planner.calls[0][0]["prayer_points"], 9900)

    def test_partial_coverage_allows_only_measured_protection_not_soul_split(self):
        value = world()
        executor = self.executor(coverage=False)
        self.assertTrue(executor.step(value, encounter(value)))
        self.assertFalse(executor.planner.calls[-1][0]["flags"] & 8)
        executor = self.executor(coverage=False, planner=FakePlanner(prayer=5))
        self.assertEqual(executor.step(value, encounter(value)), ())

    def test_changed_scope_and_unresolved_or_tampered_threat_are_blocked(self):
        for values in ({"phase": 2}, {"enrage": 301}):
            value = world(**values)
            self.assertEqual(self.executor().step(value, encounter(value)), ())
        value = world()
        for patch in ({"flags": 0}, {"check_latest_ns": 1501 * MS}, {"prayer": 3}):
            incoming = encounter(value)
            incoming.pending_threats[0].update(patch)
            self.assertEqual(self.executor().step(value, incoming), ())

    def test_only_accepted_input_calls_note_sent_and_can_send_rechecks(self):
        value = world()
        executor = self.executor()
        proposal = executor.step(value, encounter(value))[0]
        executor.note_rejected(proposal.intent_id, "focus", value.now_ns)
        self.assertFalse(executor.planner.sent)
        self.assertTrue(executor.can_send(proposal, value))
        executor.note_sent(proposal.intent_id, value.now_ns)
        self.assertEqual(executor.planner.sent, [(1, value.now_ns)])
        self.assertFalse(executor.can_send(proposal, value))

    def test_planner_fault_latches_until_explicit_reset(self):
        value = world()
        planner = FakePlanner(action=4)
        executor = self.executor(planner=planner)
        self.assertEqual(executor.step(value, encounter(value)), ())
        self.assertIsNotNone(executor.last_report.fault)
        planner.action = 2
        self.assertEqual(executor.step(value, encounter(value)), ())
        executor.reset()
        self.assertTrue(executor.step(value, encounter(value)))

    def test_changed_scene_does_not_silently_reset_a_planner(self):
        value = world()
        executor = self.executor()
        executor.step(value, encounter(value))
        record = dict(value.raw)
        record["combat"] = {"scene_epoch": 4, "active_prayer": "soul_split"}
        changed = WorldState.from_telemetry(record)
        self.assertEqual(executor.step(changed, encounter(changed)), ())
        self.assertEqual(executor.last_report.fault, "session_or_build_changed")

    def test_default_profile_stays_observe_and_preserves_all_four_chords(self):
        profile = settings()
        self.assertFalse(profile["prayer_check_deadline_validated"])
        self.assertFalse(profile["attack_coverage_validated"])
        self.assertEqual(
            [
                profile["bindings"][name]["virtual_key"]
                for name in ("magic", "ranged", "soul_split", "melee")
            ],
            [112, 113, 114, 115],
        )
        self.assertTrue(all(item["modifiers"] == 1 for item in profile["bindings"].values()))
        output = io.StringIO()
        replay(io.StringIO(json.dumps(to_jsonable(world().raw)) + "\n"), output, PrayerExecutor())
        self.assertEqual(json.loads(output.getvalue())["intents"], [])

    def test_actual_c_planner_protects_then_returns_to_soul_split_with_ack(self):
        library = ROOT.parent / "can/build/bin/rs3_native.dll"
        if not library.exists():
            library = ROOT / "build/bin/rs3_native.dll"
        if not library.exists():
            self.skipTest("Build the native DLL for this adapter check")
        planner = NativePrayerPlanner(settings(), str(library))
        self.addCleanup(planner.close)
        self.check_protection_then_soul_split(planner)

    def test_python_planner_protects_then_returns_to_soul_split_with_ack(self):
        self.check_protection_then_soul_split(PythonPrayerPlanner(settings()))

    def check_protection_then_soul_split(self, planner):
        executor = self.executor(planner=planner)
        first = world()
        proposal = executor.step(first, encounter(first))[0]
        self.assertEqual(proposal.action.prayer, "melee")
        executor.note_sent(proposal.intent_id, first.now_ns)
        second = world(1360, 2)
        self.assertEqual(executor.step(second, encounter(second)), ())
        self.assertTrue(executor.last_report.waiting_ack)
        third = world(1380, 3, active="melee")
        self.assertEqual(executor.step(third, encounter(third)), ())
        inside_hold = world(1600, 4, active="melee")
        self.assertEqual(executor.step(inside_hold, encounter(inside_hold)), ())
        fourth = world(1601, 5, active="melee")
        self.assertEqual(
            executor.step(fourth, encounter(fourth, threats=False))[0].action.prayer, "soul_split"
        )


if __name__ == "__main__":
    unittest.main()
