import unittest
from unittest.mock import Mock

from native import NPCActionEvent
from telos_prayer import ManualPrayerKeys, telos_actions


class TelosPrayerTests(unittest.TestCase):
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
