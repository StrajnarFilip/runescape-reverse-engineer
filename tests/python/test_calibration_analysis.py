"""Timing evidence must preserve misses, session boundaries and unmatched keys."""

import importlib.util
import unittest
from pathlib import Path

ANALYSIS_PATH = Path(__file__).resolve().parents[2] / "work" / "analyze_telos_calibration.py"
specification = importlib.util.spec_from_file_location("analyze_telos_calibration", ANALYSIS_PATH)
analysis = importlib.util.module_from_spec(specification)
specification.loader.exec_module(analysis)

MILLISECOND_NS = 1_000_000
PID = 1
PROCESS_CREATION_TIME = 2
SCENE_EPOCH = 3
TELOS_INSTANCE_ID = 4
VK_F2 = 113
VK_F3 = 114
CTRL_MODIFIER = 1


def ms(value):
    return value * MILLISECOND_NS


def capture_row(time_ms, active_prayer="melee"):
    return {
        "pid": PID,
        "process_creation_time": PROCESS_CREATION_TIME,
        "observed_ns": ms(time_ms),
        "combat": {"scene_epoch": SCENE_EPOCH, "active_prayer": active_prayer},
        "animations": [{"name": "Telos, the Warden", "instance_id": TELOS_INSTANCE_ID}],
        "npc_action_events": [],
        "player_hit_events": [],
        "manual_prayer_keys": [],
    }


def melee_cue(observed_ns):
    return {
        "pid": PID,
        "process_creation_time": PROCESS_CREATION_TIME,
        "scene_epoch": SCENE_EPOCH,
        "instance_id": TELOS_INSTANCE_ID,
        "sequence": 1,
        "observed_ns": observed_ns,
        "action_sequence": analysis.MELEE_AUTO_SEQUENCE,
    }


def player_hit(observed_ns, type_id, amount, scene_epoch=SCENE_EPOCH):
    return {
        "pid": PID,
        "process_creation_time": PROCESS_CREATION_TIME,
        "scene_epoch": scene_epoch,
        "sequence": 1,
        "observed_ns": observed_ns,
        "hit": {"type_id": type_id, "amount": amount},
    }


def soul_split_key(time_ms, virtual_key=VK_F3, prayer="soul_split"):
    return {
        "prayer": prayer,
        "observed_ns": ms(time_ms),
        "previous_poll_ns": ms(time_ms - 10),
        "virtual_key": virtual_key,
        "modifiers": CTRL_MODIFIER,
    }


class CalibrationAnalysisTests(unittest.TestCase):
    def test_same_tick_miss_is_not_lost_or_counted_as_damage(self):
        row = capture_row(1000)
        row["npc_action_events"] = [melee_cue(ms(1000))]
        row["player_hit_events"] = [player_hit(ms(1000) - 5000, analysis.MISS_HIT_TYPE, 0)]
        result = analysis.summarize([row])
        self.assertEqual(len(result["cue_hit_pairs"]), 1)
        self.assertEqual(result["cue_to_observed_damage_delay"]["count"], 0)
        self.assertEqual(result["cue_to_observed_miss_delay"]["count"], 1)

    def test_restarted_scene_hit_cannot_be_assigned_to_an_old_cue(self):
        row = capture_row(1000)
        row["npc_action_events"] = [melee_cue(ms(1000))]
        row["player_hit_events"] = [
            player_hit(ms(1600), analysis.MELEE_DAMAGE_HIT_TYPE, 800, scene_epoch=SCENE_EPOCH + 1)
        ]
        self.assertEqual(analysis.summarize([row])["cue_hit_pairs"], [])

    def test_only_a_matching_observed_overhead_change_acknowledges_a_key(self):
        row = capture_row(1000)
        row["manual_prayer_keys"] = [
            soul_split_key(1010),
            soul_split_key(1020, virtual_key=VK_F2, prayer="ranged"),
        ]
        result = analysis.summarize([row, capture_row(1300, "soul_split")])
        self.assertEqual(result["manual_key_events"], 2)
        self.assertEqual(len(result["matched_keyboard_changes"]), 1)
        self.assertEqual(result["sampled_key_to_overhead_delay"]["median_ms"], 290)

    def test_one_overhead_change_does_not_acknowledge_two_presses(self):
        row = capture_row(1000)
        row["manual_prayer_keys"] = [soul_split_key(1010), soul_split_key(1100)]
        result = analysis.summarize([row, capture_row(1300, "soul_split")])
        self.assertEqual(result["manual_key_events"], 2)
        self.assertEqual(len(result["matched_keyboard_changes"]), 1)


if __name__ == "__main__":
    unittest.main()
