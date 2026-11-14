"""D1 replay and gap tests never load a DLL or send input."""

import hashlib
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path

from rs3_reader.bot.encounter_tracker import (
    HISTORICAL_CLIENT,
    AttackTable,
    EncounterObserver,
    EncounterTracker,
)
from rs3_reader.bot.replay import replay
from rs3_reader.bot.world_state import Observation, WorldState, to_jsonable

ROOT = Path(__file__).resolve().parents[2]
MS = 1_000_000


def row(sequence=1, time_ms=1000, *, events=None, phase=1, enrage=300, epoch=3):
    return {
        "schema_version": 1,
        "status": "available",
        "source": "synthetic_fixture",
        "pid": 1,
        "process_creation_time": 2,
        "executable_sha256": HISTORICAL_CLIENT,
        "monotonic_ms": time_ms,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": epoch},
        "player": {"tile": {"x": 3200, "y": 3200, "plane": 0}},
        "player_hit_splats": {"status": "available", "player_index": 17, "instance_id": 9},
        "telos": {"phase": phase, "enrage": enrage},
        "npcs": {
            "status": "available",
            "entities": [
                {
                    "instance_id": 4,
                    "config_id": 22891,
                    "name": "Telos, the Warden",
                    "tile": {"x": 3201, "y": 3201, "plane": 0},
                    "target": {
                        "kind": "player",
                        "runtime_id": 17,
                        "player_index": 17,
                        "instance_id": 9,
                    },
                }
            ],
        },
        "npc_action_events": events or [],
        "npc_action_events_lost": 0,
    }


def cue(sequence=1, action=28929, *, time_ms=1000, epoch=3, instance=4, sample_sequence=1):
    return {
        "sequence": sequence,
        "action_sequence": action,
        "observed_ns": time_ms * MS,
        "sample_sequence": sample_sequence,
        "pid": 1,
        "process_creation_time": 2,
        "scene_epoch": epoch,
        "instance_id": instance,
    }


def world(*args, **kwargs):
    return WorldState.from_telemetry(row(*args, **kwargs))


def measured_table():
    return {
        "schema_version": 1,
        "attacks": [
            {
                "phase": 1,
                "enrage_range": [300, 300],
                "sequence": 28929,
                "client_sha256": HISTORICAL_CLIENT,
                "name": "synthetic measured auto",
                "sample_count": 2,
                "evidence_kind": "user_labelled_native_capture",
                "classification_validated": True,
                "timing_validated": True,
                "validated": True,
                "timing_evidence_kind": "controlled_protection_trial",
                "required_protection": "melee",
                "cue_to_prayer_check_ms": [400, 500],
                "hit_components": [{"kind": "synthetic hit"}],
                "counts_as_auto": True,
            }
        ],
    }


class EncounterTrackerTests(unittest.TestCase):
    def tracker(self):
        return EncounterTracker(AttackTable(), context="isolated_p1_melee")

    def test_historical_label_is_an_unresolved_threat_not_a_prayer_deadline(self):
        state = self.tracker().step(world(events=[cue()]))
        self.assertEqual(state.phase, 1)
        self.assertEqual(state.observed_auto_count, 1)
        self.assertIsNone(state.attack_index)
        self.assertIsNone(state.next_attack)
        self.assertIsNone(state.coverage_until_ns)
        self.assertEqual(state.pending_threats[0].name, "melee auto")
        self.assertIsNone(state.pending_threats[0].prayer_record())

    def test_scope_mismatch_and_unlabelled_sequences_never_fabricate_threats(self):
        self.assertEqual(
            EncounterTracker(AttackTable()).step(world(events=[cue()])).pending_threats, ()
        )
        for options in ({"phase": None}, {"phase": 2}, {"enrage": None}, {"enrage": 500}):
            state = self.tracker().step(world(events=[cue()], **options))
            self.assertEqual(state.pending_threats, ())
        state = self.tracker().step(world(events=[cue(action=28932)]))
        self.assertEqual(state.pending_threats, ())
        self.assertIn("sequence_unlabelled_or_context_unknown", state.reasons)

    def test_measured_window_and_expiry_use_native_monotonic_time(self):
        tracker = EncounterTracker(AttackTable(measured_table()))
        state = tracker.step(world(events=[cue()]))
        threat = state.pending_threats[0]
        self.assertEqual(
            threat.prayer_record(),
            {
                "event_id": 1,
                "source_instance": 4,
                "check_earliest_ns": 1400 * MS,
                "check_latest_ns": 1500 * MS,
                "prayer": 2,
                "flags": 1,
            },
        )
        self.assertIsNone(state.coverage_until_ns)  # One measured hit is not complete coverage.
        self.assertEqual(tracker.step(world(2, 1501)).pending_threats, ())

    def test_calibrated_cue_needs_exact_current_local_player_target(self):
        for target in (
            None,
            {"kind": "none"},
            {"kind": "npc", "instance_id": 9, "runtime_id": 17},
            {"kind": "player", "instance_id": 10, "runtime_id": 17, "player_index": 17},
            {"kind": "player", "instance_id": 9, "runtime_id": 18, "player_index": 17},
            {"kind": "player", "instance_id": 9, "runtime_id": 17, "player_index": 18},
        ):
            record = row(events=[cue()])
            record["npcs"]["entities"][0]["target"] = target
            state = EncounterTracker(AttackTable(measured_table())).step(
                WorldState.from_telemetry(record)
            )
            with self.subTest(target=target):
                self.assertEqual(len(state.pending_threats), 1)
                self.assertTrue(state.pending_threats[0].classification_validated)
                self.assertIsNone(state.pending_threats[0].prayer_record())
                self.assertEqual(state.pending_threats[0].reason, "attacker_target_unverified")
                self.assertIn("attacker_target_unverified", state.reasons)
                self.assertIsNone(state.coverage_until_ns)

    def test_stale_unmapped_target_channel_and_wrong_plane_cannot_validate_timing(self):
        record = row(events=[cue()])
        for patch in (
            {"status": "unavailable"},
            {"instance_id": 0},
            {"player_index": None},
        ):
            changed = dict(record, player_hit_splats=dict(record["player_hit_splats"], **patch))
            state = EncounterTracker(AttackTable(measured_table())).step(
                WorldState.from_telemetry(changed)
            )
            self.assertIsNone(state.pending_threats[0].prayer_record())
        value = WorldState.from_telemetry(record)
        actor = value.npcs.value[0]
        for changed_actor in (
            replace(actor, target=Observation(actor.target.value, 151, "fixture")),
            replace(actor, target=Observation(actor.target.value, 0, "fixture", reason="unmapped")),
            replace(actor, tile=Observation()),
        ):
            state = EncounterTracker(AttackTable(measured_table())).step(
                replace(value, npcs=Observation((changed_actor,), 0, "fixture"))
            )
            self.assertIsNone(state.pending_threats[0].prayer_record())
        record["npcs"]["entities"][0]["tile"]["plane"] = 1
        state = EncounterTracker(AttackTable(measured_table())).step(
            WorldState.from_telemetry(record)
        )
        self.assertIsNone(state.pending_threats[0].prayer_record())

    def test_a_different_or_unknown_event_frame_cannot_borrow_current_target_evidence(self):
        for sample_sequence in (None, 0, 2, 3):
            state = EncounterTracker(AttackTable(measured_table())).step(
                world(events=[cue(sample_sequence=sample_sequence)])
            )
            self.assertEqual(len(state.pending_threats), 1)
            self.assertIsNone(state.pending_threats[0].prayer_record())
            self.assertEqual(state.pending_threats[0].reason, "attacker_target_unverified")
        state = EncounterTracker(AttackTable(measured_table())).step(
            world(2, 1010, events=[cue(sample_sequence=1)])
        )
        self.assertIsNone(state.pending_threats[0].prayer_record())

    def test_confirmed_in_flight_threat_survives_a_later_target_clear(self):
        tracker = EncounterTracker(AttackTable(measured_table()))
        first = tracker.step(world(events=[cue()])).pending_threats[0]
        record = row(2, 1100, phase=2)
        record["npcs"]["entities"][0]["target"] = {"kind": "none"}
        state = tracker.step(WorldState.from_telemetry(record))
        self.assertEqual(state.pending_threats, (first,))
        self.assertIsNotNone(state.pending_threats[0].prayer_record())

    def test_target_reference_alone_cannot_validate_an_unmeasured_attack(self):
        threat = self.tracker().step(world(events=[cue()])).pending_threats[0]
        self.assertEqual(threat.reason, "protection_check_unmeasured")
        self.assertIsNone(threat.prayer_record())

    def test_guide_or_multihit_window_is_not_promoted(self):
        document = measured_table()
        document["attacks"][0]["hit_components"] *= 2
        self.assertIsNone(
            EncounterTracker(AttackTable(document))
            .step(world(events=[cue()]))
            .pending_threats[0]
            .prayer_record()
        )
        document["attacks"][0]["evidence_kind"] = "guide_lead"
        with self.assertRaises(ValueError):
            AttackTable(document)

    def test_duplicate_event_and_repeated_animation_application_are_distinct(self):
        tracker = self.tracker()
        self.assertEqual(len(tracker.step(world(events=[cue()])).pending_threats), 1)
        self.assertEqual(len(tracker.step(world(2, 1010, events=[cue()])).pending_threats), 1)
        self.assertEqual(
            len(tracker.step(world(3, 1020, events=[cue(2, time_ms=1020)])).pending_threats), 2
        )

    def test_loss_unknown_loss_and_sequence_gap_clear_accumulated_state(self):
        for loss in (None, 2):
            tracker = self.tracker()
            tracker.step(world(events=[cue()]))
            changed = row(2, 1010)
            changed["npc_action_events_lost"] = loss
            self.assertEqual(tracker.step(WorldState.from_telemetry(changed)).pending_threats, ())
        tracker = self.tracker()
        tracker.step(world(events=[cue()]))
        state = tracker.step(world(2, 1010, events=[cue(3, time_ms=1010)]))
        self.assertEqual(len(state.pending_threats), 1)
        self.assertIn("event_sequence_gap", state.reasons)

    def test_other_npc_events_advance_the_global_queue_cursor(self):
        tracker = self.tracker()
        state = tracker.step(world(events=[cue(), cue(2, instance=99), cue(3)]))
        self.assertEqual(len(state.pending_threats), 2)
        self.assertNotIn("event_sequence_gap", state.reasons)

    def test_reused_sequence_with_a_different_payload_invalidates_state(self):
        tracker = self.tracker()
        tracker.step(world(events=[cue()]))
        state = tracker.step(world(2, 1010, events=[cue(time_ms=1010)]))
        self.assertEqual(state.pending_threats, ())
        self.assertIn("event_sequence_regressed_or_reused", state.reasons)

    def test_scene_and_build_changes_clear_state_and_do_not_reuse_ids(self):
        tracker = self.tracker()
        first = tracker.step(world(events=[cue()])).pending_threats[0]
        state = tracker.step(world(2, 1010, events=[cue(epoch=4, time_ms=1010)], epoch=4))
        self.assertGreater(state.pending_threats[0].event_id, first.event_id)
        changed = row(3, 1020, epoch=4)
        changed["executable_sha256"] = "a" * 64
        self.assertEqual(tracker.step(WorldState.from_telemetry(changed)).pending_threats, ())

    def test_stale_foreign_and_future_events_cannot_create_threats(self):
        for event in (cue(time_ms=3000), cue(time_ms=1), cue(epoch=8)):
            self.assertEqual(
                self.tracker().step(world(time_ms=2000, events=[event])).pending_threats, ()
            )
        tracker = self.tracker()
        tracker.step(world(events=[cue()]))
        stale = WorldState.from_telemetry(row(2, 1010), now_ns=1300 * MS)
        self.assertEqual(tracker.step(stale).pending_threats, ())

    def test_regression_ambiguity_and_overflow_do_not_keep_old_threats(self):
        tracker = self.tracker()
        tracker.step(world(events=[cue()]))
        self.assertEqual(tracker.step(world(1, 900)).pending_threats, ())
        ambiguous = row(events=[cue()])
        ambiguous["npcs"]["entities"].append(
            {"instance_id": 5, "config_id": 22891, "name": "Telos"}
        )
        self.assertEqual(
            self.tracker().step(WorldState.from_telemetry(ambiguous)).pending_threats, ()
        )
        bounded = EncounterTracker(AttackTable(), context="isolated_p1_melee", maximum_pending=1)
        self.assertEqual(bounded.step(world(events=[cue(), cue(2)])).pending_threats, ())

    def test_cycle_position_needs_an_explicit_measured_anchor(self):
        document = measured_table()
        document["attacks"][0].update(
            sequence=28931, name="synthetic special anchor", counts_as_auto=False
        )
        document["attacks"][0]["cycle"] = {
            "validated": True,
            "evidence_kind": "user_labelled_native_capture",
            "index_after": 2,
            "next_sequence": 123,
        }
        state = EncounterTracker(AttackTable(document)).step(world(events=[cue(action=28931)]))
        self.assertEqual((state.attack_index, state.next_attack), (2, 123))

    def test_phase_change_preserves_pending_impacts_and_discards_cycle_prediction(self):
        tracker = EncounterTracker(AttackTable(measured_table()))
        tracker.step(world(events=[cue()]))
        self.assertEqual(len(tracker.step(world(2, 1100, phase=2)).pending_threats), 1)

    def test_replay_adapter_emits_no_intents_and_is_deterministic(self):
        text = json.dumps(row(events=[cue()])) + "\n"
        logs = []
        for _ in range(2):
            output = io.StringIO()
            observer = EncounterObserver(self.tracker())
            replay(io.StringIO(text), output, observer)
            self.assertEqual(len(observer.state.pending_threats), 1)
            self.assertEqual(json.loads(output.getvalue())["intents"], [])
            logs.append(output.getvalue())
        self.assertEqual(logs[0], logs[1])

    def test_two_local_labelled_captures_have_one_unresolved_threat_per_labelled_cue(self):
        captures = (
            (
                "telos-session-2026-11-10.jsonl",
                "f998a1f4903e8e7eef08471bbd8f3e81e9769b44cfda626708dac8b94314b66c",
                15,
                "acbb16174fc066f0e6c1442546a9b71b3e7366260484dc55f7761775d52a9806",
            ),
            (
                "telos-session-native-hits-2026-11-10.jsonl",
                "37c9c4c99481db3f73bcb355174523cb7b2aba02a7815fd1f9e243004410af23",
                16,
                "f2c9a0cfceb6535913e930fe99d9314ea5a734a5355845087eabfaceb046e54b",
            ),
        )
        for filename, digest, expected, golden_digest in captures:
            path = ROOT / "work/prayer_research" / filename
            if not path.exists():
                self.skipTest("Raw local captures are intentionally not committed")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            tracker = self.tracker()

            class AnnotatedObserver:
                def __init__(self):
                    self.ids = set()

                def step(self, original):
                    # The report labels these two captures 300% P1. Add that
                    # annotation only in this offline test, never the runtime.
                    end_ns = original.observed_ns + original.raw["sample_duration_ns"]
                    value = WorldState.from_telemetry(to_jsonable(original.raw), now_ns=end_ns)
                    value = replace(
                        value,
                        telos=replace(
                            value.telos,
                            phase=Observation(1, 0, "user_labelled_capture"),
                            enrage=Observation(300, 0, "user_labelled_capture"),
                        ),
                    )
                    state = tracker.step(value)
                    for threat in state.pending_threats:
                        self.ids.add(threat.event_id)
                        if threat.flags:
                            raise AssertionError("Unmeasured timing became validated")
                    return ()

            observer, output = AnnotatedObserver(), io.StringIO()
            with path.open(encoding="utf-8") as stream:
                replay(stream, output, observer)
            self.assertEqual(len(observer.ids), expected, filename)
            self.assertEqual(hashlib.sha256(output.getvalue().encode()).hexdigest(), golden_digest)


if __name__ == "__main__":
    unittest.main()
