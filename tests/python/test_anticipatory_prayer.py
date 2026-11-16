"""Exercise the C predictor through the bounded foreground-input controller."""

import sys
import unittest
from pathlib import Path

from rs3_reader.native import library_path
from rs3_reader.prayer import CyclePredictor, Prayer

WORK_DIRECTORY = Path(__file__).resolve().parents[2] / "work"
sys.path.insert(0, str(WORK_DIRECTORY))
try:
    from calibrate_prayer_check import PrayerCheckTrial
finally:
    sys.path.remove(str(WORK_DIRECTORY))

MS = 1_000_000


@unittest.skipUnless(library_path().is_file(), "Build the native cycle predictor first")
class AnticipatoryPrayerTests(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.now_ns = 0
        self.predictor = CyclePredictor()
        self.trial = PrayerCheckTrial(0, self.send, predictor=self.predictor, cycles=2)

    def send(self, prayer):
        self.commands.append((prayer, self.now_ns))
        return self.now_ns + 1

    def step(self, at_ms, prayer=Prayer.MELEE, form=22891, actions=(), hits=(), **changes):
        self.now_ns = at_ms * MS
        frame = {
            "now_ns": self.now_ns,
            "observed_ns": self.now_ns,
            "sequence": at_ms,
            "session": (1, 2, 3),
            "fresh": True,
            "focused": True,
            "prayer": prayer,
            "telos_form": form,
            "telos_instance": 4,
            "devotion_start": 100,
            "devotion_age_ms": max(0, at_ms - 1100),
            "stage_signature": 0,
        }
        frame.update(changes)
        return self.trial.step(frame, list(actions), list(hits))

    def cue(self, at_ms, sequence=1, action=28929):
        return {
            "instance_id": 4,
            "action_sequence": action,
            "observed_ns": at_ms * MS,
            "sequence": sequence,
        }

    def hit(self, at_ms, amount=1, type_id=133):
        return {"observed_ns": at_ms * MS, "hit": {"amount": amount, "type_id": type_id}}

    def control(self):
        self.step(1000, form=22892, devotion_start=10)
        self.step(1100)
        self.step(1200, actions=[self.cue(1200)])
        self.step(1750, hits=[self.hit(1750)])
        self.step(2300, prayer=Prayer.SOUL_SPLIT)

    def test_protection_is_sent_before_next_cue_then_soul_split_returns(self):
        self.control()
        self.assertEqual(self.commands, [(Prayer.SOUL_SPLIT, 1750 * MS)])
        self.step(3499, prayer=Prayer.SOUL_SPLIT)
        self.assertEqual(len(self.commands), 1)
        updates = self.step(3500, prayer=Prayer.SOUL_SPLIT)
        self.assertEqual(self.commands[-1], (Prayer.MELEE, 3500 * MS))
        self.assertAlmostEqual(updates[0]["accepted_before_predicted_cue_ms"], 100, places=3)
        self.step(3580, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(3580, 2)])
        self.step(4100)
        self.step(4130, hits=[self.hit(4130)])
        self.assertEqual(self.commands[-1], (Prayer.SOUL_SPLIT, 4130 * MS))
        self.assertFalse(self.trial.finished)
        self.step(4700, prayer=Prayer.SOUL_SPLIT)
        self.step(5880, prayer=Prayer.SOUL_SPLIT)
        self.assertEqual(self.commands[-1], (Prayer.MELEE, 5880 * MS))
        self.step(5960, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(5960, 3)])
        self.step(6480)
        self.step(6510, hits=[self.hit(6510)])
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.trial.completed_cycles, 2)
        self.assertEqual(len(self.commands), 4)

    def test_magic_style_is_explicit_in_the_native_calculator(self):
        plan = self.predictor.predict(
            ms(3500), ms(1200), ms(1750), Prayer.SOUL_SPLIT, protection_prayer=Prayer.MAGIC
        )
        self.assertEqual(plan.desired_prayer, Prayer.MAGIC)

    def test_stage_signature_change_stops_the_melee_cycle(self):
        self.control()
        updates = self.step(2400, prayer=Prayer.SOUL_SPLIT, stage_signature=1)
        self.assertEqual(self.trial.state, "restoring")
        self.assertIn("manual style selection", updates[0]["reason"])
        self.step(2650, stage_signature=1)
        self.assertTrue(self.trial.finished)

    def test_unknown_attack_or_rhythm_change_invalidates_prediction(self):
        for action, at_ms in ((28938, 2400), (28929, 2400)):
            with self.subTest(action=action):
                self.setUp()
                self.control()
                self.step(at_ms, prayer=Prayer.SOUL_SPLIT, actions=[self.cue(at_ms, 2, action)])
                self.assertEqual(self.trial.state, "restoring")
                self.assertEqual(self.commands[-1][0], Prayer.MELEE)

    def test_missing_auto_cue_does_not_free_run(self):
        self.control()
        self.step(3500, prayer=Prayer.SOUL_SPLIT)
        self.step(3550)
        self.step(3801)
        self.assertTrue(self.trial.finished)
        self.assertEqual(self.trial.state, "aborted")

    def test_manual_magic_is_not_overwritten_by_the_melee_cycle(self):
        self.control()
        self.step(2400, prayer=Prayer.MAGIC)
        self.assertTrue(self.trial.finished)
        self.assertEqual(len(self.commands), 1)

    def test_unclassified_incoming_hit_ends_the_cycle(self):
        self.control()
        self.step(2400, prayer=Prayer.SOUL_SPLIT, hits=[self.hit(2400, 500, 135)])
        self.assertEqual(self.trial.state, "restoring")

    def test_manual_magic_with_pending_input_is_not_overwritten(self):
        self.step(1000, form=22892, devotion_start=10)
        self.step(1100)
        self.step(1200, actions=[self.cue(1200)])
        self.step(1750, hits=[self.hit(1750)])
        self.step(1800, prayer=Prayer.MAGIC)
        self.assertTrue(self.trial.finished)
        self.assertEqual(len(self.commands), 1)


def ms(value):
    return value * MS


if __name__ == "__main__":
    unittest.main()
