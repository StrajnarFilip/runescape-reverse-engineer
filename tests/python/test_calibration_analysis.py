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
    def test_zero_damage_is_not_a_failed_protection_result(self):
        row = capture_row(2000)
        row.update(
            input_mode="controlled_calibration",
            trial_state="completed",
            client_logic_tick=200,
            devotion_cooldown={"timer_valid": True, "start_tick": 0, "end_tick": 3000},
        )
        row["calibration_updates"] = [
            {"event": "paired_hit", "stage": "control", "hit": player_hit(ms(1000), 133, 1)},
            {"event": "paired_hit", "stage": "result", "hit": player_hit(ms(1600), 133, 0)},
        ]
        result = analysis.controlled_trial_summary([row], clean_opening_confirmed=True)
        self.assertEqual(result["outcome"], "zero_damage_inconclusive")

    def test_controlled_inputs_are_not_reported_as_manual_only(self):
        row = capture_row(1000)
        row.update(input_mode="controlled_calibration", trial_state="completed")
        row["calibration_updates"] = [
            {
                "event": "input_accepted",
                "prayer": "melee",
                "accepted_ns": ms(1359),
                "requested_after_cue_ms": 350,
                "accepted_after_cue_ms": 359,
            },
            {"event": "acknowledged", "prayer": "melee", "observed_ns": ms(1600)},
            {"event": "paired_hit", "stage": "result", "hit": player_hit(ms(1600), 133, 1)},
        ]
        result = analysis.controlled_trial_summary([row])
        self.assertTrue(result["input_sent_by_calibration"])
        self.assertEqual(result["accepted_test_delay_ms"], 359)
        self.assertEqual(result["test_hit"], {"amount": 1, "type_id": 133})
        self.assertTrue(result["melee_acknowledged_after_test_command"])
        self.assertFalse(result["prayer_check_deadline_validated"])

    def devotion_rows(self):
        cast = capture_row(800)
        cast["manual_ability_keys"] = [{"ability": "devotion", "observed_ns": ms(800)}]
        timer = capture_row(900)
        timer["devotion_cooldown"] = {
            "struct_id": analysis.DEVOTION_STRUCT_ID,
            "timer_valid": True,
            "start_tick": 10,
            "end_tick": 3010,
        }
        cue = capture_row(1000, "soul_split")
        cue["npc_action_events"] = [melee_cue(ms(1000))]
        press = capture_row(1110, "soul_split")
        press["manual_prayer_keys"] = [soul_split_key(1110, virtual_key=115, prayer="melee")]
        hit = capture_row(1600, "soul_split")
        hit["player_hit_events"] = [player_hit(ms(1600), analysis.MELEE_DAMAGE_HIT_TYPE, 1)]
        acknowledgement = capture_row(1610)
        acknowledgement["previous_sample_ns"] = ms(1600)
        return [cast, timer, cue, press, hit, acknowledgement]

    def test_devotion_outcome_requires_a_clean_user_label(self):
        rows = self.devotion_rows()
        result = analysis.devotion_opening_summary(rows)
        self.assertEqual(result["trials"][0]["outcome"], "not_classified")
        result = analysis.devotion_opening_summary(rows, clean_opening_confirmed=True)
        trial = result["trials"][0]
        self.assertEqual(trial["outcome"], "one_damage_during_user_confirmed_devotion")
        self.assertEqual(trial["sampled_melee_key_after_cue_ms"], 110)
        self.assertEqual(trial["melee_first_observed_after_cue_ms"], 610)
        self.assertEqual(trial["melee_previous_sample_after_cue_ms"], 600)
        self.assertEqual(trial["prayer_near_hit_observation"], "soul_split")
        self.assertFalse(result["prayer_check_deadline_validated"])
        self.assertIsNone(result["devotion_buff_active_from_memory"])

    def test_an_old_cooldown_does_not_confirm_a_new_devotion_cast(self):
        rows = self.devotion_rows()
        baseline = capture_row(500)
        baseline["devotion_cooldown"] = rows[1]["devotion_cooldown"]
        self.assertIsNone(analysis.devotion_opening_summary([baseline] + rows))

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
