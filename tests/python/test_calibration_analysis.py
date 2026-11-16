"""Timing evidence must preserve misses, session boundaries and unmatched keys."""

import copy
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_complete_telemetry_loss_counts_and_input_history_remain_explicit(self):
        row = capture_row(1000)
        row.pop("player_hit_events")
        row.update(
            npc_action_events_lost=1,
            hit_splats={"events_lost": 2},
            player_hit_splats={"newly_observed": [], "events_lost": 3},
            inventory_changes={"newly_observed": [], "events_lost": 4},
            input_sent_by_calibration=False,
        )
        result = analysis.summarize([row])
        self.assertEqual(
            result["native_queue_losses"],
            {"npc_actions": 1, "npc_hits": 2, "player_hits": 3, "inventory_changes": 4},
        )
        self.assertIs(result["input_sent_by_calibration"], False)
        unknown = copy.deepcopy(row)
        unknown.pop("inventory_changes")
        unknown.pop("input_sent_by_calibration")
        result = analysis.summarize([row, unknown])
        self.assertIsNone(result["native_queue_losses"]["inventory_changes"])
        self.assertIsNone(result["input_sent_by_calibration"])
        unknown["input_sent_by_calibration"] = True
        self.assertIs(analysis.summarize([row, unknown])["input_sent_by_calibration"], True)

    def test_input_history_does_not_treat_numeric_or_missing_flags_as_false(self):
        for value in (0, 1, "false", None):
            self.assertIsNone(analysis.input_history([{"input_sent_by_calibration": value}]))
        self.assertIsNone(analysis.input_history([]))
        self.assertIsNone(analysis.input_history([{}]))

    def test_output_aliases_cannot_overwrite_capture_annotations_or_another_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            capture, annotation = directory / "capture.jsonl", directory / "labels.json"
            capture.write_text("preserve raw capture\n")
            annotation.write_text("preserve annotations\n")
            alias = directory / "alias.jsonl"
            os.link(capture, alias)
            for inputs, outputs in (
                ([capture], [capture]),
                ([capture], [alias]),
                ([capture, annotation], [annotation]),
                ([capture], [directory / "out.json", directory / "out.json"]),
            ):
                with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                    analysis.validate_output_paths(inputs, outputs)
            analysis.validate_output_paths([capture, annotation], [directory / "out.json", None])
            self.assertEqual(capture.read_text(), "preserve raw capture\n")
            self.assertEqual(annotation.read_text(), "preserve annotations\n")

    def test_cli_rejects_capture_output_alias_before_reading_or_writing(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            capture = Path(temporary) / "capture.jsonl"
            capture.write_text("preserve even non-JSON input\n")
            arguments = [
                "analysis",
                "--capture",
                str(capture),
                "--output",
                str(capture),
                "--phase",
                "1",
                "--enrage",
                "300",
                "--session-date",
                "2026-11-14",
            ]
            with patch("sys.argv", arguments), self.assertRaises(SystemExit) as error:
                analysis.main()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(capture.read_text(), "preserve even non-JSON input\n")

    def test_legacy_filtered_cue_uses_its_parent_session_without_overwriting_identity(self):
        row = capture_row(1000)
        row.pop("npc_action_events")
        row["telos_actions"] = [
            {
                "event_sequence": 9,
                "instance_id": 4,
                "observed_ns": ms(1000),
                "action_sequence": 28929,
            }
        ]
        self.assertEqual(analysis.summarize([row])["action_sequence_counts"], {"28929": 1})
        row["telos_actions"][0]["scene_epoch"] = 4
        self.assertEqual(analysis.summarize([row])["action_sequence_counts"], {})

    def test_current_recorder_normalizes_keys_and_unknown_poll_intervals(self):
        first = capture_row(1000)
        first["telos"] = first.pop("animations")
        first["telos"][0].pop("name")
        first["sample_started_ns"] = first.pop("observed_ns")
        first["manual_prayer_presses"] = [
            {"key": "Ctrl+F1", "bound_prayer": "magic", "observed_ns": ms(1100)}
        ]
        first.pop("manual_prayer_keys")
        result = analysis.summarize([first, capture_row(1200, "magic")])
        self.assertEqual(result["matched_keyboard_changes"][0]["virtual_key"], 112)
        self.assertIsNone(result["matched_keyboard_changes"][0]["key_poll_interval_ms"])
        self.assertIsNone(result["npc_action_events_lost"])

    def test_baseline_does_not_acknowledge_a_key_and_invalid_rows_do_not_crash(self):
        row = capture_row(1000, "soul_split")
        row["manual_prayer_keys"] = [soul_split_key(1000)]
        invalid = capture_row(0)
        invalid.update(pid=0, observed_ns=None)
        self.assertEqual(analysis.summarize([invalid, row])["matched_keyboard_changes"], [])

    def test_reused_event_sequence_in_different_sessions_is_distinct(self):
        rows = []
        for epoch in (3, 4):
            row = capture_row(1000)
            row["combat"]["scene_epoch"] = epoch
            cue = melee_cue(ms(1000))
            cue["scene_epoch"] = epoch
            row["npc_action_events"] = [cue]
            row["player_hit_events"] = [player_hit(ms(1500), 133, 1, scene_epoch=epoch)]
            rows.append(row)
        self.assertEqual(
            len(analysis.summarize(rows, isolated_phase_1_melee=True)["cue_hit_pairs"]), 2
        )

    def test_forecast_and_observed_prayer_intervals_are_distinct(self):
        row = capture_row(5000)
        row.update(
            anticipatory_mode=True, input_mode="anticipatory_calibration", trial_state="completed"
        )
        row["calibration_updates"] = [
            {
                "event": "paired_hit",
                "stage": "control",
                "cue_sequence": 1,
                "hit": player_hit(ms(1500), 133, 1),
            },
            {
                "event": "prediction_scheduled",
                "anchor_cue_sequence": 1,
                "next_cue_ns": ms(3400),
                "protection_send_ns": ms(3300),
                "period_ms": 2400,
                "lead_before_cue_ms": 100,
            },
            {"event": "input_accepted", "prayer": "soul_split", "accepted_ns": ms(1510)},
            {"event": "acknowledged", "prayer": "soul_split", "observed_ns": ms(2100)},
            {
                "event": "input_accepted",
                "prayer": "melee",
                "accepted_ns": ms(3300),
                "predicted_cue_ns": ms(3400),
            },
            {"event": "auto_cue", "stage": "result", "cue_sequence": 2, "cue_ns": ms(3370)},
            {"event": "acknowledged", "prayer": "melee", "observed_ns": ms(3900)},
            {
                "event": "paired_hit",
                "stage": "result",
                "cue_sequence": 2,
                "hit": player_hit(ms(3920), 133, 1),
            },
            {"event": "input_accepted", "prayer": "soul_split", "accepted_ns": ms(3930)},
            {"event": "acknowledged", "prayer": "soul_split", "observed_ns": ms(4500)},
            {"event": "cycle_completed"},
        ]
        result = analysis.anticipatory_trial_summary([row])
        cycle = result["cycles"][0]
        self.assertEqual(cycle["prediction_error_ms"], -30)
        self.assertEqual(cycle["command_before_actual_cue_ms"], 70)
        self.assertEqual(cycle["melee_observed_before_hit_ms"], 20)
        self.assertEqual(cycle["soul_split_observed_ms"], 1800)
        self.assertEqual(cycle["melee_observed_ms_until_next_soul_split"], 600)
        self.assertIsNone(analysis.controlled_trial_summary([row]))
        self.assertFalse(result["prayer_check_deadline_validated"])

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
        result = analysis.summarize([row], isolated_phase_1_melee=True)
        self.assertEqual(len(result["cue_hit_pairs"]), 1)
        self.assertEqual(result["cue_to_observed_damage_delay"]["count"], 0)
        self.assertEqual(result["cue_to_observed_miss_delay"]["count"], 1)

    def test_restarted_scene_hit_cannot_be_assigned_to_an_old_cue(self):
        row = capture_row(1000)
        row["npc_action_events"] = [melee_cue(ms(1000))]
        row["player_hit_events"] = [
            player_hit(ms(1600), analysis.MELEE_DAMAGE_HIT_TYPE, 800, scene_epoch=SCENE_EPOCH + 1)
        ]
        self.assertEqual(
            analysis.summarize([row], isolated_phase_1_melee=True)["cue_hit_pairs"], []
        )

    def test_unlabelled_context_never_pairs_an_unidentified_hit_to_telos(self):
        row = capture_row(1000)
        row["npc_action_events"] = [melee_cue(ms(1000))]
        row["player_hit_events"] = [player_hit(ms(1500), 133, 1)]
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


class CatalogueBuildTests(unittest.TestCase):
    def test_the_same_capture_cannot_inflate_counts_twice(self):
        source = self.source()
        with self.assertRaises(ValueError):
            analysis.build_attack_catalogue([source, source])

    def source(self):
        row = capture_row(1000)
        row["npc_action_events"] = [melee_cue(ms(1000))]
        return {
            "capture_sha256": "b" * 64,
            "client_sha256": "a" * 64,
            "rows": [row],
            "intervals": [
                {
                    "session": [1, 2, 3],
                    "start_ns": ms(900),
                    "end_ns": ms(2000),
                    "phase": 1,
                    "enrage": 300,
                    "source": "user",
                }
            ],
            "labels": {
                "28929": {
                    "name": "auto",
                    "damage_style": "melee",
                    "required_protection": "melee",
                    "source": "user_labelled_native_capture",
                }
            },
        }

    def test_counts_are_event_counts_and_hit_times_never_become_protection_windows(self):
        source = self.source()
        source["rows"].append(copy.deepcopy(source["rows"][0]))
        catalogue = analysis.build_attack_catalogue([source])
        self.assertEqual(catalogue["attacks"][0]["sample_count"], 1)
        self.assertTrue(catalogue["attacks"][0]["classification_validated"])
        self.assertIsNone(catalogue["attacks"][0]["cue_to_prayer_check_ms"])
        self.assertFalse(catalogue["attacks"][0]["validated"])
        self.assertEqual(catalogue["coverage"][4]["status"], "not_captured")

    def test_ambiguous_phase_and_boundary_cues_remain_unassigned(self):
        for ambiguity in (True, False):
            source = self.source()
            if ambiguity:
                source["intervals"].append(copy.deepcopy(source["intervals"][0]))
            else:
                source["intervals"][0]["uncertainty_ns"] = ms(150)
            catalogue = analysis.build_attack_catalogue([source])
            self.assertEqual(catalogue["attacks"], [])
            self.assertEqual(catalogue["unassigned_sequences"], {"28929": 1})

    def test_guide_leads_and_cross_hash_data_cannot_validate(self):
        source = self.source()
        source["labels"]["28929"]["source"] = "guide"
        row = analysis.build_attack_catalogue([source])["attacks"][0]
        self.assertFalse(row["classification_validated"])
        self.assertEqual(row["evidence_kind"], "guide_lead")
        source["rows"][0]["executable_sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            analysis.build_attack_catalogue([source])

    def test_telemetry_normalization_does_not_guess_a_phase_from_raw_stage(self):
        row = capture_row(1000)
        row["npcs"] = {"entities": row.pop("animations")}
        row["encounter"] = {"stage_raw": 4}
        row["monotonic_ms"] = 1000
        row.pop("observed_ns")
        normalized = analysis.normalize_row(row)
        self.assertEqual(normalized["observed_ns"], ms(1000))
        self.assertNotIn("phase", normalized)


if __name__ == "__main__":
    unittest.main()
