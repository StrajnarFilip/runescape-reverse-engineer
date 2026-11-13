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

    def test_tendrils_release_cancels_pending_damage_response(self):
        phase = PhaseOne()
        phase.observe([event(1, TENDRILS)])
        timers = {"Hurricane": None}
        self.assertIsNone(phase.next_action(SECOND_NS, timers, 20, Prayer.MELEE))
        self.assertEqual(phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE).ability, "Hurricane")
        phase.observe([event(2, RELEASE)])
        self.assertIsNone(phase.next_action(2 * SECOND_NS, timers, 0, Prayer.MELEE))


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
        reader.active_prayer.side_effect = [Prayer.NONE, Prayer.MELEE, Prayer.MELEE, Prayer.MELEE]
        clock = 10 * SECOND_NS
        reader.monotonic_ns.return_value = clock
        info = SimpleNamespace(
            pid=1, process_creation_time=2, monotonic_ns=clock, status=Status.AVAILABLE, sequence=1
        )
        player = Mock(clock_stalled_ns=0, gcd_reason=Reason.NONE, gcd_remaining_ms=0)
        player.variable.side_effect = {Variable.PRAYER_PACKED: 990}.__getitem__
        dormant = SimpleNamespace(config_id=22892)
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
            (info, player, active, 1, 9900),
            (info, player, phase_two, 2, 9000),
        ]
        args = SimpleNamespace(
            duration=30,
            freedom="U",
            anticipation="A",
            hurricane="8",
            surge=None,
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
        self.assertEqual(mock_globals["log"].call_args.args[0], "phase_two_observed")
        board.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
