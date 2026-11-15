"""Exercise the standalone controller with synthetic attack events and no HID input."""

import io
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rs3_reader.bot.attempt_log import AttemptLog
from rs3_reader.native import (
    EncounterState,
    HazardFlags,
    NPCActionEvent,
    NPCFlags,
    Reason,
    SpatialFlags,
    Status,
    TelosCleanse,
    Variable,
)
from rs3_reader.prayer import Prayer
from rs3_reader.telos_prayer import telos_actions

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
    def test_uppercut_seen_during_beam_click_still_gets_escape_and_owns_movement(self):
        # P3 Uppercut was sampled at frame 4962 during a beam click and drained at 4963.
        marker = NPCActionEvent(
            sequence=201,
            observed_ns=166_125_031_800,
            sample_sequence=4962,
            scene_epoch=3,
            process_creation_time=2,
            instance_id=1,
            pid=1,
            action_sequence=UPPERCUT,
        )
        session = (1, 2, 3)
        self.assertEqual(telos_actions([marker], session, 4963, {1}), [])
        actions = telos_actions(
            [marker], session, 4963, {1}, additional_samples={4962: None}
        )
        phase = PhaseOne(phase=3)
        phase.observe(actions)
        self.assertEqual(phase.expected, HOLD_STILL)
        now = marker.observed_ns + 2_500_000_000
        self.assertTrue(phase.owns_movement(now, 560))
        self.assertEqual(
            phase.next_action(now, {"Escape": 0}, 0, Prayer.SOUL_SPLIT, received_tick=560),
            controller["Action"]("Escape", "Uppercut escape"),
        )
        self.assertFalse(phase.owns_movement(marker.observed_ns + 7 * SECOND_NS, 568))

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
        # Hold Still alone never triggers Freedom; Anticipation is the planned answer.
        self.assertIsNone(phase.next_action(4 * SECOND_NS, timers, 1000, Prayer.MELEE))
        self.assertEqual(phase.cast_result, ("Anticipation", False))
        # Freedom is on the GCD and the client drops presses made while it runs.
        self.assertIsNone(
            phase.next_action(4 * SECOND_NS, timers, 1000, Prayer.MELEE, stun_expiry=900)
        )
        action = phase.next_action(4 * SECOND_NS, timers, 80, Prayer.MELEE, stun_expiry=900)
        self.assertEqual(action, controller["Action"]("Freedom", "Player stunned"))
        phase.sent(action, 4 * SECOND_NS)
        self.assertIsNone(phase.next_action(5 * SECOND_NS, {"Freedom": 16000}, 0, Prayer.NONE))
        self.assertEqual(phase.cast_result, ("Freedom", True))

    def test_uppercut_dodge_choice_projects_escape_and_surge_on_the_known_floor(self):
        choose = controller["uppercut_dodge_choice"]
        # 07:35 P3: player (10129,844) attacking Telos at (10131,844); the floor seen began at
        # y 842. Escape went south-west and stopped at (10127,842), two tiles: hit for 7,750.
        bounds = (10122, 842, 10160, 880)
        facing = (10133 - 10129, 846 - 844)
        ability, escape, surge = choose((10129, 844), (10131, 844), 5, facing, bounds)
        self.assertEqual(escape, (10127, 842))
        self.assertEqual(ability, "Surge")
        self.assertEqual(surge, (10139, 854))
        # With room behind, Escape is kept.
        open_floor = (10100, 820, 10160, 880)
        self.assertEqual(choose((10129, 844), (10131, 844), 5, facing, open_floor)[0], "Escape")
        # A Surge that would stop under Telos does not count as clearing.
        cramped = (10127, 842, 10133, 846)
        self.assertEqual(choose((10129, 844), (10131, 844), 5, facing, cramped)[0], "Escape")
        # Unknown heading or floor: no choice.
        self.assertIsNone(choose((10129, 844), (10131, 844), 5, None, bounds))
        self.assertIsNone(choose((10129, 844), (10131, 844), 5, facing, None))
        landing = controller["ray_landing"]
        self.assertEqual(landing((5, 5), (1, 0), 7, (0, 0, 8, 10)), (8, 5))
        self.assertEqual(landing((5, 5), (3, -1), 3, (0, 0, 20, 20)), (8, 5))
        self.assertIsNone(landing((5, 5), (0, 0), 7, (0, 0, 8, 10)))

    def test_uppercut_uses_one_jump_chosen_by_space(self):
        timers = {"Escape": 0, "Surge": 0}
        for choice, expected in (("Surge", "Surge"), ("Escape", "Escape"), (None, "Escape")):
            phase = PhaseOne(phase=3)
            phase.observe([dict(event(1, UPPERCUT), received_tick=15150)])
            self.assertIsNone(
                phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE, received_tick=15153,
                                  uppercut_choice=choice)
            )
            action = phase.next_action(
                SECOND_NS, timers, 0, Prayer.MELEE, received_tick=15154, uppercut_choice=choice
            )
            self.assertEqual(action.ability, expected)
            phase.sent(action, SECOND_NS)
            # Never the other jump as well.
            cooling = {expected: 20_000, ({"Escape", "Surge"} - {expected}).pop(): 0}
            self.assertIsNone(
                phase.next_action(SECOND_NS + 1, cooling, 0, Prayer.MELEE, received_tick=15155,
                                  uppercut_choice="Surge" if expected == "Escape" else "Escape")
            )
        # The chosen jump cooling: the other one goes instead.
        phase = PhaseOne()
        phase.observe([dict(event(2, UPPERCUT), received_tick=200)])
        action = phase.next_action(
            SECOND_NS, {"Escape": 9_000, "Surge": 0}, 0, Prayer.MELEE, received_tick=204,
            uppercut_choice="Escape",
        )
        self.assertEqual(action, controller["Action"]("Surge", "Uppercut Surge"))
        # Hold Still no longer triggers the old Surge trial.
        still = PhaseOne()
        still.observe([event(3, HOLD_STILL)])
        self.assertIsNone(still.next_action(4 * SECOND_NS, {"Surge": 0}, 0, Prayer.MELEE))

    def test_freedom_only_while_stunned_and_once_per_stun(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=10)])
        timers = {"Freedom": 0}
        for tick in (11, 14, 18):
            self.assertIsNone(
                phase.next_action(3 * SECOND_NS, timers, 0, Prayer.MELEE, received_tick=tick)
            )
        self.assertIsNone(
            phase.next_action(3 * SECOND_NS, {"Freedom": 5000}, 0, Prayer.MELEE, stun_expiry=700)
        )
        action = phase.next_action(3 * SECOND_NS, timers, 0, Prayer.MELEE, stun_expiry=700)
        self.assertEqual(action.ability, "Freedom")
        self.assertIsNone(
            phase.next_action(3 * SECOND_NS, timers, 0, Prayer.MELEE, stun_expiry=700)
        )
        # A new stun (a new expiry) allows Freedom again.
        self.assertEqual(
            phase.next_action(4 * SECOND_NS, timers, 0, Prayer.MELEE, stun_expiry=950).ability,
            "Freedom",
        )

    def test_active_stun_expiry_reads_the_stun_timer_ahead_of_the_logic_tick(self):
        stun_effect = SimpleNamespace(effect_id=6, raw_valid_mask=1, raw_expiry=1_000)
        reader = SimpleNamespace(effects=[SimpleNamespace(effect_id=i) for i in range(1, 6)])
        reader.effects.append(stun_effect)
        active = controller["active_stun_expiry"]
        self.assertEqual(active(reader, 900), 1_000)
        self.assertIsNone(active(reader, 1_000))
        self.assertIsNone(active(reader, 1_200))
        self.assertIsNone(active(reader, None))
        stun_effect.raw_valid_mask = 0
        self.assertIsNone(active(reader, 900))

    def test_footprint_and_stomp_escape_tiles(self):
        under = controller["under_footprint"]
        escapes = controller["stomp_escape_tiles"]
        boss = (100, 200)
        self.assertTrue(under(boss, 5, (100, 200)))
        self.assertTrue(under(boss, 5, (104, 204)))
        self.assertFalse(under(boss, 5, (105, 202)))
        self.assertFalse(under(boss, 5, (99, 202)))
        stomp = controller["in_stomp_area"]
        # 500% capture: Telos SW (14555,1486), player (14561,1484), 4 tiles from centre: hit.
        self.assertFalse(under((14555, 1486), 5, (14561, 1484)))
        self.assertTrue(stomp((14555, 1486), 5, (14561, 1484)))
        # 07:25 at 250%: a landing 5 tiles from the centre still took 5,647; 6 is clear.
        self.assertTrue(stomp((15892, 714), 5, (15899, 716)))
        self.assertFalse(stomp((14555, 1486), 5, (14563, 1484)))
        # Player on the east side (centre 102,202): continue east first, 6 tiles out.
        tiles = escapes(boss, 5, (104, 202))
        self.assertEqual(tiles[0], (108, 202))
        self.assertEqual(len(set(tiles)), 8)
        for tile in tiles:
            self.assertFalse(stomp(boss, 5, tile))
        # North-west offset continues north-west.
        self.assertEqual(escapes(boss, 5, (100, 204))[0], (96, 208))

    def test_dive_targets_only_known_floor_outside_the_stomp(self):
        targets = controller["known_floor_targets"]
        boss, player = (100, 200), (106, 202)  # Centre (102,202); player 4 tiles east.
        floor = {(106, 202), (107, 202), (108, 202), (96, 202), (102, 213), (100, 200)}
        # (106,202) and (107,202) are inside the stomp area, (102,213) is beyond Dive reach.
        self.assertEqual(targets(boss, 5, player, floor), [(108, 202), (96, 202)])
        self.assertEqual(targets(boss, 5, player, {(100, 200), (104, 204)}), [])

    def test_phase_three_opening_after_tendrils_forecasts_hold_still(self):
        phase = PhaseOne(phase=3, carried_expected=controller["ONSLAUGHT"])
        self.assertEqual(phase.expected, HOLD_STILL)
        magic = controller["MAGIC_ATTACK"]
        phase.observe([event(1, controller["P2_OPENING_ATTACK"])])
        phase.observe([event(index, magic) for index in range(2, 7)])
        timers = {"Anticipation": 0}
        self.assertIsNone(
            phase.next_action(6 * SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, flicking=True)
        )
        phase.observe([event(7, magic)])
        action = phase.next_action(7 * SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, flicking=True)
        self.assertEqual(action.ability, "Anticipation")

    def test_tendrils_release_cancels_pending_damage_response(self):
        phase = PhaseOne()
        phase.observe([event(1, TENDRILS)])
        timers = {"Hurricane": None}
        early = controller["GCD_PRESS_LEAD_MS"] + 20
        self.assertIsNone(phase.next_action(SECOND_NS, timers, early, Prayer.MELEE))
        self.assertEqual(phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE).ability, "Hurricane")
        phase.observe([event(2, RELEASE)])
        self.assertIsNone(phase.next_action(2 * SECOND_NS, timers, 0, Prayer.MELEE))

    def test_anticipation_waits_for_the_gcd_end_and_retries_once(self):
        phase = PhaseOne()
        phase.observe([event(1, UPPERCUT), event(2, AUTO), event(3, AUTO)])
        timers = {"Anticipation": 0}
        now = 3 * SECOND_NS
        timeout = controller["ACK_TIMEOUT_NS"]
        # 2026-11-15: presses with 1,760-1,780 ms left never cast, 360-600 ms always did.
        lead = controller["GCD_PRESS_LEAD_MS"]
        for gcd in (1780, 600, lead + 1, None):
            self.assertIsNone(
                phase.next_action(now, timers, gcd, Prayer.SOUL_SPLIT, flicking=True)
            )
        action = phase.next_action(now, timers, lead, Prayer.SOUL_SPLIT, flicking=True)
        self.assertEqual(action.ability, "Anticipation")
        phase.sent(action, now)
        retry = phase.next_action(now + timeout, timers, 40, Prayer.SOUL_SPLIT, flicking=True)
        self.assertEqual(phase.cast_result, ("Anticipation", False))
        self.assertEqual(retry.ability, "Anticipation")
        phase.sent(retry, now + timeout)
        self.assertTrue(phase.forecast_handled)
        later = now + 2 * timeout - 1
        self.assertIsNone(phase.next_action(later, timers, 0, Prayer.SOUL_SPLIT, flicking=True))

    def test_unknown_equipment_match_is_accepted_only_when_bound(self):
        from rs3_reader.native import AbilityFlags, EquipmentMatch

        def ability(name, match, reason, flags=0):
            return SimpleNamespace(
                name=name.encode(),
                equipment_match=match,
                reason=reason,
                flags=flags,
                remaining_ms=0,
            )

        reader = SimpleNamespace(
            abilities=[
                ability("Resonance", EquipmentMatch.UNKNOWN, Reason.NOT_SET),
                ability("Barricade", EquipmentMatch.UNKNOWN, Reason.NOT_SET),
                ability("Escape", EquipmentMatch.ALLOWED, Reason.NONE, AbilityFlags.TIMER_VALID),
                ability("Surge", EquipmentMatch.BLOCKED, Reason.NOT_SET),
            ]
        )
        timers = controller["cooldowns"](reader, {"Resonance", "Surge"})
        self.assertEqual(timers, {"Resonance": None, "Escape": 0})

    @staticmethod
    def hit(type_id):
        return SimpleNamespace(hit=SimpleNamespace(type_id=type_id))

    def test_resonance_waits_for_the_gcd_end_after_the_small_hit(self):
        # 5743 at 250%: Resonance pressed at +0 with 620 ms left cast at +1, so the +2 small hit
        # used it and the heavy +4 hit dealt 2,083. Now nothing is pressed before the small hit.
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=5743)])
        timers = {"Resonance": 0}
        for tick, gcd in ((5743, 620), (5743, 80), (5744, 0), (5744, 60)):
            self.assertIsNone(
                phase.next_action(SECOND_NS, timers, gcd, Prayer.MELEE, received_tick=tick)
            )
        phase.observe_hits([self.hit(133)], 5745)
        self.assertEqual(phase.small_hit_tick, 5745)
        # The GCD still runs for 540 ms: a press now would be dropped.
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, 540, Prayer.MELEE, received_tick=5745)
        )
        action = phase.next_action(SECOND_NS, timers, 90, Prayer.MELEE, received_tick=5745)
        self.assertEqual(action, controller["Action"]("Resonance", "Hold Still second melee hit"))

    def test_resonance_after_a_blocked_small_hit_at_plus_one(self):
        # 5629 at 250%: the small hit was a 0 (type 482) at +1.
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=5629)])
        timers = {"Resonance": 0}
        for type_id in (143, 144):
            phase.observe_hits([self.hit(type_id)], 5630)
        self.assertIsNone(phase.small_hit_tick)
        phase.observe_hits([self.hit(482)], 5630)
        action = phase.next_action(SECOND_NS, timers, 100, Prayer.MELEE, received_tick=5630)
        self.assertEqual(action.ability, "Resonance")

    def test_resonance_at_plus_three_needs_no_small_hit_and_is_pressed_once(self):
        phase = PhaseOne(phase=3)
        phase.observe([dict(event(1, HOLD_STILL), received_tick=50)])
        self.assertIsNone(
            phase.next_action(SECOND_NS, {"Resonance": 0}, 50, Prayer.MELEE, received_tick=52)
        )
        self.assertIsNone(
            phase.next_action(SECOND_NS, {"Resonance": 3000}, 50, Prayer.MELEE, received_tick=53)
        )
        timers = {"Resonance": None}
        self.assertTrue(phase.resonance_press_possible(timers, 53))
        action = phase.next_action(SECOND_NS, timers, 50, Prayer.MELEE, received_tick=53)
        self.assertEqual(action.ability, "Resonance")
        phase.sent(action, SECOND_NS)
        self.assertFalse(phase.resonance_press_possible(timers, 53))
        self.assertTrue(phase.resonance_committed())
        self.assertIsNone(
            phase.next_action(SECOND_NS + 20_000_000, timers, 0, Prayer.MELEE, received_tick=53)
        )
        cooling = {"Resonance": 30_000}
        self.assertIsNone(
            phase.next_action(
                SECOND_NS + 600_000_000, cooling, 1780, Prayer.MELEE, received_tick=54
            )
        )
        self.assertEqual(phase.cast_result, ("Resonance", True))
        # Too late after +3: the cast would follow the heavy hit.
        late = PhaseOne()
        late.observe([dict(event(1, HOLD_STILL), received_tick=50)])
        self.assertFalse(late.resonance_press_possible({"Resonance": 0}, 54))
        self.assertIsNone(
            late.next_action(SECOND_NS, {"Resonance": 0}, 0, Prayer.MELEE, received_tick=54)
        )

    def test_resonance_takes_the_gcd_slot_before_freedom(self):
        # 06:30 P3 Hold Still at 8728: Freedom and Resonance both pressed at +3; Resonance failed.
        phase = PhaseOne(phase=3)
        phase.observe([dict(event(1, HOLD_STILL), received_tick=8728)])
        timers = {"Resonance": 0, "Freedom": 0}
        action = phase.next_action(
            SECOND_NS, timers, 90, Prayer.MELEE, received_tick=8731, stun_expiry=264200
        )
        self.assertEqual(action.ability, "Resonance")
        phase.sent(action, SECOND_NS)
        # Once Resonance has been pressed, the stun gets Freedom on the next GCD slot.
        action = phase.next_action(
            2 * SECOND_NS,
            {"Resonance": 30_000, "Freedom": 0},
            90,
            Prayer.MELEE,
            received_tick=8734,
            stun_expiry=264200,
        )
        self.assertEqual(action.ability, "Freedom")

    def test_soul_split_returns_for_the_heavy_hit_from_the_start_of_the_resonance_tick(self):
        # 06:51 P1: Resonance pressed at +2 was confirmed at +3, so Soul Split came a tick late.
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=10543)])
        timers = {"Resonance": 0}
        phase.observe_hits([self.hit(133)], 10545)
        # 40 ms into +2 the GCD has 560 ms left: it ends at +3, so Resonance goes this tick.
        self.assertFalse(phase.resonance_due(timers, 10545, 560))
        self.assertTrue(phase.soul_split_for_heavy_hit(timers, 10545, 560, 40))
        # With the GCD ending a tick later Resonance is not due here and Deflect stays.
        self.assertFalse(phase.soul_split_for_heavy_hit(timers, 10545, 1160, 40))
        # 07:01 P4: 780 ms left 400 ms into the tick ended two ticks later; no early release.
        self.assertFalse(phase.soul_split_for_heavy_hit(timers, 10545, 780, 400))
        # Without the tick phase only a press that is due now releases.
        self.assertFalse(phase.soul_split_for_heavy_hit(timers, 10545, 560))
        self.assertTrue(phase.soul_split_for_heavy_hit(timers, 10545, 150))
        action = phase.next_action(SECOND_NS, timers, 190, Prayer.MELEE, received_tick=10545)
        phase.sent(action, SECOND_NS)
        self.assertTrue(phase.soul_split_for_heavy_hit({"Resonance": 30_000}, 10546, 1700))
        # A press that produced no cooldown keeps Deflect Melee for the heavy hit.
        phase.next_action(SECOND_NS + controller["ACK_TIMEOUT_NS"], timers, 1700, Prayer.MELEE,
                          received_tick=10546)
        self.assertEqual(phase.cast_result, ("Resonance", False))
        self.assertFalse(phase.soul_split_for_heavy_hit(timers, 10546, 1700))

    def test_devotion_covers_the_heavy_hit_when_resonance_cannot(self):
        enough = controller["DEVOTION_ADRENALINE_TENTHS"]
        # Resonance cooling: Devotion takes the first slot through +3, with enough adrenaline.
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=100)])
        cooling = {"Resonance": 12_000, "Devotion": 0}
        self.assertIsNone(
            phase.next_action(SECOND_NS, cooling, 0, Prayer.MELEE, received_tick=100,
                              adrenaline_tenths=enough - 10)
        )
        action = phase.next_action(SECOND_NS, cooling, 0, Prayer.MELEE, received_tick=100,
                                   adrenaline_tenths=enough)
        self.assertEqual(action.ability, "Devotion")
        phase.sent(action, SECOND_NS)
        self.assertFalse(phase.devotion_press_possible(cooling, 101, enough))
        # Resonance ready: +0 is left for the +3 Resonance slot, but a +1 slot before the small
        # hit is the last one in time, so Devotion takes it.
        ready = {"Resonance": 0, "Devotion": 0}
        second = PhaseOne()
        second.observe([dict(event(2, HOLD_STILL), received_tick=200)])
        self.assertIsNone(
            second.next_action(SECOND_NS, ready, 0, Prayer.MELEE, received_tick=200,
                               adrenaline_tenths=enough)
        )
        self.assertEqual(
            second.next_action(SECOND_NS, ready, 0, Prayer.MELEE, received_tick=201,
                               adrenaline_tenths=enough).ability,
            "Devotion",
        )
        # Too late after +3, and never on top of a pressed Resonance.
        late = PhaseOne()
        late.observe([dict(event(3, HOLD_STILL), received_tick=300)])
        self.assertFalse(late.devotion_press_possible(cooling, 304, enough))
        late.sent(controller["Action"]("Resonance", "Hold Still second melee hit"), SECOND_NS)
        self.assertFalse(late.devotion_press_possible(cooling, 301, enough))

    def test_dive_cooldown_comes_from_the_shared_bladed_dive_timer(self):
        from rs3_reader.native import AbilityFlags, EquipmentMatch

        def ability(name, match, reason, flags=0, remaining=0):
            return SimpleNamespace(
                name=name.encode(), equipment_match=match, reason=reason, flags=flags,
                remaining_ms=remaining,
            )

        valid = AbilityFlags.TIMER_VALID
        reader = SimpleNamespace(
            abilities=[
                ability("Dive", EquipmentMatch.ALLOWED, Reason.NOT_SET),
                ability("Bladed Dive", EquipmentMatch.BLOCKED, Reason.NONE, valid, 14_000),
            ]
        )
        timers = controller["cooldowns"](reader, {"Dive"})
        self.assertEqual(timers, {"Dive": 14_000})
        reader.abilities[1].remaining_ms = 0
        self.assertEqual(controller["cooldowns"](reader, {"Dive"}), {"Dive": 0})

    def test_anima_bomb_defence_casts_ahead_of_the_typeless_hit(self):
        # 06:34 at 250%: 28942 at 9367 and a 5,228 typeless hit at 9370.
        bomb = controller["ANIMA_BOMB"]
        phase = PhaseOne(phase=4)
        phase.observe([dict(event(1, bomb), received_tick=9367)])
        self.assertIsNone(phase.special)
        timers = {"Resonance": 0, "Reflect": 0}
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE, received_tick=9367)
        )
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, 400, Prayer.MELEE, received_tick=9368)
        )
        action = phase.next_action(SECOND_NS, timers, 150, Prayer.MELEE, received_tick=9368)
        self.assertEqual(action, controller["Action"]("Resonance", "Anima bomb"))
        phase.sent(action, SECOND_NS)
        self.assertNotIn("Resonance", phase.handled)
        self.assertIsNone(
            phase.next_action(SECOND_NS, {"Reflect": 0}, 0, Prayer.MELEE, received_tick=9369)
        )
        # With Resonance cooling (the font instakill restarts every cooldown), Reflect at +2.
        later = PhaseOne(phase=4)
        later.observe([dict(event(2, bomb), received_tick=200)])
        cooling = {"Resonance": 12_000, "Reflect": 0}
        self.assertEqual(
            later.next_action(SECOND_NS, cooling, 0, Prayer.MELEE, received_tick=202).ability,
            "Reflect",
        )
        self.assertIsNone(
            later.next_action(SECOND_NS, cooling, 0, Prayer.MELEE, received_tick=203)
        )
        # Outside P4 28942 is not treated as the bomb.
        first = PhaseOne()
        first.observe([dict(event(3, bomb), received_tick=50)])
        self.assertIsNone(first.next_action(SECOND_NS, timers, 0, Prayer.MELEE, received_tick=51))

    def test_super_restore_at_fifty_prayer_points_waits_for_each_dose(self):
        restorer = controller["PrayerRestorer"]()
        retry = controller["SUPER_RESTORE_RETRY_NS"]
        self.assertFalse(restorer.due(None, 0))
        self.assertFalse(restorer.due(501, 0))
        self.assertTrue(restorer.due(300, 0))
        restorer.sent(300, 0)
        # Still draining before the dose lands: no second dose yet.
        self.assertFalse(restorer.due(280, retry - 1))
        # The dose landed but points are still at most 50: drink again.
        self.assertTrue(restorer.due(450, 1))
        restorer.sent(450, 1)
        # A dose that never landed is retried after the timeout.
        self.assertTrue(restorer.due(430, 1 + retry))
        # Above the threshold the restorer resets.
        self.assertFalse(restorer.due(2400, 2 + retry))
        self.assertTrue(restorer.due(500, 3 + retry))

    def test_gcd_remaining_now_counts_down_to_the_press(self):
        remaining = controller["gcd_remaining_now"]
        player = SimpleNamespace(gcd_reason=Reason.NONE, gcd_remaining_ms=160)
        self.assertEqual(remaining(player, 0, 70_000_000), 90)
        self.assertIsNone(remaining(player, 0, 170_000_000))
        player.gcd_remaining_ms = 0
        self.assertEqual(remaining(player, 0, 500_000_000), 0)
        player.gcd_reason = Reason.NOT_SET
        self.assertIsNone(remaining(player, 0, 0))
        ready = controller["gcd_press_ready"]
        lead = controller["GCD_PRESS_LEAD_MS"]
        self.assertEqual(
            [ready(value) for value in (None, 0, lead, lead + 1)], [False, True, True, False]
        )

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

    def test_phase_two_anticipates_hold_still_after_onslaught_not_uppercut(self):
        onslaught = controller["ONSLAUGHT"]
        phase = PhaseOne(phase=2, carried_expected=UPPERCUT)
        self.assertEqual(phase.expected, onslaught)
        phase.observe([event(1, onslaught), event(2, onslaught), event(3, AUTO), event(4, AUTO)])
        self.assertEqual(phase.expected, HOLD_STILL)
        # 28959 is also a lone magic attack, so P2 waits for the third auto.
        self.assertIsNone(
            phase.next_action(
                4 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
            )
        )
        phase.observe([event(5, AUTO)])
        action = phase.next_action(
            5 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
        )
        self.assertEqual(action.ability, "Anticipation")
        # 500% capture: lone 28959, two autos, a five-projectile Onslaught, three autos, Hold Still.
        phase = PhaseOne(phase=2, carried_expected=TENDRILS)
        phase.observe([event(10, controller["P2_OPENING_ATTACK"]), event(11, onslaught)])
        phase.observe([event(12, AUTO), event(13, AUTO)])
        self.assertIsNone(
            phase.next_action(
                13 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
            )
        )
        phase.observe([event(14 + index, onslaught) for index in range(5)])
        phase.observe([event(19, AUTO), event(20, AUTO), event(21, AUTO)])
        action = phase.next_action(
            21 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
        )
        self.assertEqual(action.ability, "Anticipation")
        phase = PhaseOne(phase=2, carried_expected=TENDRILS)
        phase.observe([event(1, TENDRILS), event(2, RELEASE), event(3, AUTO)])
        self.assertEqual(phase.expected, onslaught)
        phase.observe([event(4, UPPERCUT), event(5, AUTO), event(6, AUTO)])
        self.assertIsNone(
            phase.next_action(
                6 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
            )
        )

    def test_phase_two_carry_over_counts_opening_attack(self):
        phase = PhaseOne(phase=2, carried_expected=HOLD_STILL)
        self.assertEqual(phase.expected, HOLD_STILL)
        phase.observe([event(1, controller["P2_OPENING_ATTACK"]), event(2, AUTO)])
        action = phase.next_action(
            2 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
        )
        self.assertEqual(action.ability, "Anticipation")

    def test_phase_two_opening_magic_attack_counts_as_auto(self):
        onslaught = controller["ONSLAUGHT"]
        opening = controller["P2_OPENING_ATTACK"]
        # 123908: opener, lone 28959 at +11, then Hold Still.
        phase = PhaseOne(phase=2, carried_expected=HOLD_STILL)
        phase.observe([dict(event(1, opening), received_tick=100)])
        phase.observe([dict(event(2, onslaught), received_tick=111)])
        self.assertEqual(phase.expected, HOLD_STILL)
        self.assertIsNone(phase.special)
        action = phase.next_action(
            2 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
        )
        self.assertEqual(action.ability, "Anticipation")
        # 105331: the 28959 at +15 is the Onslaught itself.
        phase = PhaseOne(phase=2, carried_expected=UPPERCUT)
        phase.observe([dict(event(1, opening), received_tick=100)])
        phase.observe([dict(event(2, onslaught), received_tick=115)])
        self.assertEqual(phase.special, onslaught)
        self.assertEqual(phase.expected, HOLD_STILL)

    def test_phase_four_forecasts_opening_and_cued_hold_still(self):
        phase = PhaseOne(phase=4, carried_expected=HOLD_STILL)
        self.assertEqual(phase.expected, HOLD_STILL)
        phase.observe([event(1, controller["P2_OPENING_ATTACK"]), event(2, AUTO)])
        timers = {"Anticipation": 0}
        self.assertIsNone(
            phase.next_action(2 * SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, flicking=True)
        )
        phase.observe([event(3, AUTO)])
        action = phase.next_action(3 * SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, flicking=True)
        self.assertEqual(action.ability, "Anticipation")
        # Tendrils restarts the count; 28966 is anticipated at once.
        phase = PhaseOne(phase=4)
        phase.observe([event(1, TENDRILS), event(2, RELEASE), event(3, AUTO)])
        self.assertIsNone(phase.expected)
        phase.observe([event(4, controller["P4_HOLD_STILL_CUE"])])
        self.assertEqual(phase.expected, HOLD_STILL)
        action = phase.next_action(4 * SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, flicking=True)
        self.assertEqual(action.ability, "Anticipation")

    def test_hold_position_blocks_escape_and_surge(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, UPPERCUT), received_tick=100)])
        phase.hold_position = True
        timers = {"Escape": 0, "Surge": 0}
        for tick in (104, 105):
            self.assertIsNone(
                phase.next_action(4 * SECOND_NS, timers, 0, Prayer.MELEE, received_tick=tick)
            )
        phase.hold_position = False
        action = phase.next_action(4 * SECOND_NS, timers, 0, Prayer.MELEE, received_tick=105)
        self.assertEqual(action.ability, "Escape")

    def test_stale_auto_count_cannot_trigger_anticipation(self):
        phase = PhaseOne()
        phase.observe([event(1, UPPERCUT), event(2, AUTO), event(3, AUTO)])
        self.assertIsNone(phase.next_action(6 * SECOND_NS, {"Anticipation": None}, 0, Prayer.MELEE))

    def test_uppercut_surge_keeps_its_acknowledgement_window(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, UPPERCUT), received_tick=10)])
        now = 4 * SECOND_NS
        timers = {"Escape": 0, "Surge": 0}
        action = phase.next_action(now, timers, 0, Prayer.MELEE, received_tick=14,
                                   uppercut_choice="Surge")
        self.assertEqual(action.ability, "Surge")
        phase.sent(action, now)
        self.assertIsNone(phase.next_action(now + 1, timers, 0, Prayer.MELEE, received_tick=14,
                                            uppercut_choice="Surge"))
        self.assertIsNone(phase.cast_result)
        phase.next_action(now + 2, {"Escape": 0, "Surge": 10000}, 0, Prayer.MELEE,
                          received_tick=15, uppercut_choice="Surge")
        self.assertEqual(phase.cast_result, ("Surge", True))


FontDuty = controller["FontDuty"]
GREEN_FONT = (25, 14608, 1832)


def fonts(green_config, green_health):
    """Fonts as in the 250% P4 capture (telos-20261114T171804): green plus two idle fonts."""
    return [
        (GREEN_FONT[0], green_config, green_health, GREEN_FONT[1:]),
        (26, 22895, 1, (14621, 1805)),
        (27, 22894, 1, (14638, 1829)),
    ]


class FontDutyTests(unittest.TestCase):
    def test_idle_fonts_need_nothing(self):
        duty = FontDuty()
        self.assertEqual(duty.update(fonts(22893, 1), 4190), (None, None))
        self.assertFalse(duty.active)

    def test_captured_green_font_charge_and_hold(self):
        duty = FontDuty()
        target, event = duty.update(fonts(22896, 7500), 4226)
        self.assertEqual((target, event), (GREEN_FONT[1:], "font_charge_started"))
        self.assertTrue(duty.active)
        # Player at (14616, 1833), eight tiles away: move now, then retry every 3 ticks.
        self.assertTrue(duty.move_due((14616, 1833), target, 4226))
        self.assertFalse(duty.move_due((14615, 1833), target, 4227))
        self.assertTrue(duty.move_due((14613, 1833), target, 4229))
        # The charge ran from (14609, 1831): inside reach, so no more clicks.
        self.assertFalse(duty.move_due((14609, 1831), target, 4232))
        for tick, health in ((4232, 6000), (4235, 4500), (4237, 3000), (4241, 1500)):
            self.assertEqual(duty.update(fonts(22896, health), tick), (target, None))
        self.assertEqual(duty.update(fonts(22899, 1), 4244), (target, "font_charged"))
        self.assertTrue(duty.active)
        hold_end = 4244 + controller["FONT_HOLD_TICKS"]
        self.assertEqual(duty.update(fonts(22899, 1), hold_end - 1), (target, None))
        self.assertEqual(duty.update(fonts(22899, 1), hold_end), (None, "font_hold_ended"))
        self.assertFalse(duty.active)
        # The finished font is not taken again; the next charging font is.
        self.assertEqual(duty.update(fonts(22899, 1), hold_end + 1), (None, None))
        red = [(26, 22898, 7500, (14621, 1805))]
        self.assertEqual(duty.update(red, hold_end + 50), ((14621, 1805), "font_charge_started"))

    def test_stand_tile_avoids_golems_and_counts_as_inside(self):
        stand = controller["font_stand_tiles"]
        in_font = controller["in_font"]
        font = (10192, 2216)
        # 173454 golems beside the first font: (10194, 2215), (10196, 2219), (10194, 2217).
        golem_tiles = {(10194, 2215), (10196, 2219), (10194, 2217)}
        tiles = stand(font, (10200, 2216), golem_tiles)
        tile = tiles[0]
        self.assertNotIn(tile, golem_tiles)
        self.assertEqual(tile, (10194, 2216))
        self.assertTrue(in_font(tile, font))
        # Earlier charges ran inside the 2x2 and one tile diagonally outside it.
        self.assertTrue(in_font((10193, 2216), font))
        self.assertTrue(in_font((14609, 1831), (14608, 1832)))
        self.assertFalse(in_font((10195, 2216), font))
        # With every ring tile taken, the font's own tile is the fallback.
        ring = {(font[0] + dx, font[1] + dy) for dx in range(-1, 3) for dy in range(-1, 3)}
        self.assertFalse(set(tiles) & golem_tiles)
        self.assertEqual(stand(font, (10200, 2216), ring), [font])

    def test_vanished_font_ends_duty(self):
        duty = FontDuty()
        duty.update(fonts(22896, 7500), 10)
        self.assertEqual(duty.update([], 11), (None, "font_lost"))
        self.assertFalse(duty.active)


BeamBlocker = controller["BeamBlocker"]


class BeamTests(unittest.TestCase):
    def test_stomp_beam_landing_requires_matching_colour_range_and_clearance(self):
        choose = controller["stream_destination"]
        kind_by_phase = controller["STOMP_BEAM_BY_PHASE"]
        player = SimpleNamespace(tile=SimpleNamespace(x=106, y=103, plane=0))
        pose = SimpleNamespace(tile=SimpleNamespace(x=100, y=100, plane=0), footprint_size=5)
        flags = HazardFlags.SCENE_LOCATION | HazardFlags.TILE | HazardFlags.TERRAIN_HEIGHT
        hazards = [
            SimpleNamespace(
                tile=SimpleNamespace(x=x, y=y, plane=plane),
                flags=flags,
                kind=kind,
                config_id=config,
            )
            for x, y, plane, kind, config in (
                (104, 103, 0, 1, 103545),  # Telos's footprint
                (105, 103, 0, 1, 103546),  # Outside the footprint, inside the stomp
                (107, 103, 1, 1, 103546),
                (117, 103, 0, 1, 103546),  # Eleven tiles away
                (116, 103, 0, 1, 103545),  # Ten tiles away
                (107, 103, 0, 3, 103549),  # Five tiles from the centre: inside the stomp
                (108, 103, 0, 3, 103549),
            )
        ]
        reader = SimpleNamespace(
            hazard_info=SimpleNamespace(count=len(hazards)),
            hazards=hazards,
            hazard_terrain_height=lambda hazard: 4544.0,
        )
        with patch.dict(choose.__globals__, project_tile=lambda r, tile, plane, height: tile):
            green = choose(reader, kind_by_phase[1], player, pose, stomp=True)
            red = choose(reader, kind_by_phase[3], player, pose, stomp=True)
            self.assertEqual(green, (((116, 103), 0, 103545), (116, 103)))
            self.assertEqual(red, (((108, 103), 0, 103549), (108, 103)))
            self.assertIsNone(
                choose(
                    reader, kind_by_phase[1], player, pose, rejected_tiles={(116, 103)}, stomp=True
                )
            )
            for hover in ((104, 103, 0), (105, 103, 0), (107, 103, 0), (117, 103, 0)):
                self.assertIsNone(
                    choose(reader, kind_by_phase[1], player, pose, use_tile=hover, stomp=True)
                )

    def test_beam_keeping_prefers_strip_tiles_in_melee_reach_and_never_under_telos(self):
        choose = controller["stream_destination"]
        player = SimpleNamespace(tile=SimpleNamespace(x=110, y=110, plane=0))
        pose = SimpleNamespace(tile=SimpleNamespace(x=100, y=100, plane=0), footprint_size=5)
        flags = HazardFlags.SCENE_LOCATION | HazardFlags.TILE | HazardFlags.TERRAIN_HEIGHT
        hazards = [
            SimpleNamespace(
                tile=SimpleNamespace(x=x, y=y, plane=0), flags=flags, kind=1, config_id=103545
            )
            for x, y in ((104, 104), (105, 102), (109, 109), (108, 104))
        ]
        reader = SimpleNamespace(
            hazard_info=SimpleNamespace(count=len(hazards)),
            hazards=hazards,
            hazard_terrain_height=lambda hazard: 4544.0,
        )
        with patch.dict(choose.__globals__, project_tile=lambda r, tile, plane, height: tile):
            # Nearest first without the preference; the footprint tile (104,104) never.
            self.assertEqual(choose(reader, 1, player, pose)[0][0], (109, 109))
            self.assertEqual(choose(reader, 1, player, pose, prefer_reach=True)[0][0], (105, 102))
            fallback = choose(
                reader, 1, player, pose, prefer_reach=True, rejected_tiles={(105, 102)}
            )
            self.assertEqual(fallback[0][0], (109, 109))
        strip = controller["on_strip"]
        self.assertTrue(strip(reader, 1, (109, 109), 0))
        self.assertFalse(strip(reader, 3, (109, 109), 0))
        self.assertFalse(strip(reader, 1, (109, 109), 1))

    def test_p3_anchor_keeps_the_player_on_a_strip_tile_that_left_the_reader_window(self):
        # 07:54 P3: on (6874,1045) at 17074, the tile left the listed strip at 17075, and the
        # keeper walked one tile; the anchor keeps the player still instead.
        keeper = controller["BeamKeeper"]()
        tile = (6874, 1045)
        column = [(6874, y) for y in range(1046, 1070)]
        self.assertTrue(keeper.holding(tile, True, column))
        # The listed window moved on along the same column: still in the beam.
        self.assertTrue(keeper.holding(tile, False, column))
        self.assertTrue(keeper.holding(tile, False, []))
        # 08:01 at 17856: Telos's meter rose while the player stood on (9171,1046) in a row
        # strip; the meter no longer ends the anchor.
        row = [(x, 1046) for x in range(9172, 9190)]
        self.assertTrue(keeper.holding((9171, 1046), True, row))
        self.assertTrue(keeper.holding((9171, 1046), False, row))
        # The strip reappeared on another line: the beam left the player.
        elsewhere = [(9160, y) for y in range(1030, 1050)]
        self.assertFalse(keeper.holding((9171, 1046), False, elsewhere))
        # A mechanic that moved the player ends the anchor too.
        keeper.holding(tile, True, column)
        self.assertFalse(keeper.holding((6874, 1042), False, column))
        self.assertFalse(keeper.holding(tile, False, column))

    def test_beam_keeper_remembers_recent_strip_tiles_and_retries_once_per_tick(self):
        keeper = controller["BeamKeeper"]()
        recent = controller["BEAM_RECENT_TICKS"]
        keeper.remember([(9178, 1038), (9178, 1039)], 17906)
        keeper.remember([(9178, 1040)], 17908)
        # 08:01: the hover matched (9178,1038) after it left the listed window.
        self.assertTrue(keeper.recently_on_strip((9178, 1038)))
        keeper.remember([], 17906 + recent + 1)
        self.assertFalse(keeper.recently_on_strip((9178, 1038)))
        self.assertTrue(keeper.recently_on_strip((9178, 1040)))
        self.assertTrue(keeper.move_due(100))
        keeper.retry_now(100)
        self.assertTrue(keeper.move_due(100))
        keeper.retry_now(100)
        self.assertFalse(keeper.move_due(100))

    def test_beam_keeper_waits_between_moves(self):
        keeper = controller["BeamKeeper"]()
        retry = controller["BEAM_KEEP_RETRY_TICKS"]
        self.assertTrue(keeper.move_due(10))
        self.assertFalse(keeper.move_due(10 + retry - 1))
        self.assertTrue(keeper.move_due(10 + retry))

    def test_virus_destination_checks_colour_plane_projection_and_footprint(self):
        choose = controller["virus_beam_destination"]
        player = SimpleNamespace(tile=SimpleNamespace(x=100, y=100, plane=0))
        boss = SimpleNamespace(instance_id=7)
        pose = SimpleNamespace(
            instance_id=7,
            tile=SimpleNamespace(x=101, y=101, plane=0),
            footprint_size=3,
            valid_mask=SpatialFlags.TILE | SpatialFlags.SAME_WORLD,
        )
        flags = HazardFlags.SCENE_LOCATION | HazardFlags.TILE | HazardFlags.TERRAIN_HEIGHT
        hazards = [
            SimpleNamespace(
                tile=SimpleNamespace(x=x, y=y, plane=plane),
                flags=flags,
                kind=kind,
                config_id=config,
            )
            for x, y, plane, kind, config in (
                (101, 101, 0, 2, 103547),
                (99, 100, 0, 3, 103549),
                (100, 101, 1, 2, 103547),
                (100, 99, 0, 2, 103547),
                (98, 100, 0, 2, 103548),
            )
        ]
        reader = SimpleNamespace(
            hazard_info=SimpleNamespace(count=len(hazards)),
            hazards=hazards,
            spatial=SimpleNamespace(npc_count=1),
            npc_spatial=[pose],
            hazard_terrain_height=lambda hazard: 4544.0,
        )
        with patch.dict(
            choose.__globals__,
            project_tile=lambda r, tile, plane, height: (520, 530) if tile[0] == 98 else None,
        ):
            self.assertEqual(choose(reader, 2, player, boss), (((98, 100), 0, 103548), (520, 530)))
            self.assertIsNone(choose(reader, 2, player, boss, use_tile=(100, 99, 0)))
            self.assertIsNone(choose(reader, 2, player, boss, rejected={(520, 530)}))

    def test_virus_timer_preserves_unknown_and_decodes_countdown(self):
        timer = controller["virus_timer"]
        encounter = EncounterState()
        encounter.raw_count = 1
        sample = encounter.raw[0]
        sample.varp_id = 6362
        self.assertIsNone(timer(encounter))
        sample.valid = 1
        for value, expected in ((3840, 3840), (3712, 3712), (7, None), (0, 0)):
            sample.value = value
            self.assertEqual(timer(encounter), expected)

    def test_virus_clear_requires_recent_message_after_onset(self):
        response = controller["VirusResponse"]()
        old = TelosCleanse(1, 1000, 1, 0)
        self.assertEqual(response.observe(2, 3840, 42, 1001, old), ((42, 1001), None))
        self.assertEqual(response.observe(2, 0, 44, 1061, old), (None, None))
        message = TelosCleanse(2, 1061, 1, 0)
        self.assertEqual(
            response.observe(2, 0, 44, 1061, message), (None, "cleanse_confirmed")
        )

    def test_virus_phase_change_and_unacknowledged_clear(self):
        response = controller["VirusResponse"]()
        absent = TelosCleanse()
        self.assertEqual(response.observe(1, 3840, 40, 1000, absent), (None, None))
        response.observe(2, 3840, 42, 1060, absent)
        self.assertEqual(response.observe(3, 0, 43, 1090, absent), (None, "phase_changed"))
        response.observe(3, 3840, 44, 1120, absent)
        self.assertEqual(response.observe(3, 0, 46, 1180, absent), (None, None))
        self.assertEqual(response.observe(3, 0, 48, 1240, absent), (None, "timer_cleared"))

    def test_meter_rises_detect_feeding_but_projectile_spend_does_not(self):
        blocker = BeamBlocker()
        # Logged 174445 meter: 0 at the P2 opener, then +3 per tick while the stream fed Telos.
        self.assertFalse(blocker.observe(0, 6618))
        self.assertFalse(blocker.observe(0, 6638))
        self.assertFalse(blocker.observe(3, 6639))
        self.assertTrue(blocker.observe(6, 6640))
        for tick in range(6641, 6649):
            self.assertTrue(blocker.observe(3 * (tick - 6638), tick))
        # An Onslaught projectile spends 7 while the stream keeps feeding: still fed.
        self.assertTrue(blocker.observe(23, 6649))
        self.assertTrue(blocker.observe(26, 6650))
        # Once blocked, an unfed Onslaught only falls (100% kill: 70, 60, 50 ...).
        for tick, meter in ((5702, 60), (5706, 50), (5710, 40)):
            self.assertFalse(blocker.observe(meter, tick))

    def test_multiple_meter_updates_at_one_tick_do_not_confirm_feeding(self):
        blocker = BeamBlocker()
        self.assertFalse(blocker.observe(0, 100))
        self.assertFalse(blocker.observe(3, 101))
        self.assertFalse(blocker.observe(6, 101))
        self.assertTrue(blocker.observe(9, 102))

    def test_flat_meter_is_not_fed(self):
        # 175142 P2: the meter sat at 3 for 30 ticks after two early rises.
        blocker = BeamBlocker()
        for tick, meter in ((7350, 0), (7351, 3), (7352, 3)):
            blocker.observe(meter, tick)
        for tick in range(7353, 7390):
            self.assertFalse(blocker.observe(3, tick))

    def test_moves_are_rate_limited(self):
        blocker = BeamBlocker()
        self.assertTrue(blocker.move_due(100))
        self.assertFalse(blocker.move_due(101))
        self.assertTrue(blocker.move_due(100 + controller["BEAM_MOVE_RETRY_TICKS"]))


TargetPicker = controller["TargetPicker"]


def golems(pure, corrupt, volcanic):
    """First-font golems of the 100% kill (telos-20261114T173454) with the given health."""
    return [
        (34, controller["PURE_GOLEM"], pure, (10194, 2217)),
        (33, controller["CORRUPT_GOLEM"], corrupt, (10196, 2219)),
        (32, controller["VOLCANIC_GOLEM"], volcanic, (10194, 2215)),
    ]


class TargetPickerTests(unittest.TestCase):
    def test_golems_in_priority_order_then_telos(self):
        picker = TargetPicker()
        # Nothing is clicked while the font charges.
        self.assertIsNone(picker.choose(golems(10000, 10000, 10000), "charging", 5909))
        choice = picker.choose(golems(10000, 10000, 10000), "holding", 5923)
        self.assertEqual(choice[0], 32)  # Volcanic (red) first.
        self.assertIsNone(picker.choose(golems(10000, 10000, 4970), "holding", 5931))
        self.assertEqual(picker.choose(golems(10000, 10000, 0), "holding", 5934)[0], 34)
        self.assertEqual(picker.choose(golems(0, 9782, 0), None, 5937)[0], 33)
        self.assertEqual(picker.choose([], None, 5995), "telos")
        self.assertIsNone(picker.choose([], None, 5996))

    def test_golem_is_clicked_again_after_the_player_moves(self):
        picker = TargetPicker()
        self.assertEqual(picker.choose(golems(9733, 10000, 0), "holding", 9377)[0], 34)
        self.assertIsNone(picker.choose(golems(9000, 10000, 0), "holding", 9380))
        picker.moved()  # Stomp Dive at 9403 in 180906.
        self.assertEqual(picker.choose(golems(8000, 10000, 0), None, 9405)[0], 34)

    def test_font_end_without_golems_reengages_telos_and_retries(self):
        picker = TargetPicker()
        picker.telos_due = True
        self.assertIsNone(picker.choose([], "holding", 100))
        self.assertEqual(picker.choose([], None, 101), "telos")
        picker.failed(101)
        self.assertIsNone(picker.choose([], None, 102))
        retry_tick = 101 + controller["FONT_MOVE_RETRY_TICKS"]
        self.assertEqual(picker.choose([], None, retry_tick), "telos")


PrayerFlicker = controller["PrayerFlicker"]


class PrayerFlickerTests(unittest.TestCase):
    def test_final_prayer_outcome_precedes_the_end_timestamp(self):
        stream = io.StringIO()
        clock = iter(range(100, 110))
        attempt = AttemptLog(stream, mode="practice", enrage=250, monotonic_ns=clock.__next__)
        attempt.write("prayer_flick_sent", prayer="magic")
        attempt.write("attempt_ended", result="incomplete", reason="interrupted")
        rows = list(map(json.loads, stream.getvalue().splitlines()))
        outcome, end = rows[-2:]
        self.assertEqual(outcome["outcome"], "attempt_ended")
        self.assertLessEqual(outcome["elapsed_ns"], end["elapsed_ns"])

    def test_attempt_log_matches_observed_overhead_to_the_issued_request(self):
        stream = io.StringIO()
        now = 100
        attempt = AttemptLog(
            stream, mode="practice", enrage=250, monotonic_ns=lambda: now,
            prayer_ack_timeout_ns=20,
        )
        attempt.game_tick = 61
        attempt.write("prayer_flick_sent", prayer="magic", cue_native_sequence=10)
        attempt.observe_prayer(99, "magic")
        attempt.observe_prayer(103, "melee")
        attempt.game_tick = 62
        now = 108
        attempt.observe_prayer(now, "magic")
        attempt.write("prayer_flick_sent", prayer="soul_split")
        now = 129
        attempt.observe_prayer(now, "magic")
        attempt.write("prayer_flick_sent", prayer="soul_split")
        attempt.write("attempt_ended", result="incomplete", reason="interrupted")
        outcomes = [
            row for row in map(json.loads, stream.getvalue().splitlines())
            if row["event"] == "prayer_flick_outcome"
        ]
        self.assertEqual([row["outcome"] for row in outcomes], [
            "acknowledged", "timeout", "attempt_ended",
        ])
        self.assertEqual(
            (outcomes[0]["request_sequence"], outcomes[0]["sent_tick"], outcomes[0]["game_tick"]),
            (2, 61, 62),
        )

    def test_animation_holds_melee_until_next_received_tick(self):
        flicker = PrayerFlicker()
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, 10, 0), Prayer.SOUL_SPLIT)
        flicker.sent(Prayer.SOUL_SPLIT, 0)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 1))
        self.assertEqual(flicker.observe([event(1, AUTO)], 10, 2)["event_sequence"], 1)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 3), Prayer.MELEE)
        flicker.sent(Prayer.MELEE, 3)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 4))
        self.assertIsNone(flicker.next_prayer(Prayer.MELEE, 10, 5))
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, 11, 6), Prayer.SOUL_SPLIT)

    def test_cue_without_its_own_tick_uses_the_sample_tick(self):
        # 07:12: Hold Still arrived with an invalid counter; the estimated tick opens its windows.
        flicker = PrayerFlicker()
        flicker.observe([dict(event(1, HOLD_STILL), received_tick=None)], 12727, 0)
        self.assertEqual(
            [flicker.active_prayer(tick, 0) for tick in range(12727, 12732)],
            [Prayer.MELEE] * 4 + [None],
        )

    def test_intervening_walk_sample_preserves_the_original_cue_tick(self):
        flicker = PrayerFlicker()
        action = dict(event(1, AUTO), received_tick=21)
        flicker.observe([action], 22, 0)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 22, 1))

    def test_uppercut_and_repeated_events_do_not_flick(self):
        flicker = PrayerFlicker()
        self.assertIsNone(flicker.observe([event(1, UPPERCUT)], 10, 0))
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 0))
        flicker.observe([event(2, HOLD_STILL)], 10, 0)
        self.assertIsNone(flicker.observe([event(2, HOLD_STILL)], 11, 0))
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 0), Prayer.MELEE)

    def test_phase_lockout_ends_when_the_first_new_attack_arrives(self):
        flicker = PrayerFlicker()
        transition = controller["P1_TO_P2_TRANSITION"]
        opener = controller["P2_OPENING_ATTACK"]
        flicker.observe([event(1, AUTO)], 10, 0)
        self.assertEqual(flicker.update_transition([event(2, transition)], 1), (True, 2))
        self.assertFalse(flicker.windows)
        self.assertIsNone(flicker.update_transition([event(3, transition)], 2))
        self.assertIsNone(flicker.update_transition([event(4, opener)], 2))
        self.assertEqual(flicker.transition_phase, 1)
        self.assertIsNone(flicker.observe([event(4, opener)], 15, 1))
        magic = [event(5, controller["MAGIC_ATTACK"])]
        self.assertEqual(flicker.update_transition(magic, 2), (False, 5))
        flicker.observe(magic, 16, 2, distance=2)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 16, 3), Prayer.MAGIC)

    def test_long_range_magic_flick_is_delayed(self):
        magic = controller["MAGIC_ATTACK"]
        # 175845: at 9 tiles a 28959 at 8080 hit for 3,765 at 8083; protect +1 and +2 only.
        flicker = PrayerFlicker()
        flicker.observe([event(1, magic)], 8080, 0, distance=9)
        self.assertEqual(
            [flicker.active_prayer(tick, 0) for tick in range(8080, 8083)],
            [None, Prayer.MAGIC, Prayer.MAGIC],
        )
        # Up to 4 tiles away: one tick, on the animation tick only (no pre-flick).
        for distance in (1, 4):
            flicker = PrayerFlicker()
            flicker.observe([event(1, magic)], 100, 0, distance=distance)
            self.assertEqual(
                [flicker.active_prayer(tick, 0) for tick in range(100, 106)],
                [Prayer.MAGIC, None, None, None, None, None],
            )
        # A melee auto during a pending long-range window deflects melee on its own tick.
        flicker = PrayerFlicker()
        flicker.observe([event(1, magic)], 200, 0, distance=8)
        flicker.observe([event(2, AUTO)], 200, 0, distance=8)
        self.assertEqual(flicker.active_prayer(200, 0), Prayer.MELEE)
        self.assertEqual(flicker.cue_event, 2)
        self.assertEqual(flicker.active_prayer(201, 0), Prayer.MAGIC)
        self.assertEqual(flicker.cue_event, 1)

    def test_magic_flick_windows_by_distance(self):
        windows = controller["magic_flick_ticks"]
        self.assertEqual([windows(d) for d in (0, 4, 5, 6, 7, 9, 12)],
                         [(0, 0), (0, 0), (0, 1), (0, 1), (1, 2), (1, 2), (1, 3)])
        self.assertEqual(windows(None), (0, 2))

    def test_magic_attack_selects_deflect_magic(self):
        flicker = PrayerFlicker()
        magic = controller["MAGIC_ATTACK"]
        flicker.observe([event(1, magic)], 30, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 30, 0), Prayer.MAGIC)
        flicker.sent(Prayer.MAGIC, 0)
        # No positions: Deflect Magic covers +0 to +2.
        self.assertIsNone(flicker.next_prayer(Prayer.MAGIC, 32, 1))
        self.assertEqual(flicker.next_prayer(Prayer.MAGIC, 33, 1), Prayer.SOUL_SPLIT)
        flicker.sent(Prayer.SOUL_SPLIT, 1)
        flicker.observe([event(2, AUTO)], 33, 2)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 33, 3), Prayer.MELEE)

    def test_phase_three_default_is_deflect_magic_but_melee_autos_keep_melee(self):
        flicker = PrayerFlicker()
        flicker.default_prayer = Prayer.MAGIC
        flicker.observe([event(1, controller["P4_HOLD_STILL_CUE"])], 40, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 40, 0), Prayer.MAGIC)
        # P3 28929 hits were type 133 melee.
        flicker.observe([event(2, AUTO)], 44, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 44, 0), Prayer.MELEE)

    def test_anima_bomb_deflects_melee_through_plus_two(self):
        flicker = PrayerFlicker()
        flicker.observe([event(1, controller["ANIMA_BOMB"])], 6007, 0)
        self.assertEqual(
            [flicker.active_prayer(tick, 0) for tick in range(6007, 6010)],
            [Prayer.MELEE, Prayer.MELEE, None],
        )

    def test_beam_moves_wait_for_a_quiet_tick(self):
        flicker = PrayerFlicker()
        self.assertTrue(flicker.quiet(10, 0))
        self.assertFalse(flicker.quiet(None, 0))
        flicker.observe([event(1, AUTO)], 10, 0)
        self.assertFalse(flicker.quiet(10, 0))
        self.assertTrue(flicker.quiet(11, 0))
        # A long-range magic window starting next tick also blocks the move.
        flicker.observe([event(2, controller["MAGIC_ATTACK"])], 12, 0, distance=8)
        self.assertFalse(flicker.quiet(12, 0))
        self.assertFalse(flicker.quiet(13, 0))
        self.assertTrue(flicker.quiet(15, 0))

    def test_hold_still_deflects_both_hits_without_resonance(self):
        flicker = PrayerFlicker()
        flicker.default_prayer = Prayer.MAGIC
        # Recorded P1 cue at 3518: the small hit at +2 and the heavy hit at +4.
        flicker.observe([event(1, HOLD_STILL)], 3518, 0)
        self.assertEqual(
            [flicker.active_prayer(tick, 0) for tick in range(3518, 3524)],
            [Prayer.MELEE] * 4 + [None, None],
        )
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, 3522, 0), Prayer.SOUL_SPLIT)

    def test_hold_still_returns_to_soul_split_once_resonance_is_confirmed(self):
        flicker = PrayerFlicker()
        flicker.observe([event(7, HOLD_STILL)], 100, 0)
        # Confirmed at +3 (pressed at +2): Soul Split is requested at +3 for the heavy hit.
        self.assertTrue(flicker.release(7, 103))
        self.assertFalse(flicker.release(7, 103))
        # A later sample without a received tick must not bring the deflect back.
        self.assertIsNone(flicker.active_prayer(None, 1))
        self.assertEqual(
            [flicker.active_prayer(tick, 0) for tick in range(100, 104)],
            [Prayer.MELEE, Prayer.MELEE, Prayer.MELEE, None],
        )

    def test_phase_three_hold_still_deflects_magic_then_melee(self):
        flicker = PrayerFlicker()
        flicker.phase = 3
        flicker.default_prayer = Prayer.MAGIC
        # 5862 at 250% P3: a type 139 hit of 954 at +2 under Deflect Melee, 5,035 melee at +4.
        flicker.observe([event(1, HOLD_STILL)], 5862, 0)
        self.assertEqual(
            [flicker.active_prayer(tick, 0) for tick in range(5862, 5867)],
            [Prayer.MAGIC, Prayer.MAGIC, Prayer.MAGIC, Prayer.MELEE, None],
        )
        # Release from +3 drops the melee window but keeps the protected small-hit ticks.
        released = PrayerFlicker()
        released.phase = 3
        released.observe([event(1, HOLD_STILL)], 5862, 0)
        released.release(1, 5865)
        self.assertEqual(released.active_prayer(5864, 0), Prayer.MAGIC)
        self.assertIsNone(released.active_prayer(5865, 0))

    def test_phase_three_counts_magic_autos_before_hold_still(self):
        phase = PhaseOne(phase=3)
        magic = controller["MAGIC_ATTACK"]
        phase.observe([event(1, UPPERCUT), event(2, magic), event(3, magic)])
        self.assertEqual(phase.expected, HOLD_STILL)
        action = phase.next_action(
            3 * SECOND_NS, {"Anticipation": 0}, 0, Prayer.SOUL_SPLIT, flicking=True
        )
        self.assertEqual(action.ability, "Anticipation")

    def test_tendrils_release_holds_melee_through_unanimated_auto(self):
        flicker = PrayerFlicker()
        flicker.observe([event(1, RELEASE)], 20, 0)
        for tick in (20, 21, 22):
            self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, tick, 0), Prayer.MELEE)
        flicker.observe([event(2, AUTO)], 21, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 22, 0), Prayer.MELEE)
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, 23, 0), Prayer.SOUL_SPLIT)

    def test_invalid_tick_falls_back_to_one_tick_duration(self):
        flicker = PrayerFlicker()
        fallback = controller["TICK_FALLBACK_NS"]
        flicker.observe([event(1, TENDRILS)], None, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, None, fallback - 1), Prayer.MELEE)
        self.assertEqual(flicker.next_prayer(Prayer.MELEE, None, fallback), Prayer.SOUL_SPLIT)

    def test_unacknowledged_toggle_is_not_repeated_before_timeout(self):
        flicker = PrayerFlicker()
        timeout = controller["ACK_TIMEOUT_NS"]
        flicker.observe([event(1, AUTO)], 10, 0)
        flicker.sent(Prayer.MELEE, 0)
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 11, timeout - 1))
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 11, timeout))


class PracticeRunTests(unittest.TestCase):
    def test_confirmed_phase_survives_tendrils_healing_above_previous_threshold(self):
        check = controller["check_active_phase"]
        health = {"current": 450_309, "max": 600_000}
        check(2, 2, health, 600_000)
        check(1, 2, {**health, "current": 460_764}, 600_000, saw_p2_transition=True)
        with self.assertRaisesRegex(RuntimeError, "Phase 2 entry"):
            check(1, 2, health, 600_000)
        with self.assertRaisesRegex(RuntimeError, "Unexpected Telos stage change"):
            check(2, 1, health, 600_000, saw_p2_transition=True)
        check(2, 3, {**health, "current": 300_000}, 600_000)

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

    def test_manual_engage_uses_prayer_key_and_waits_for_operator(self):
        run = controller["run"]
        reader, board, keys = Mock(), Mock(), Mock()
        keys.get_async_key_state.return_value = 0
        reader.combat.scene_epoch = 1
        reader.position.valid = False
        reader.abilities = []
        reader.drain_npc_action_events.return_value = ([], 0)
        reader.drain_player_hit_events.return_value = ([], 0)
        reader.active_prayer.side_effect = [Prayer.NONE] + [Prayer.MELEE] * 5
        clock = 10 * SECOND_NS
        reader.monotonic_ns.return_value = clock
        info = SimpleNamespace(
            pid=1, process_creation_time=2, monotonic_ns=clock, status=Status.AVAILABLE, sequence=1
        )
        player = Mock(clock_stalled_ns=0, gcd_reason=Reason.NONE, gcd_remaining_ms=0)
        player.variable.side_effect = {Variable.PRAYER_PACKED: 990}.get
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
        samples = [(info, player, dormant, None, 9900)] * 3 + [
            (info, player, active, 1, 9900),
            (info, player, phase_two, 2, 9000),
        ]
        args = SimpleNamespace(
            duration=30,
            freedom="U",
            anticipation="A",
            hurricane=None,
            surge=None,
            reacquire=None,
            continue_phase_two=False,
            frame=None,
            melee=None,
            approach=None,
            observe_transition=False,
            manual_engage=True,
            flick_melee_key="F3",
        )
        console = SimpleNamespace(GameWindow=Mock(), frame_target=Mock(), move_pointer=Mock())
        mock_globals = {
            "ManualPrayerKeys": Mock(return_value=keys),
            "boss_sample": Mock(side_effect=samples),
            "player_hit_splats_record": Mock(return_value={}),
            "log": Mock(),
        }
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                self.assertEqual(run(reader, args, board), 2)
        console.GameWindow.assert_not_called()
        console.frame_target.assert_not_called()
        board.command.assert_not_called()
        self.assertEqual(
            [[key.name for key in call.args[0]] for call in board.tap.call_args_list], [["F3"]]
        )
        logged = [call.args[0] for call in mock_globals["log"].call_args_list]
        self.assertIn("waiting_for_engage", logged)

    def test_focus_loss_pauses_input_instead_of_stopping(self):
        run = controller["run"]
        reader, board, keys = Mock(), Mock(), Mock()
        keys.get_async_key_state.return_value = 0
        # Focused at start, then unfocused for two checks, then focused again.
        focus = iter([7, 7, None, None])
        keys.game_window.side_effect = lambda _session: next(focus, 7)
        reader.combat.scene_epoch = 1
        reader.position.valid = False
        reader.abilities = []
        reader.drain_npc_action_events.return_value = ([], 0)
        reader.drain_player_hit_events.return_value = ([], 0)
        reader.active_prayer.side_effect = [Prayer.MELEE] * 10
        clock = 10 * SECOND_NS
        reader.monotonic_ns.return_value = clock
        info = SimpleNamespace(
            pid=1, process_creation_time=2, monotonic_ns=clock, status=Status.AVAILABLE, sequence=1
        )
        player = Mock(clock_stalled_ns=0, gcd_reason=Reason.NONE, gcd_remaining_ms=0)
        player.variable.side_effect = {Variable.PRAYER_PACKED: 990}.get
        dormant = SimpleNamespace(config_id=22892, instance_id=3)
        active = SimpleNamespace(
            config_id=22891,
            instance_id=4,
            flags=NPCFlags.HEALTH,
            health_max=400_000,
            health_current=400_000,
        )
        # Observation continues while unfocused, so every loop consumes a sample.
        samples = [
            (info, player, dormant, None, 9900),
            (info, player, dormant, None, 9900),
            (info, player, active, 1, 9900),
            (info, player, active, 1, 9900),
            (info, player, active, 1, 9900),
            (info, player, active, 1, 0),
        ]
        args = SimpleNamespace(
            freedom="U",
            anticipation="A",
            hurricane=None,
            surge=None,
            reacquire=None,
            continue_phase_two=True,
            frame=None,
            melee=None,
            approach=None,
            observe_transition=False,
            manual_engage=True,
            flick_melee_key="F3",
        )
        console = SimpleNamespace(GameWindow=Mock(), frame_target=Mock(), move_pointer=Mock())
        mock_globals = {
            "ManualPrayerKeys": Mock(return_value=keys),
            "boss_sample": Mock(side_effect=samples),
            "player_hit_splats_record": Mock(return_value={}),
            "log": Mock(),
        }
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                with self.assertRaisesRegex(RuntimeError, "Player HP reached zero"):
                    run(reader, args, board)
        logged = [call.args[0] for call in mock_globals["log"].call_args_list]
        self.assertIn("input_paused", logged)
        self.assertIn("input_resumed", logged)
        self.assertIn("engaged", logged)

    def test_input_waits_for_baseline_prayer_and_stops_at_phase_two(self):
        run = controller["run"]
        reader = Mock()
        board = Mock()
        keys = Mock()
        keys.get_async_key_state.return_value = 0
        reader.combat.scene_epoch = 1
        reader.position.valid = False
        reader.spatial.player.valid_mask = 0  # No player tile: magic flicks use the default.
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
        player.variable.side_effect = {Variable.PRAYER_PACKED: 990}.get
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
        reader.active_prayer.side_effect = [Prayer.MELEE] * 7
        phase_three = SimpleNamespace(**vars(phase_two))
        phase_three.health_current = 200_000
        phase_four = SimpleNamespace(**vars(phase_two))
        phase_four.health_current = 90_000
        mock_globals["boss_sample"].side_effect = (
            samples[:3]
            + samples[-2:]
            + [
                (info, player, damaged_boss, 2, 9000),
                (info, player, phase_three, 3, 9000),
                (info, player, phase_four, 4, 9000),
                (info, player, phase_four, 4, 0),
            ]
        )
        mock_globals["telos_actions"].side_effect = [[], [], [event(1, HOLD_STILL)], [], []]
        mock_globals["cooldowns"] = Mock(return_value={"Freedom": 0})
        reader.monotonic_ns.return_value = SECOND_NS
        info.monotonic_ns = SECOND_NS
        with patch.dict(sys.modules, {"game_console": console}), patch("time.sleep"):
            with patch.dict(run.__globals__, mock_globals):
                # Only death (or F12) ends the run; P3 and P4 are both handled.
                with self.assertRaisesRegex(RuntimeError, "Player HP reached zero"):
                    run(reader, args, board)
        # No stun was reported, so Hold Still alone sends no Freedom.
        self.assertEqual([call.args[0][-1].name for call in board.tap.call_args_list], [])
        logged = [call.args[0] for call in mock_globals["log"].call_args_list]
        self.assertIn("phase_three_observed", logged)
        self.assertIn("phase_4_observed", logged)

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
