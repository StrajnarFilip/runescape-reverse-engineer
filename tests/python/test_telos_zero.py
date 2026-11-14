"""Exercise the standalone controller with synthetic attack events and no HID input."""

import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rs3_reader.native import NPCFlags, Reason, Status, Variable
from rs3_reader.prayer import Prayer

controller = runpy.run_path(str(Path(__file__).resolve().parents[2] / "work" / "telos_zero.py"))
PhaseOne = controller["PhaseOne"]
AUTO = controller["MELEE_AUTO_SEQUENCE"]
TENDRILS = controller["TENDRILS"]
RELEASE = controller["TENDRILS_RELEASE"]
UPPERCUT = controller["UPPERCUT"]
HOLD_STILL = controller["HOLD_STILL"]
SECOND_NS = 1_000_000_000


def event(ordinal, animation):
    return dict(event_sequence=ordinal, action_sequence=animation, observed_ns=ordinal * SECOND_NS)


class PhaseOneTests(unittest.TestCase):
    def test_repeated_ids_count_once_per_application(self):
        phase = PhaseOne()
        autos = [event(ordinal, AUTO) for ordinal in range(1, 8)]
        phase.observe(autos)
        phase.observe(autos)
        self.assertEqual(phase.autos, 7)
        self.assertEqual(phase.expected, TENDRILS)
        phase.observe([event(8, TENDRILS), event(9, RELEASE), event(10, AUTO), event(11, AUTO)])
        self.assertEqual(phase.autos, 3)
        self.assertEqual(phase.expected, UPPERCUT)

    def test_hold_still_cue_preempts_unacknowledged_forecast(self):
        phase = PhaseOne()
        phase.observe([event(1, UPPERCUT), event(2, AUTO), event(3, AUTO)])
        timers = {"Anticipation": None, "Freedom": None}
        action = phase.next_action(3 * SECOND_NS, timers, 0, Prayer.MELEE)
        self.assertEqual(action.ability, "Anticipation")
        phase.sent(action, 3 * SECOND_NS)
        phase.observe([event(4, HOLD_STILL)])
        action = phase.next_action(4 * SECOND_NS, timers, 1000, Prayer.MELEE)
        self.assertEqual(action.ability, "Freedom")
        self.assertEqual(phase.cast_result, ("Anticipation", False))
        phase.sent(action, 4 * SECOND_NS)
        self.assertIsNone(phase.next_action(5 * SECOND_NS, {"Freedom": 16000}, 0, Prayer.NONE))
        self.assertEqual(phase.cast_result, ("Freedom", True))

    def test_movement_requires_cue_and_configured_cooldown_within_window(self):
        phase = PhaseOne()
        timers = {"Surge": None}
        phase.observe([event(1, UPPERCUT)])
        self.assertIsNone(phase.next_action(4 * SECOND_NS, timers, 0, Prayer.MELEE))
        self.assertIsNone(phase.next_action(5 * SECOND_NS, {}, 0, Prayer.MELEE))
        action = phase.next_action(5 * SECOND_NS, timers, 0, Prayer.MELEE)
        self.assertEqual(action.ability, "Surge")
        self.assertIsNone(phase.next_action(6 * SECOND_NS, timers, 0, Prayer.MELEE))

    def test_timed_freedom_waits_for_heavy_hit_and_gcd_then_expires(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=10)])
        timers = {"Freedom": 0}
        for tick, gcd in ((None, 0), (13, 0), (14, 20), (14, None), (18, 0)):
            self.assertIsNone(
                phase.next_action(
                    3 * SECOND_NS, timers, gcd, Prayer.MELEE, received_tick=tick, timed_freedom=True
                )
            )
        action = phase.next_action(
            3 * SECOND_NS, timers, 0, Prayer.MELEE, received_tick=14, timed_freedom=True
        )
        self.assertEqual(action.ability, "Freedom")

    def test_tendrils_release_cancels_pending_damage_response(self):
        phase = PhaseOne()
        phase.observe([event(1, TENDRILS)])
        timers = {"Hurricane": None}
        self.assertIsNone(phase.next_action(SECOND_NS, timers, 20, Prayer.MELEE))
        self.assertEqual(phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE).ability, "Hurricane")
        phase.observe([event(2, RELEASE)])
        self.assertIsNone(phase.next_action(2 * SECOND_NS, timers, 0, Prayer.MELEE))

    def test_uppercut_escape_uses_received_ticks_once(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, UPPERCUT), received_tick=100)])
        timers = {"Escape": 0}
        for tick in (102, 103, 106):
            self.assertIsNone(
                phase.next_action(SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, received_tick=tick)
            )
        self.assertIsNone(
            phase.next_action(SECOND_NS, {"Escape": 5000}, 0, Prayer.SOUL_SPLIT, received_tick=104)
        )
        action = phase.next_action(SECOND_NS, timers, 1000, Prayer.SOUL_SPLIT, received_tick=104)
        self.assertEqual(action, controller["Action"]("Escape", "Uppercut escape"))
        phase.sent(action, SECOND_NS)
        phase.next_action(SECOND_NS + 1, {"Escape": 9000}, 0, Prayer.SOUL_SPLIT, received_tick=105)
        self.assertEqual(phase.cast_result, ("Escape", True))
        self.assertIsNone(
            phase.next_action(SECOND_NS + 2, timers, 0, Prayer.SOUL_SPLIT, received_tick=105)
        )

    def test_uppercut_escape_falls_back_to_elapsed_time(self):
        phase = PhaseOne()
        phase.observe([event(1, UPPERCUT)])
        timers = {"Escape": 0}
        self.assertIsNone(phase.next_action(3 * SECOND_NS, timers, 0, Prayer.MELEE))
        action = phase.next_action(3_500_000_000, timers, 0, Prayer.MELEE)
        self.assertEqual(action.ability, "Escape")

    def test_stale_auto_count_cannot_trigger_anticipation(self):
        phase = PhaseOne()
        phase.observe([event(1, UPPERCUT), event(2, AUTO), event(3, AUTO)])
        self.assertIsNone(phase.next_action(6 * SECOND_NS, {"Anticipation": None}, 0, Prayer.MELEE))

    def test_hold_still_surge_keeps_its_acknowledgement_window(self):
        phase = PhaseOne()
        phase.observe([event(1, HOLD_STILL)])
        now = 4 * SECOND_NS
        action = phase.next_action(now, {"Surge": 0}, 0, Prayer.MELEE)
        self.assertEqual(action.ability, "Surge")
        phase.sent(action, now)
        self.assertIsNone(phase.next_action(now + 1, {"Surge": 0}, 0, Prayer.MELEE))
        self.assertIsNone(phase.cast_result)
        phase.next_action(now + 2, {"Surge": 10000}, 0, Prayer.MELEE)
        self.assertEqual(phase.cast_result, ("Surge", True))


MeleeFlicker = controller["MeleeFlicker"]


class MeleeFlickerTests(unittest.TestCase):
    def test_animation_holds_melee_until_next_received_tick(self):
        flicker = MeleeFlicker()
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, 10, 0), Prayer.SOUL_SPLIT)
        flicker.sent(Prayer.SOUL_SPLIT, 0)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 1))
        self.assertEqual(flicker.observe([event(1, AUTO)], 10, 2)["event_sequence"], 1)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 3), Prayer.MELEE)
        flicker.sent(Prayer.MELEE, 3)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 4))
        self.assertIsNone(flicker.next_prayer(Prayer.MELEE, 10, 5))
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, 11, 6), Prayer.SOUL_SPLIT)

    def test_uppercut_and_repeated_events_do_not_flick(self):
        flicker = MeleeFlicker()
        self.assertIsNone(flicker.observe([event(1, UPPERCUT)], 10, 0))
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 0))
        flicker.observe([event(2, HOLD_STILL)], 10, 0)
        self.assertIsNone(flicker.observe([event(2, HOLD_STILL)], 11, 0))
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 0), Prayer.MELEE)

    def test_invalid_tick_falls_back_to_one_tick_duration(self):
        flicker = MeleeFlicker()
        fallback = controller["TICK_FALLBACK_NS"]
        flicker.observe([event(1, TENDRILS)], None, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, None, fallback - 1), Prayer.MELEE)
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, None, fallback), Prayer.SOUL_SPLIT)

    def test_unacknowledged_toggle_is_not_repeated_before_timeout(self):
        flicker = MeleeFlicker()
        timeout = controller["ACK_TIMEOUT_NS"]
        flicker.observe([event(1, AUTO)], 10, 0)
        flicker.sent(Prayer.MELEE, 0)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 11, timeout - 1))
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 11, timeout))


class PracticeRunTests(unittest.TestCase):
    def test_phase_and_font_candidates_require_matching_health(self):
        checkpoint = controller["encounter_checkpoint"]
        health = {"current": 300_000, "max": 400_000}
        self.assertEqual(checkpoint(1, health)["phase"], 1)
        self.assertEqual(checkpoint(2, health)["phase"], 2)
        self.assertIsNone(checkpoint(3, health))
        self.assertEqual(checkpoint(3, {**health, "current": 150_000})["phase"], 3)
        for current, count in [(75_001, 0), (75_000, 1), (50_000, 2), (25_000, 3)]:
            candidate = checkpoint(4, {**health, "current": current})
            self.assertEqual(candidate["font_thresholds_reached"], count)
            self.assertFalse(candidate["zero_hp"])
        self.assertTrue(checkpoint(4, {**health, "current": 0})["zero_hp"])
        self.assertIsNone(checkpoint(4, {"current": 25_000, "max": None}))
        self.assertIsNone(checkpoint(5, {**health, "current": 0}))

    def test_observation_replay_retains_transition_and_later_animation(self):
        replay = controller["replay"]
        boss_id = controller["ACTIVE_TELOS"]
        action = dict(event(1, 28930), instance_id=4, config_id=boss_id)
        row = dict(
            event="observation",
            session=[1, 2, 3],
            boss_config_id=boss_id,
            boss_instance_id=4,
            phase_candidate=2,
            sample_ns=SECOND_NS,
            hp=2000,
            npc_actions=[action],
            npc_action_events_lost=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.jsonl"
            capture.write_text(json.dumps(row), encoding="utf-8")
            logger = Mock()
            with patch.dict(replay.__globals__, {"log": logger}):
                replay(capture)
        self.assertEqual(
            [call.args[0] for call in logger.call_args_list],
            [
                "phase_candidate",
                "later_phase_cues",
            ],
        )
        self.assertEqual(logger.call_args.kwargs["actions"], [action])
        self.assertFalse(logger.call_args.kwargs["protected_melee_cue"])

    def test_phase_two_melee_cue_requires_current_protection(self):
        cue = controller["phase_two_melee_cue"]
        self.assertTrue(cue([event(1, AUTO)], "melee"))
        self.assertFalse(cue([event(1, AUTO)], "none"))
        self.assertFalse(cue([event(1, 28930)], "melee"))
        self.assertFalse(cue([], "melee"))

    def test_input_waits_for_baseline_prayer_and_stops_at_phase_two(self):
        run = controller["run"]
        reader = Mock()
        board = Mock()
        keys = Mock()
        keys.get_async_key_state.return_value = 0
        reader.combat.scene_epoch = 1
        reader.position.valid = False
        reader.abilities = []
        reader.drain_npc_action_events.return_value = ([], 0)
        reader.drain_player_hit_events.return_value = ([], 0)
        reader.active_prayer.side_effect = [Prayer.NONE, Prayer.NONE] + [Prayer.MELEE] * 4
        clock = 10 * SECOND_NS
        reader.monotonic_ns.return_value = clock
        info = SimpleNamespace(
            pid=1, process_creation_time=2, monotonic_ns=clock, status=Status.AVAILABLE, sequence=1
        )
        player = Mock(clock_stalled_ns=0, gcd_reason=Reason.NONE, gcd_remaining_ms=0)
        player.variable.side_effect = {Variable.PRAYER_PACKED: 990}.__getitem__
        dormant = SimpleNamespace(config_id=22892, instance_id=3)
        active = SimpleNamespace(
            config_id=22891,
            instance_id=4,
            flags=NPCFlags.HEALTH,
            health_max=400_000,
            health_current=400_000,
        )
        phase_two = SimpleNamespace(**vars(active))
        phase_two.health_current = 300_000
        samples = [
            (info, player, dormant, None, 9900),
            (info, player, dormant, None, 9900),
            (info, player, dormant, None, 9900),
            (info, player, dormant, None, 9900),
            (info, player, dormant, None, 9900),
            (info, player, active, 1, 9900),
            (info, player, phase_two, 2, 9000),
        ]
        args = SimpleNamespace(
            duration=30,
            freedom="U",
            anticipation="A",
            hurricane="8",
            surge=None,
            reacquire=None,
            continue_phase_two=False,
            frame=None,
            melee=(10, 20),
            approach=(30, 40),
            observe_transition=False,
        )
        game = Mock()
        game.cursor.return_value = (10, 20)
        console = SimpleNamespace(
            GameWindow=Mock(return_value=game),
            frame_target=Mock(side_effect=lambda _, __, x, y: (game.bounds(), (x, y))),
            move_pointer=Mock(),
        )
        mock_globals = {
            "ManualPrayerKeys": Mock(return_value=keys),
            "boss_sample": Mock(side_effect=samples),
            "player_hit_splats_record": Mock(return_value={}),
            "log": Mock(),
        }
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                self.assertEqual(run(reader, args, board), 2)
        self.assertEqual(
            [call.args[-1] for call in console.move_pointer.call_args_list],
            [args.melee, args.approach],
        )
        self.assertEqual([call.args[0] for call in board.command.call_args_list], ["CLICK L"] * 2)
        self.assertEqual(mock_globals["log"].call_args.args[0], "observation")
        board.tap.assert_not_called()

        board.reset_mock()
        reader.active_prayer.side_effect = [Prayer.MELEE, Prayer.NONE]
        mock_globals["boss_sample"].side_effect = samples[:3]
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                with self.assertRaisesRegex(RuntimeError, "Deflect Melee changed"):
                    run(reader, args, board)
        board.command.assert_not_called()

        args.reacquire = ("F6", "Q")
        reader.active_prayer.side_effect = [Prayer.MELEE] * 5
        damaged_boss = SimpleNamespace(**vars(phase_two))
        damaged_boss.health_current -= 500
        mock_globals["boss_sample"].side_effect = (
            samples[:3]
            + samples[-2:]
            + [
                (info, player, damaged_boss, 2, 9000),
            ]
        )
        mock_globals["telos_actions"] = Mock(side_effect=[[], [{"action_sequence": AUTO}], []])
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                self.assertEqual(run(reader, args, board), 2)
        self.assertEqual([call.args[0][-1].name for call in board.tap.call_args_list], ["F6", "Q"])
        self.assertEqual(mock_globals["log"].call_args.args[0], "phase_two_reacquire_result")
        self.assertTrue(mock_globals["log"].call_args.kwargs["damage_observed"])
        self.assertTrue(
            any(
                call.args[0] == "observation"
                and call.kwargs["npc_actions"] == [{"action_sequence": AUTO}]
                for call in mock_globals["log"].call_args_list
            )
        )

        board.reset_mock()
        args.reacquire = None
        args.continue_phase_two = True
        reader.active_prayer.side_effect = [Prayer.MELEE] * 6
        phase_three = SimpleNamespace(**vars(phase_two))
        phase_three.health_current = 200_000
        mock_globals["boss_sample"].side_effect = (
            samples[:3]
            + samples[-2:]
            + [
                (info, player, damaged_boss, 2, 9000),
                (info, player, phase_three, 3, 9000),
            ]
        )
        mock_globals["telos_actions"].side_effect = [[], [], [event(1, HOLD_STILL)], []]
        mock_globals["cooldowns"] = Mock(return_value={"Freedom": 0})
        reader.monotonic_ns.return_value = SECOND_NS
        info.monotonic_ns = SECOND_NS
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                self.assertEqual(run(reader, args, board), 2)
        self.assertEqual([call.args[0][-1].name for call in board.tap.call_args_list], ["U"])
        self.assertEqual(mock_globals["log"].call_args.args[0], "phase_three_observed")

        board.reset_mock()
        args.continue_phase_two = False
        args.flick_melee = True
        reader.encounter.game_tick = SimpleNamespace(valid=True, counter=50)
        reader.monotonic_ns.return_value = clock
        info.monotonic_ns = clock
        reader.active_prayer.side_effect = (
            [Prayer.NONE, Prayer.NONE] + [Prayer.MELEE] * 3 + [Prayer.SOUL_SPLIT] * 3
        )
        mock_globals["boss_sample"].side_effect = samples
        mock_globals["telos_actions"].side_effect = [[event(1, AUTO)], []]
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                self.assertEqual(run(reader, args, board), 2)
        self.assertEqual(
            [[key.name for key in call.args[0]] for call in board.tap.call_args_list],
            [["F3"]],
        )
        flick_events = [
            call.args[0]
            for call in mock_globals["log"].call_args_list
            if call.args[0].startswith("prayer_flick")
        ]
        self.assertEqual(flick_events, ["prayer_flick_cue", "prayer_flick_sent"])


if __name__ == "__main__":
    unittest.main()
