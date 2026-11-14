"""Experimental Telos prayer trial, action-event filtering and manual key sampling."""

import contextlib
import copy
import io
import json
import unittest
from unittest.mock import Mock, patch

from rs3_reader import telos_prayer
from rs3_reader.native import NPCActionEvent
from rs3_reader.prayer import Prayer
from rs3_reader.telos_prayer import (
    MELEE_AUTO_SEQUENCE,
    VK_CONTROL,
    VK_F3,
    VK_F4,
    ManualPrayerKeys,
    PrayerTrial,
    telos_actions,
)

MILLISECOND_NS = 1_000_000
TARGET = (1, 2, 3)
KEY_DOWN = -32768
ARGUMENT_ERROR_EXIT_CODE = 2


def ms(value):
    return value * MILLISECOND_NS


class PrayerTrialTests(unittest.TestCase):
    def test_unvalidated_live_trial_never_opens_a_reader(self):
        for timing_validated, coverage_validated in ((False, False), (True, False), (False, True)):
            profile = {
                "prayer_check_deadline_validated": timing_validated,
                "attack_coverage_validated": coverage_validated,
            }
            profile_path = Mock(read_text=Mock(return_value=json.dumps(profile)))
            with (
                self.subTest(timing=timing_validated, coverage=coverage_validated),
                patch("sys.argv", ["telos_prayer", "--trial"]),
                patch.object(telos_prayer, "PRAYER_FLICK_PROFILE", profile_path),
                patch.object(telos_prayer, "Reader") as reader,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as error:
                    telos_prayer.main()
                self.assertEqual(error.exception.code, ARGUMENT_ERROR_EXIT_CODE)
                reader.assert_not_called()

    def test_trial_waits_for_acknowledgement_and_repeats_the_600_ms_cycle(self):
        send_prayer = Mock(side_effect=[ms(time) + 1 for time in (1000, 1600, 2000, 2600)])
        trial = PrayerTrial(send_prayer)
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(999), ms(1000), ms(1000))
        send_prayer.assert_called_once_with(Prayer.MELEE)
        self.assertEqual(trial.step(TARGET, Prayer.MELEE, ms(1000), ms(1010)), [])
        self.assertEqual(trial.pending_prayer, Prayer.MELEE)
        updates = trial.step(TARGET, Prayer.MELEE, ms(1019), ms(1020))
        self.assertEqual(updates[0]["action"], "acknowledged")
        self.assertEqual(trial.step(TARGET, Prayer.MELEE, ms(1598), ms(1599)), [])
        send_prayer.assert_called_once_with(Prayer.MELEE)
        trial.step(TARGET, Prayer.MELEE, ms(1599), ms(1600))
        self.assertEqual(send_prayer.call_args.args, (Prayer.SOUL_SPLIT,))
        self.assertEqual(trial.step(TARGET, Prayer.MELEE, ms(1619), ms(1620)), [])
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(1629), ms(1630))
        self.assertEqual(trial.completed_cycles, 1)
        self.assertEqual(trial.step(TARGET, Prayer.SOUL_SPLIT, ms(1700), ms(1700)), [])
        self.assertEqual(send_prayer.call_count, 2)
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(1999), ms(2000), ms(2000))
        trial.step(TARGET, Prayer.MELEE, ms(2019), ms(2020))
        self.assertEqual(trial.step(TARGET, Prayer.MELEE, ms(2598), ms(2599)), [])
        trial.step(TARGET, Prayer.MELEE, ms(2599), ms(2600))
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(2629), ms(2630))
        self.assertEqual(trial.completed_cycles, 2)
        self.assertEqual(
            [call.args[0] for call in send_prayer.call_args_list],
            [Prayer.MELEE, Prayer.SOUL_SPLIT] * 2,
        )

    def test_new_cue_can_share_the_soul_split_acknowledgement_sample(self):
        send_prayer = Mock(side_effect=[ms(1600) + 1, ms(1630) + 1])
        trial = PrayerTrial(send_prayer)
        trial.step(TARGET, Prayer.MELEE, ms(999), ms(1000), ms(1000))
        trial.step(TARGET, Prayer.MELEE, ms(1599), ms(1600))
        updates = trial.step(TARGET, Prayer.SOUL_SPLIT, ms(1629), ms(1630), ms(1630))
        self.assertEqual([update["action"] for update in updates], ["acknowledged", "sent"])
        self.assertEqual(trial.completed_cycles, 1)
        self.assertEqual(trial.melee_release_ns, ms(2230))
        self.assertEqual(trial.pending_prayer, Prayer.MELEE)

    def test_already_active_prayers_are_not_toggled(self):
        send_prayer = Mock()
        trial = PrayerTrial(send_prayer)
        trial.step(TARGET, Prayer.MELEE, ms(999), ms(1000), ms(1000))
        trial.step(TARGET, Prayer.MELEE, ms(1299), ms(1300), ms(1300))
        self.assertEqual(trial.step(TARGET, Prayer.MELEE, ms(1599), ms(1600)), [])
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(1899), ms(1900))
        send_prayer.assert_not_called()
        self.assertEqual(trial.completed_cycles, 1)
        self.assertIsNone(trial.target)

    def test_late_melee_confirmation_can_still_return_to_soul_split(self):
        send_prayer = Mock(side_effect=[ms(1000) + 1, ms(1790) + 1])
        trial = PrayerTrial(send_prayer)
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(999), ms(1000), ms(1000))
        self.assertEqual(trial.step(TARGET, Prayer.UNKNOWN, ms(1599), ms(1600)), [])
        send_prayer.assert_called_once_with(Prayer.MELEE)
        updates = trial.step(TARGET, Prayer.MELEE, ms(1789), ms(1790))
        self.assertEqual([update["action"] for update in updates], ["acknowledged", "sent"])
        self.assertEqual(send_prayer.call_args.args, (Prayer.SOUL_SPLIT,))

    def test_cue_waits_briefly_for_a_pending_soul_split(self):
        for acknowledgement_ms, cue_still_fresh in [(1680, True), (1750, False)]:
            with self.subTest(acknowledgement_ms=acknowledgement_ms):
                send_prayer = Mock(side_effect=[ms(1600) + 1, ms(acknowledgement_ms) + 1])
                trial = PrayerTrial(send_prayer)
                trial.step(TARGET, Prayer.MELEE, ms(999), ms(1000), ms(1000))
                trial.step(TARGET, Prayer.MELEE, ms(1599), ms(1600))
                self.assertEqual(trial.step(TARGET, Prayer.MELEE, ms(1629), ms(1630), ms(1630)), [])
                send_prayer.assert_called_once_with(Prayer.SOUL_SPLIT)
                trial.step(
                    TARGET, Prayer.SOUL_SPLIT, ms(acknowledgement_ms - 1), ms(acknowledgement_ms)
                )
                self.assertEqual(trial.completed_cycles, 1)
                self.assertEqual(send_prayer.call_count, 2 if cue_still_fresh else 1)

    def test_trial_rearms_after_a_missing_acknowledgement_or_focus(self):
        scenarios = [(TARGET, 2210, "not observed"), (None, 1600, "foreground")]
        for next_target, now_ms, reason in scenarios:
            with self.subTest(reason=reason):
                send_prayer = Mock(side_effect=[ms(1000) + 1, ms(2300) + 1])
                trial = PrayerTrial(send_prayer)
                trial.step(TARGET, Prayer.SOUL_SPLIT, ms(999), ms(1000), ms(1000))
                updates = trial.step(next_target, Prayer.SOUL_SPLIT, ms(now_ms - 1), ms(now_ms))
                self.assertEqual(updates[0]["action"], "cycle_skipped")
                self.assertIn(reason, updates[0]["reason"])
                self.assertEqual(trial.step(TARGET, Prayer.SOUL_SPLIT, ms(2299), ms(2300)), [])
                send_prayer.assert_called_once_with(Prayer.MELEE)
                trial.step(TARGET, Prayer.SOUL_SPLIT, ms(2299), ms(2300), ms(2300))
                self.assertEqual(send_prayer.call_count, 2)

    def test_input_rejection_skips_the_cycle_and_cools_down_before_retrying(self):
        send_prayer = Mock(side_effect=[RuntimeError("input rejected"), ms(2300) + 1])
        trial = PrayerTrial(send_prayer)
        updates = trial.step(TARGET, Prayer.SOUL_SPLIT, ms(999), ms(1000), ms(1000))
        self.assertEqual(updates[0]["action"], "cycle_skipped")
        self.assertEqual(trial.step(TARGET, Prayer.SOUL_SPLIT, ms(2099), ms(2100), ms(2100)), [])
        send_prayer.assert_called_once_with(Prayer.MELEE)
        trial.step(TARGET, Prayer.SOUL_SPLIT, ms(2299), ms(2300), ms(2300))
        self.assertEqual(send_prayer.call_count, 2)


class TelosActionTests(unittest.TestCase):
    def test_same_id_events_require_the_matching_actor_and_sample(self):
        def event(sequence, instance_id=7, sample_sequence=14, scene_epoch=3):
            return NPCActionEvent(
                sequence=sequence,
                observed_ns=1000,
                sample_sequence=sample_sequence,
                scene_epoch=scene_epoch,
                process_creation_time=2,
                instance_id=instance_id,
                pid=1,
                action_sequence=MELEE_AUTO_SEQUENCE,
                previous_action_sequence=MELEE_AUTO_SEQUENCE,
                application_stamp=40 + sequence,
                flags=1,
            )

        events = [
            event(1),
            event(2),
            event(3, instance_id=8),
            event(4, sample_sequence=13),
            event(5, scene_epoch=2),
        ]
        rows = telos_actions(events, (1, 2, 3), 14, {7})
        self.assertEqual([row["event_sequence"] for row in rows], [1, 2])
        self.assertTrue(all(row["isolated_p1_melee_sequence_lead"] for row in rows))
        self.assertTrue(all("user_labeled_melee_auto" not in row for row in rows))


class ManualPrayerKeyTests(unittest.TestCase):
    def test_key_edges_require_control_and_the_foreground_game(self):
        keys = ManualPrayerKeys.__new__(ManualPrayerKeys)
        keys.focused_session_window = None
        keys.held_keys = set()
        keys.get_foreground_window = Mock(return_value=55)

        def report_game_pid(window, owner_pid):
            owner_pid._obj.value = 1
            return 123

        keys.get_window_thread_process_id = report_game_pid
        held_keys = {VK_F4, VK_CONTROL}
        keys.get_async_key_state = Mock(side_effect=lambda key: KEY_DOWN if key in held_keys else 0)

        def clock():
            return 1000

        self.assertEqual(keys.sample((4, 2, 3), clock), [])
        keys.get_async_key_state.assert_not_called()
        # Ctrl+F4 held when focus is gained is a baseline.
        self.assertEqual(keys.sample((1, 2, 3), clock), [])
        held_keys.remove(VK_F4)
        self.assertEqual(keys.sample((1, 2, 3), clock), [])
        held_keys.add(VK_F4)
        self.assertEqual(
            keys.sample((1, 2, 3), clock),
            [
                {
                    "key": "Ctrl+F4",
                    "bound_prayer": "melee",
                    "prayer": "melee",
                    "observed_ns": 1000,
                    "virtual_key": VK_F4,
                    "modifiers": 1,
                    "poll_interval_ms": 0.0,
                }
            ],
        )
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # No repeat while held.
        held_keys.remove(VK_CONTROL)
        held_keys.add(VK_F3)
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # Bare F3 is not the binding.
        held_keys.add(VK_CONTROL)
        # Adding Ctrl to an already held key is not a down edge.
        self.assertEqual(keys.sample((1, 2, 3), clock), [])


class AttackCatalogueTests(unittest.TestCase):
    def test_repository_catalogue_is_loadable_without_authorizing_input(self):
        catalogue = telos_prayer.load_attack_catalogue()
        self.assertEqual({row["phase"] for row in catalogue.document["coverage"]}, {1, 2, 3, 4, 5})
        self.assertTrue(catalogue.attacks)
        for row in catalogue.attacks:
            if not row["timing_validated"]:
                self.assertFalse(row["validated"])
                self.assertIsNone(row["cue_to_prayer_check_ms"])
            self.assertIsNone(
                catalogue.lookup(
                    row["phase"],
                    row["enrage_range"][0],
                    row["sequence"],
                    executable_sha256="0" * 64,
                    context=row.get("context"),
                )
            )

    def document(self):
        return {
            "schema_version": 1,
            "automatic_flick_ready": False,
            "attacks": [
                {
                    "phase": 1,
                    "enrage_range": [300, 300],
                    "sequence": 28929,
                    "client_sha256": "a" * 64,
                    "context": "isolated_p1_melee",
                    "sample_count": 2,
                    "name": "melee auto",
                    "classification_validated": True,
                    "timing_validated": False,
                    "validated": False,
                    "cue_to_prayer_check_ms": None,
                    "evidence_kind": "user_labelled_native_capture",
                }
            ],
        }

    def test_lookup_never_crosses_scope_or_authorizes_unmeasured_timing(self):
        document = self.document()
        catalogue = telos_prayer.AttackCatalogue(document)
        lookup = lambda phase, enrage, sequence, **kwargs: catalogue.lookup(
            phase, enrage, sequence, executable_sha256="a" * 64, **kwargs
        )
        self.assertIsNotNone(lookup(1, 300, 28929, context="isolated_p1_melee"))
        self.assertIsNone(lookup(1, 300, 28929))
        self.assertIsNone(lookup(2, 300, 28929, context="isolated_p1_melee"))
        self.assertIsNone(lookup(1, 301, 28929, context="isolated_p1_melee"))
        self.assertIsNone(lookup(1, 300, 28930, context="isolated_p1_melee"))
        self.assertIsNone(
            lookup(1, 300, 28929, context="isolated_p1_melee", require_validated=True)
        )
        self.assertIsNone(
            catalogue.lookup(1, 300, 28929, executable_sha256="b" * 64, context="isolated_p1_melee")
        )
        document["attacks"][0]["sample_count"] = 99
        self.assertEqual(catalogue.attacks[0]["sample_count"], 2)
        with self.assertRaises(TypeError):
            catalogue.attacks[0]["validated"] = True

    def test_ambiguous_or_unsubstantiated_validation_is_rejected(self):
        mutations = [
            lambda doc: doc.update(automatic_flick_ready=True),
            lambda doc: doc["attacks"].append(copy.deepcopy(doc["attacks"][0])),
            lambda doc: doc["attacks"][0].update(validated=True),
            lambda doc: doc["attacks"][0].update(timing_validated=True),
            lambda doc: doc["attacks"][0].update(evidence_kind="guide_lead"),
            lambda doc: doc["attacks"][0].update(client_sha256="unknown"),
        ]
        for mutation in mutations:
            document = self.document()
            mutation(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                telos_prayer.AttackCatalogue(document)


if __name__ == "__main__":
    unittest.main()
