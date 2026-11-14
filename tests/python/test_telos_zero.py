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
        # Hold Still alone never triggers Freedom; Anticipation is the planned answer.
        self.assertIsNone(phase.next_action(4 * SECOND_NS, timers, 1000, Prayer.MELEE))
        self.assertEqual(phase.cast_result, ("Anticipation", False))
        action = phase.next_action(4 * SECOND_NS, timers, 1000, Prayer.MELEE, stun_expiry=900)
        self.assertEqual(action, controller["Action"]("Freedom", "Player stunned"))
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
        self.assertFalse(stomp((14555, 1486), 5, (14562, 1484)))
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
        floor = {(106, 202), (107, 202), (108, 202), (97, 202), (102, 213), (100, 200)}
        # (106,202) is inside the stomp area, (102,213) is beyond Dive reach; nearest first.
        self.assertEqual(targets(boss, 5, player, floor), [(107, 202), (108, 202), (97, 202)])
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
        self.assertIsNone(phase.next_action(SECOND_NS, timers, 20, Prayer.MELEE))
        self.assertEqual(phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE).ability, "Hurricane")
        phase.observe([event(2, RELEASE)])
        self.assertIsNone(phase.next_action(2 * SECOND_NS, timers, 0, Prayer.MELEE))

    def test_anticipation_ignores_gcd_and_retries_once(self):
        phase = PhaseOne()
        phase.observe([event(1, UPPERCUT), event(2, AUTO), event(3, AUTO)])
        timers = {"Anticipation": 0}
        now = 3 * SECOND_NS
        timeout = controller["ACK_TIMEOUT_NS"]
        action = phase.next_action(now, timers, 1200, Prayer.SOUL_SPLIT, flicking=True)
        self.assertEqual(action.ability, "Anticipation")
        phase.sent(action, now)
        retry = phase.next_action(now + timeout, timers, 1200, Prayer.SOUL_SPLIT, flicking=True)
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

    def test_resonance_queues_into_the_gcd_slot_at_plus_three(self):
        # The 500% case: Revolution started a GCD on the Hold Still tick, freeing +3.
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=5171)])
        action = phase.next_action(
            SECOND_NS, {"Resonance": None}, 1780, Prayer.SOUL_SPLIT, received_tick=5171
        )
        self.assertEqual(action.ability, "Resonance")

    def test_resonance_retries_once_when_queued_press_did_not_cast(self):
        # The 500% P2 case: queued at +0, but the slot freed at +3 without a cast.
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=5791)])
        timers = {"Resonance": 0}
        first = phase.next_action(SECOND_NS, timers, 1780, Prayer.SOUL_SPLIT, received_tick=5791)
        phase.sent(first, SECOND_NS)
        for tick, gcd in ((5792, 1180), (5793, 580)):
            self.assertIsNone(
                phase.next_action(SECOND_NS, timers, gcd, Prayer.SOUL_SPLIT, received_tick=tick)
            )
        retry = phase.next_action(SECOND_NS, timers, 0, Prayer.SOUL_SPLIT, received_tick=5794)
        self.assertEqual(retry.ability, "Resonance")
        phase.sent(retry, SECOND_NS)
        self.assertIsNone(phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE, received_tick=5794))

    def test_resonance_skips_free_slot_before_plus_two(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=50)])
        timers = {"Resonance": 0}
        # Free at +0/+1 (Revolution takes it); a GCD started at +2 would free +5, too late.
        for tick, gcd in ((50, 0), (51, 0), (52, 1780)):
            self.assertIsNone(
                phase.next_action(SECOND_NS, timers, gcd, Prayer.SOUL_SPLIT, received_tick=tick)
            )
        action = phase.next_action(SECOND_NS, timers, 600, Prayer.SOUL_SPLIT, received_tick=53)
        self.assertEqual(action.ability, "Resonance")
        self.assertIsNone(phase.next_action(SECOND_NS, timers, 0, Prayer.MELEE, received_tick=55))

    def test_resonance_without_gcd_reading_waits_for_first_hold_still_hit(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=50)])
        timers = {"Resonance": 0}
        phase.observe_hits(1, 51)
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, None, Prayer.SOUL_SPLIT, received_tick=51)
        )
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, None, Prayer.SOUL_SPLIT, received_tick=52)
        )
        phase.observe_hits(1, 52)
        action = phase.next_action(SECOND_NS, timers, None, Prayer.SOUL_SPLIT, received_tick=52)
        self.assertEqual(action, controller["Action"]("Resonance", "Hold Still second melee hit"))
        phase.sent(action, SECOND_NS)
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, None, Prayer.SOUL_SPLIT, received_tick=53)
        )

    def test_resonance_without_gcd_reading_falls_back_to_third_tick(self):
        phase = PhaseOne()
        phase.observe([dict(event(1, HOLD_STILL), received_tick=50)])
        timers = {"Resonance": 0}
        self.assertIsNone(
            phase.next_action(SECOND_NS, timers, None, Prayer.MELEE, received_tick=54)
        )
        self.assertEqual(
            phase.next_action(SECOND_NS, timers, None, Prayer.MELEE, received_tick=53).ability,
            "Resonance",
        )
        self.assertIsNone(
            phase.next_action(SECOND_NS, {"Resonance": 3000}, None, Prayer.MELEE, received_tick=53)
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

    def test_vanished_font_ends_duty(self):
        duty = FontDuty()
        duty.update(fonts(22896, 7500), 10)
        self.assertEqual(duty.update([], 11), (None, "font_lost"))
        self.assertFalse(duty.active)


BeamBlocker = controller["BeamBlocker"]
beam_target = controller["beam_target"]


class BeamTests(unittest.TestCase):
    def test_target_is_stream_tile_against_telos(self):
        # 250% P2 death (telos-20261114T174445): black strip x=7509, y=2456..2465; Telos 5x5
        # at (7507, 2451); player at (7505, 2449).
        strip = [(7509, y) for y in range(2456, 2466)]
        self.assertEqual(beam_target(strip, (7507, 2451), 5, (7505, 2449)), (7509, 2456))
        self.assertIsNone(beam_target([], (7507, 2451), 5, (7505, 2449)))
        # A strip that stops short of Telos is not followed (300% P1 green, 175142).
        self.assertIsNone(beam_target(strip[3:], (7507, 2451), 5, (7505, 2449)))

    def test_meter_rises_detect_feeding_but_projectile_spend_does_not(self):
        blocker = BeamBlocker()
        # Logged 174445 meter: 0 at the P2 opener, then +3 per tick while the stream fed Telos.
        self.assertFalse(blocker.observe(0, 6618))
        self.assertFalse(blocker.observe(3, 6639))
        self.assertTrue(blocker.observe(6, 6640))
        for tick in range(6641, 6649):
            self.assertTrue(blocker.observe(3 * (tick - 6638), tick))
        # An Onslaught projectile spends 7 while the stream keeps feeding: still fed.
        self.assertTrue(blocker.observe(23, 6649))
        self.assertTrue(blocker.observe(26, 6650))
        # Once blocked, an unfed Onslaught only falls (100% kill: 70, 60, 50 ...).
        blocker.blocked()
        for tick, meter in ((5702, 60), (5706, 50), (5710, 40)):
            self.assertFalse(blocker.observe(meter, tick))

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

    def test_uppercut_and_repeated_events_do_not_flick(self):
        flicker = PrayerFlicker()
        self.assertIsNone(flicker.observe([event(1, UPPERCUT)], 10, 0))
        self.assertIsNone(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 0))
        flicker.observe([event(2, HOLD_STILL)], 10, 0)
        self.assertIsNone(flicker.observe([event(2, HOLD_STILL)], 11, 0))
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 10, 0), Prayer.MELEE)

    def test_magic_attack_selects_deflect_magic(self):
        flicker = PrayerFlicker()
        magic = controller["MAGIC_ATTACK"]
        flicker.observe([event(1, magic)], 30, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 30, 0), Prayer.MAGIC)
        flicker.sent(Prayer.MAGIC, 0)
        self.assertEqual(flicker.next_prayer(Prayer.MAGIC, 31, 1), Prayer.SOUL_SPLIT)
        flicker.sent(Prayer.SOUL_SPLIT, 1)
        flicker.observe([event(2, AUTO)], 31, 2)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 31, 3), Prayer.MELEE)

    def test_phase_three_default_is_deflect_magic(self):
        flicker = PrayerFlicker()
        flicker.default_prayer = Prayer.MAGIC
        flicker.observe([event(1, AUTO)], 40, 0)
        self.assertEqual(flicker.next_prayer(Prayer.SOUL_SPLIT, 40, 0), Prayer.MAGIC)

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
