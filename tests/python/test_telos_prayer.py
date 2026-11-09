import unittest
from unittest.mock import Mock

from native import NPCActionEvent
from prayer import Prayer
from telos_prayer import ManualPrayerKeys, PrayerTrial, telos_actions


MS = 1_000_000


class TelosPrayerTests(unittest.TestCase):
    def test_trial_waits_for_ack_and_repeats_the_600_ms_cycle(self):
        send = Mock(side_effect=[t * MS + 1 for t in (1000, 1600, 2000, 2600)])
        trial = PrayerTrial(send)
        target = (1, 2, 3)
        trial.step(target, Prayer.SOUL_SPLIT, 999 * MS, 1000 * MS, 1000 * MS)
        send.assert_called_once_with(Prayer.MELEE)
        self.assertEqual(trial.step(target, Prayer.MELEE, 1000 * MS, 1010 * MS), [])
        self.assertEqual(trial.pending, Prayer.MELEE)
        self.assertEqual(trial.step(target, Prayer.MELEE, 1019 * MS, 1020 * MS)[0]["action"], "acknowledged")
        self.assertEqual(trial.step(target, Prayer.MELEE, 1598 * MS, 1599 * MS), [])
        send.assert_called_once_with(Prayer.MELEE)
        trial.step(target, Prayer.MELEE, 1599 * MS, 1600 * MS)
        self.assertEqual(send.call_args.args, (Prayer.SOUL_SPLIT,))
        self.assertEqual(trial.step(target, Prayer.MELEE, 1619 * MS, 1620 * MS), [])
        trial.step(target, Prayer.SOUL_SPLIT, 1629 * MS, 1630 * MS)
        self.assertEqual(trial.completed_cycles, 1)
        self.assertEqual(trial.step(target, Prayer.SOUL_SPLIT, 1700 * MS, 1700 * MS), [])
        self.assertEqual(send.call_count, 2)
        trial.step(target, Prayer.SOUL_SPLIT, 1999 * MS, 2000 * MS, 2000 * MS)
        trial.step(target, Prayer.MELEE, 2019 * MS, 2020 * MS)
        self.assertEqual(trial.step(target, Prayer.MELEE, 2598 * MS, 2599 * MS), [])
        trial.step(target, Prayer.MELEE, 2599 * MS, 2600 * MS)
        trial.step(target, Prayer.SOUL_SPLIT, 2629 * MS, 2630 * MS)
        self.assertEqual(trial.completed_cycles, 2)
        self.assertEqual([call.args[0] for call in send.call_args_list],
                         [Prayer.MELEE, Prayer.SOUL_SPLIT] * 2)

    def test_new_cue_can_share_the_soul_split_acknowledgement_sample(self):
        send = Mock(side_effect=[1600 * MS + 1, 1630 * MS + 1])
        trial = PrayerTrial(send)
        target = (1, 2, 3)
        trial.step(target, Prayer.MELEE, 999 * MS, 1000 * MS, 1000 * MS)
        trial.step(target, Prayer.MELEE, 1599 * MS, 1600 * MS)
        updates = trial.step(target, Prayer.SOUL_SPLIT, 1629 * MS, 1630 * MS, 1630 * MS)
        self.assertEqual([update["action"] for update in updates], ["acknowledged", "sent"])
        self.assertEqual(trial.completed_cycles, 1)
        self.assertEqual(trial.release_ns, 2230 * MS)
        self.assertEqual(trial.pending, Prayer.MELEE)

    def test_already_active_prayers_are_not_toggled(self):
        send = Mock()
        trial = PrayerTrial(send)
        target = (1, 2, 3)
        trial.step(target, Prayer.MELEE, 999 * MS, 1000 * MS, 1000 * MS)
        trial.step(target, Prayer.MELEE, 1299 * MS, 1300 * MS, 1300 * MS)
        self.assertEqual(trial.step(target, Prayer.MELEE, 1599 * MS, 1600 * MS), [])
        trial.step(target, Prayer.SOUL_SPLIT, 1899 * MS, 1900 * MS)
        send.assert_not_called()
        self.assertEqual(trial.completed_cycles, 1)
        self.assertIsNone(trial.target)

    def test_late_melee_confirmation_can_still_return_to_soul_split(self):
        send = Mock(side_effect=[1000 * MS + 1, 1790 * MS + 1])
        trial = PrayerTrial(send)
        target = (1, 2, 3)
        trial.step(target, Prayer.SOUL_SPLIT, 999 * MS, 1000 * MS, 1000 * MS)
        self.assertEqual(trial.step(target, Prayer.UNKNOWN, 1599 * MS, 1600 * MS), [])
        send.assert_called_once_with(Prayer.MELEE)
        updates = trial.step(target, Prayer.MELEE, 1789 * MS, 1790 * MS)
        self.assertEqual([update["action"] for update in updates], ["acknowledged", "sent"])
        self.assertEqual(send.call_args.args, (Prayer.SOUL_SPLIT,))

    def test_cue_waits_briefly_for_pending_soul_split(self):
        target = (1, 2, 3)
        for ack_ms, fresh in [(1680, True), (1750, False)]:
            with self.subTest(ack_ms=ack_ms):
                send = Mock(side_effect=[1600 * MS + 1, ack_ms * MS + 1])
                trial = PrayerTrial(send)
                trial.step(target, Prayer.MELEE, 999 * MS, 1000 * MS, 1000 * MS)
                trial.step(target, Prayer.MELEE, 1599 * MS, 1600 * MS)
                self.assertEqual(trial.step(target, Prayer.MELEE, 1629 * MS, 1630 * MS, 1630 * MS), [])
                send.assert_called_once_with(Prayer.SOUL_SPLIT)
                trial.step(target, Prayer.SOUL_SPLIT, (ack_ms - 1) * MS, ack_ms * MS)
                self.assertEqual(trial.completed_cycles, 1)
                self.assertEqual(send.call_count, 2 if fresh else 1)

    def test_trial_rearms_after_missing_ack_or_focus(self):
        target = (1, 2, 3)
        for next_target, now_ms, reason in [(target, 2210, "not observed"), (None, 1600, "foreground")]:
            with self.subTest(reason=reason):
                send = Mock(side_effect=[1000 * MS + 1, 2300 * MS + 1])
                trial = PrayerTrial(send)
                trial.step(target, Prayer.SOUL_SPLIT, 999 * MS, 1000 * MS, 1000 * MS)
                updates = trial.step(next_target, Prayer.SOUL_SPLIT, (now_ms - 1) * MS, now_ms * MS)
                self.assertEqual(updates[0]["action"], "cycle_skipped")
                self.assertIn(reason, updates[0]["reason"])
                self.assertEqual(trial.step(target, Prayer.SOUL_SPLIT, 2299 * MS, 2300 * MS), [])
                send.assert_called_once_with(Prayer.MELEE)
                trial.step(target, Prayer.SOUL_SPLIT, 2299 * MS, 2300 * MS, 2300 * MS)
                self.assertEqual(send.call_count, 2)

    def test_input_rejection_skips_cycle_and_cools_down_before_retrying(self):
        send = Mock(side_effect=[RuntimeError("input rejected"), 2300 * MS + 1])
        trial = PrayerTrial(send)
        target = (1, 2, 3)
        updates = trial.step(target, Prayer.SOUL_SPLIT, 999 * MS, 1000 * MS, 1000 * MS)
        self.assertEqual(updates[0]["action"], "cycle_skipped")
        self.assertEqual(trial.step(target, Prayer.SOUL_SPLIT, 2099 * MS, 2100 * MS, 2100 * MS), [])
        send.assert_called_once_with(Prayer.MELEE)
        trial.step(target, Prayer.SOUL_SPLIT, 2299 * MS, 2300 * MS, 2300 * MS)
        self.assertEqual(send.call_count, 2)

    def test_same_id_events_require_matching_actor_and_sample(self):
        def event(sequence, instance=7, sample=14, epoch=3):
            return NPCActionEvent(sequence=sequence, observed_ns=1000, sample_sequence=sample,
                                  scene_epoch=epoch, process_creation_time=2, instance_id=instance,
                                  pid=1, action_sequence=28929, previous_action_sequence=28929,
                                  application_stamp=40 + sequence, flags=1)

        events = [event(1), event(2), event(3, instance=8), event(4, sample=13), event(5, epoch=2)]
        rows = telos_actions(events, (1, 2, 3), 14, {7})
        self.assertEqual([row["event_sequence"] for row in rows], [1, 2])
        self.assertTrue(all(row["user_labeled_melee_auto"] for row in rows))

    def test_key_edges_are_unmodified_and_foreground_only(self):
        keys = ManualPrayerKeys.__new__(ManualPrayerKeys)
        keys.focus, keys.down = None, set()
        keys.foreground = Mock(return_value=55)

        def game_pid(window, out):
            out._obj.value = 1
            return 123

        keys.window_pid = game_pid
        pressed = {0x70}
        keys.key_state = Mock(side_effect=lambda key: -32768 if key in pressed else 0)
        clock = lambda: 1000
        self.assertEqual(keys.sample((4, 2, 3), clock), [])
        keys.key_state.assert_not_called()
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # F1 held on focus gain.
        pressed.clear()
        self.assertEqual(keys.sample((1, 2, 3), clock), [])
        pressed.add(0x70)
        self.assertEqual(keys.sample((1, 2, 3), clock),
                         [{"key": "F1", "bound_prayer": "melee", "observed_ns": 1000}])
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # No repeat while held.
        pressed.update((0x71, 0x11))
        self.assertEqual(keys.sample((1, 2, 3), clock), [])  # Ctrl+F2 is a separate binding.
