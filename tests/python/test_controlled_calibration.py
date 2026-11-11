"""Bounded calibration sends one test switch and requires a protected control."""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from rs3_reader.prayer import Prayer

WORK_DIRECTORY = Path(__file__).resolve().parents[2] / "work"
sys.path.insert(0, str(WORK_DIRECTORY))
try:
    import calibrate_prayer_check as calibration
    from calibrate_prayer_check import PrayerCheckTrial
finally:
    sys.path.remove(str(WORK_DIRECTORY))

MS = 1_000_000


class ControlledCalibrationTests(unittest.TestCase):
    def test_input_requires_explicit_experiment_mode_before_opening_reader(self):
        argv = [
            "calibrate_prayer_check",
            "--pid",
            "1",
            "--delay-ms",
            "350",
            "--output",
            "unused.jsonl",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(calibration, "Reader") as reader,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as error:
                calibration.main()
            self.assertEqual(error.exception.code, 2)
            reader.assert_not_called()

    def setUp(self):
        self.commands = []
        self.now_ns = 0
        self.trial = PrayerCheckTrial(350, self.send)

    def send(self, prayer):
        self.commands.append((prayer, self.now_ns))
        return self.now_ns + 1

    def step(self, time_ms, prayer=Prayer.MELEE, form=22891, actions=(), hits=(), **changes):
        self.now_ns = time_ms * MS
        frame = {
            "now_ns": self.now_ns,
            "observed_ns": self.now_ns,
            "sequence": time_ms,
            "session": (1, 2, 3),
            "fresh": True,
            "focused": True,
            "prayer": prayer,
            "telos_form": form,
            "telos_instance": 4,
            "devotion_start": 100,
            "devotion_age_ms": max(0, time_ms - 1100),
        }
        frame.update(changes)
        return self.trial.step(frame, list(actions), list(hits))

    def cue(self, time_ms, sequence=1):
        return {
            "instance_id": 4,
            "action_sequence": 28929,
            "observed_ns": time_ms * MS,
            "sequence": sequence,
        }

    def hit(self, time_ms, amount=1, type_id=133):
        return {"observed_ns": time_ms * MS, "hit": {"amount": amount, "type_id": type_id}}

    def opening(self):
        self.step(1000, form=22892, devotion_start=10)
        self.step(1100)
        self.step(1200, actions=[self.cue(1200)])

    def soul_split_after_control(self):
        self.opening()
        self.step(1800, hits=[self.hit(1800)])
        self.step(1900, prayer=Prayer.SOUL_SPLIT)

    def test_one_delayed_switch_waits_for_ack_and_stops_in_melee(self):
        self.soul_split_after_control()
        self.step(3600, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(3600, 2)])
        self.step(3949, prayer=Prayer.SOUL_SPLIT)
        self.assertEqual(len(self.commands), 1)
        updates = self.step(3950, prayer=Prayer.SOUL_SPLIT)
        self.assertEqual(updates[0]["requested_after_cue_ms"], 350)
        self.assertEqual(self.commands, [(Prayer.SOUL_SPLIT, 1800 * MS), (Prayer.MELEE, 3950 * MS)])
        self.step(4200, prayer=Prayer.SOUL_SPLIT, hits=[self.hit(4200, 1400)])
        self.assertFalse(self.trial.finished)
        updates = self.step(4300)
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.trial.state, "completed")
        self.assertEqual(updates[-1]["hit_amount"], 1400)
        self.assertFalse(updates[-1]["prayer_check_deadline_validated"])
        self.step(6000, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(6000, 3)])
        self.assertEqual(len(self.commands), 2)

    def test_a_miss_does_not_establish_devotion_control(self):
        self.opening()
        self.step(1800, hits=[self.hit(1800, 0, 482)])
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.commands, [])

    def test_setup_waits_for_manual_melee_instead_of_toggling_through_menus(self):
        self.step(1000, prayer=Prayer.NONE, form=22892, devotion_start=10)
        self.step(3000, prayer=Prayer.NONE, form=22892, devotion_start=10)
        self.assertFalse(self.trial.finished)
        self.assertEqual(self.trial.state, "waiting")
        self.assertEqual(self.commands, [])

    def test_a_stale_auto_cue_never_drives_a_test(self):
        self.step(1000, form=22892, devotion_start=10)
        self.step(1100)
        self.step(1600, actions=[self.cue(1200)])
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.commands, [])

    def test_a_predispatch_miss_cancels_without_a_timed_test_command(self):
        self.soul_split_after_control()
        updates = self.step(
            3600,
            prayer=Prayer.SOUL_SPLIT,
            actions=[self.cue(3600, 2)],
            hits=[self.hit(3600, 0, 482)],
        )
        self.assertEqual(self.trial.state, "restoring")
        accepted = [update for update in updates if update["event"] == "input_accepted"]
        self.assertIsNone(accepted[0]["requested_after_cue_ms"])
        self.step(3900)
        self.assertTrue(self.trial.finished)

    def test_late_melee_confirmation_does_not_request_another_manual_toggle(self):
        self.soul_split_after_control()
        self.step(3600, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(3600, 2)])
        self.step(3950, prayer=Prayer.SOUL_SPLIT)
        updates = self.step(5300)
        self.assertTrue(self.trial.finished)
        self.assertFalse(any(update["event"] == "manual_protection_required" for update in updates))

    def test_unmitigated_control_never_sends_soul_split(self):
        self.opening()
        self.step(1800, hits=[self.hit(1800, 700)])
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.commands, [])

    def test_focus_loss_never_sends_input_to_another_window(self):
        self.soul_split_after_control()
        updates = self.step(2000, prayer=Prayer.SOUL_SPLIT, focused=False)
        self.assertTrue(self.trial.finished)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(updates[-1]["event"], "manual_protection_required")

    def test_a_special_restores_melee_and_waits_for_acknowledgement(self):
        self.soul_split_after_control()
        special = dict(self.cue(2400, 2), action_sequence=28931)
        self.step(2400, prayer=Prayer.SOUL_SPLIT, actions=[special])
        self.assertEqual(self.trial.state, "restoring")
        self.assertFalse(self.trial.finished)
        self.step(2700)
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.commands[-1][0], Prayer.MELEE)

    def test_an_expired_control_window_cannot_authorize_a_test(self):
        self.soul_split_after_control()
        self.step(3600, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(3600, 2)], devotion_age_ms=9000)
        self.assertEqual(self.trial.state, "restoring")
        self.assertEqual(self.commands[-1][0], Prayer.MELEE)


if __name__ == "__main__":
    unittest.main()
