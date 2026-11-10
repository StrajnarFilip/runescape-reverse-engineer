"""Experimental Telos prayer trial, action-event filtering and manual key sampling."""

import unittest
from unittest.mock import Mock

from rs3_reader.native import NPCActionEvent
from rs3_reader.prayer import Prayer
from rs3_reader.telos_prayer import (
    MELEE_AUTO_SEQUENCE,
    VK_F1,
    VK_F2,
    ManualPrayerKeys,
    PrayerTrial,
    telos_actions,
)

MILLISECOND_NS = 1_000_000
TARGET = (1, 2, 3)
VK_CONTROL = 0x11
KEY_DOWN = -32768


def ms(value):
    return value * MILLISECOND_NS


class PrayerTrialTests(unittest.TestCase):
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
        self.assertTrue(all(row["user_labeled_melee_auto"] for row in rows))


class ManualPrayerKeyTests(unittest.TestCase):
    def test_key_edges_are_unmodified_and_foreground_only(self):
        keys = ManualPrayerKeys.__new__(ManualPrayerKeys)
        keys.focused_session_window = None
        keys.held_keys = set()
        keys.get_foreground_window = Mock(return_value=55)

        def report_game_pid(window, owner_pid):
            owner_pid._obj.value = 1
            return 123

        keys.get_window_thread_process_id = report_game_pid
        held_keys = {VK_F1}
        keys.get_async_key_state = Mock(side_effect=lambda key: KEY_DOWN if key in held_keys else 0)

        def clock():
            return 1000

        self.assertEqual(keys.sample((4, 2, 3), clock), [])
        keys.get_async_key_state.assert_not_called()
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # F1 held when focus is gained.
        held_keys.clear()
        self.assertEqual(keys.sample((1, 2, 3), clock), [])
        held_keys.add(VK_F1)
        self.assertEqual(
            keys.sample((1, 2, 3), clock),
            [{"key": "F1", "bound_prayer": "melee", "observed_ns": 1000}],
        )
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # No repeat while held.
        held_keys.update((VK_F2, VK_CONTROL))
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # Ctrl+F2 is a separate binding.


if __name__ == "__main__":
    unittest.main()
