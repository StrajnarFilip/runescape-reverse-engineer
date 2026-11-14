"""Observe Telos, or run the melee practice controller through phases 1-3.

With --port, keys come from the displayed action bars and the enrage from memory. The operator
wakes Telos unless screenshot points are given for the automatic approach.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rs3_reader.action_bar_keys import displayed_bindings
from rs3_reader.bot.attempt_log import AttemptLog
from rs3_reader.native import (
    PRAYER_POINTS_MASK,
    AbilityFlags,
    Camera,
    EquipmentMatch,
    NPCFlags,
    Position,
    Reader,
    Reason,
    ScreenPoint,
    SpatialFlags,
    Status,
    Variable,
)
from rs3_reader.prayer import Prayer
from rs3_reader.rotation_keys import (
    ArduinoBackend,
    parse_chord,
    resolve_layout_chord,
    window_keyboard_layout,
)
from rs3_reader.telemetry import npc_health_record, player_hit_splats_record
from rs3_reader.telos_prayer import MELEE_AUTO_SEQUENCE, ManualPrayerKeys, telos_actions

ACTIVE_TELOS = 22891
DORMANT_TELOS = 22892
TENDRILS = 28931
TENDRILS_RELEASE = 28932
HOLD_STILL = 28935
UPPERCUT = 28938
PHASE_SHIFT = 21
PHASE_MASK = 7
OPENING_AUTOS = 7
LOOP_AUTOS = 3
HOLD_STILL_FORECAST_WINDOW_NS = 2_400_000_000
ANTICIPATION_MAX_ATTEMPTS = 2
PHASE_COUNT = 4
FONT_SIXTEENTHS = (3, 2, 1)
SIXTEENTHS = 16
MAX_SAMPLE_AGE_NS = 100_000_000
SPECIAL_WINDOW_NS = 3_000_000_000
ACK_TIMEOUT_NS = 1_200_000_000
KEY_HOLD_SECONDS = 0.05
POLL_SECONDS = 0.025
MAX_PRACTICE_SECONDS = 300
ZERO_ENRAGE_HP = 400_000
F12 = 0x7B
APPROACH_TIMEOUT_NS = 30_000_000_000
FOREGROUND_WAIT_SECONDS = 60
ENGAGE_TIMEOUT_NS = 2_000_000_000
REACQUIRE_TIMEOUT_NS = 3_600_000_000
REACQUIRE_CUE_TIMEOUT_NS = 10_000_000_000
# Fallback when the received server tick counter is invalid. One game tick is 0.6 seconds.
TICK_FALLBACK_NS = 600_000_000
# Action bar 9 keys observed on 2026-11-14: F3 Deflect Melee, F4 Soul Split. Ctrl+F3 did not
# change the overhead in that session.
DEFAULT_FLICK_MELEE_KEY = "F3"
DEFAULT_FLICK_SOUL_SPLIT_KEY = "F4"
DEFAULT_FLICK_MAGIC_KEY = "F1"
# The account owner identified 28959 as a Telos magic attack. Its type 139 hits rose from 1,139
# to 3,828 in P2 at 250% while Deflect Melee was flicked (2026-11-14).
MAGIC_ATTACK = 28959
# The RuneScape Wiki strategy page names the P2 magic barrage Magical Onslaught: projectiles
# grow in damage, matching the rising 28959 hits. Each projectile repeats the animation.
ONSLAUGHT = MAGIC_ATTACK
FLICK_PRAYER_BY_ANIMATION = {MAGIC_ATTACK: Prayer.MAGIC}
# Next special per phase, from the RuneScape Wiki rotation (guide-derived; P1 matches the live
# captures). P2: Onslaught, Hold Still, Black Virus, Uppercut, Tendrils, each after 3 autos.
# The virus has no mapped animation, so Hold Still is followed directly by Uppercut here.
NEXT_SPECIAL_BY_PHASE = {
    1: {TENDRILS: UPPERCUT, UPPERCUT: HOLD_STILL, HOLD_STILL: TENDRILS},
    2: {ONSLAUGHT: HOLD_STILL, HOLD_STILL: UPPERCUT, UPPERCUT: TENDRILS, TENDRILS: ONSLAUGHT},
    # P3: Red Virus, a once-per-phase spec skip, Uppercut, Hold Still. Only the last two have
    # mapped animations, so Hold Still is forecast after Uppercut.
    3: {UPPERCUT: HOLD_STILL, HOLD_STILL: UPPERCUT},
    # P4 (wiki): Uppercut, Weak Anima Bomb, Hold Still. The bomb has no mapped animation, so
    # no forecast follows Uppercut here; an unforecast stun is broken by Freedom.
    4: {UPPERCUT: None, HOLD_STILL: UPPERCUT},
    # P5 (wiki): golems, viruses and the anima bomb; its specials are not mapped yet.
    5: {UPPERCUT: None, HOLD_STILL: None},
}
HANDLED_PHASES = (1, 2, 3, 4, 5)
# Deflect used for unmapped animations: P3 and P5 are magic only (wiki, account owner); P4
# autos are melee within reach and magic otherwise, and 28959 still selects Deflect Magic.
DEFAULT_FLICK_PRAYER_BY_PHASE = {
    1: Prayer.MELEE,
    2: Prayer.MELEE,
    3: Prayer.MAGIC,
    4: Prayer.MELEE,
    5: Prayer.MAGIC,
}
# run() result asking main() to start over for a new encounter (scene or Telos changed).
ENCOUNTER_RESET = 3
# Hold Still ends with a typeless stomp that only hurts a player inside Telos's footprint
# (account owner, 2026-11-14; up to 9,900 at 500%). If the player is still underneath during
# these received ticks after 28935, Dive to a tile this far from Telos's centre.
# Telos relocated at +3 in the 500% capture of 2026-11-14 and the stomp follows his new position
# at +8/+9, so the check starts once he has moved (and after Resonance's +2..+4 slot: a click
# right after queuing Resonance coincided with Resonance never casting).
STOMP_CHECK_TICKS = (4, 7)
# The Dive press and the click are separated so the client is in targeting mode first; a
# Dive that leaves the tile unchanged after this many ticks falls back to walking there.
DIVE_SELECT_SECONDS = 0.15
DIVE_RANGE_TILES = 10
STOMP_TARGET_ATTEMPTS = 4
STOMP_DIVE_CONFIRM_TICKS = 2
# The stomp reaches beyond the 5x5 body: players 4 tiles (Chebyshev) from Telos's centre took
# 9,322 at 500% and 5,401 at 500% P3 (2026-11-14). Treat 4 tiles from the centre as unsafe and
# Dive to 6 tiles out.
STOMP_RADIUS_TILES = 4
STOMP_ESCAPE_DISTANCE = 6
EIGHT_DIRECTIONS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def under_footprint(boss_tile, size, tile):
    """Whether tile lies in the size x size footprint whose south-west corner is boss_tile."""
    return (
        boss_tile[0] <= tile[0] < boss_tile[0] + size
        and boss_tile[1] <= tile[1] < boss_tile[1] + size
    )


def in_stomp_area(boss_tile, size, tile):
    """Whether tile is within STOMP_RADIUS_TILES (Chebyshev) of Telos's centre tile."""
    centre_x = boss_tile[0] + (size - 1) / 2
    centre_y = boss_tile[1] + (size - 1) / 2
    return max(abs(tile[0] - centre_x), abs(tile[1] - centre_y)) <= STOMP_RADIUS_TILES


def stomp_escape_tiles(boss_tile, size, player_tile):
    """Candidate Dive targets outside the footprint, best first.

    The first direction continues the player's offset from Telos's centre (the side the
    character came from is likely open floor); the rest follow by angular closeness.
    """
    centre_x = boss_tile[0] + (size - 1) / 2
    centre_y = boss_tile[1] + (size - 1) / 2
    offset = (player_tile[0] - centre_x, player_tile[1] - centre_y)
    angle = math.atan2(offset[1], offset[0]) if offset != (0, 0) else 0.0

    def closeness(direction):
        difference = math.atan2(direction[1], direction[0]) - angle
        return abs(math.atan2(math.sin(difference), math.cos(difference)))

    # Grid (Chebyshev) distance: diagonal targets are as far outside the square stomp area
    # as straight ones, and still within Dive's 10-tile reach from a player near the centre.
    return [
        (
            round(centre_x + direction[0] * STOMP_ESCAPE_DISTANCE),
            round(centre_y + direction[1] * STOMP_ESCAPE_DISTANCE),
        )
        for direction in sorted(EIGHT_DIRECTIONS, key=closeness)
    ]


def known_floor_targets(boss_tile, size, player_tile, floor):
    """Known-walkable Dive targets outside the stomp area and within Dive reach, nearest first.

    A direction-only target landed off the platform on 2026-11-14 (account owner), so only
    tiles the player or Telos's body has occupied on the current platform are used.
    """
    candidates = [
        tile
        for tile in floor
        if not in_stomp_area(boss_tile, size, tile)
        and max(abs(tile[0] - player_tile[0]), abs(tile[1] - player_tile[1])) <= DIVE_RANGE_TILES
    ]
    return sorted(
        candidates,
        key=lambda tile: (
            max(abs(tile[0] - player_tile[0]), abs(tile[1] - player_tile[1])),
            tile,
        ),
    )


def project_tile(reader, tile, plane, fine_height):
    """Physical screen pixel of a tile at the given ground height, or None.

    Matches rs3_project_actor for the player's own tile (2026-11-14, Compatibility Mode).
    """
    library = reader.library
    library.rs3_project_tile.argtypes = [
        ctypes.POINTER(Camera),
        ctypes.POINTER(Position),
        ctypes.c_float,
        ctypes.POINTER(ScreenPoint),
        ctypes.c_uint32,
    ]
    library.rs3_project_tile.restype = ctypes.c_int32
    position = Position(1, tile[0], tile[1], plane)
    point = ScreenPoint()
    result = library.rs3_project_tile(
        ctypes.byref(reader.spatial.camera),
        ctypes.byref(position),
        ctypes.c_float(fine_height),
        ctypes.byref(point),
        ctypes.sizeof(point),
    )
    return (point.x, point.y) if result == 0 and point.valid else None


# P3 attacks are magic only (wiki; confirmed by the account owner). 28959 repeated every
# 4 received ticks in the 250% P3 capture, so it is counted as the P3 auto there.
PHASE_THREE_AUTOS = frozenset({MAGIC_ATTACK})
# enum rs3_effect_id RS3_EFFECT_STUN and raw_valid_mask bit 0 (include/rs3/effects.h).
STUN_EFFECT_ID = 6
EFFECT_RAW_EXPIRY_VALID = 1
# Stuns last a few seconds; a longer expiry in 20 ms logic ticks is not treated as a stun.
MAX_STUN_TICKS = 1_500
P3_OPENING_AUTOS = 7
P3_LONG_OPENING_AUTOS = 10
# P3 opening by the P2 special that would have come next (wiki). Live at 500% on 2026-11-14:
# P2 ended on Tendrils, and P3 opened with 28930, seven 28959 magic autos, then Hold Still.
# The other rows are guide-derived. After Onslaught the Red Virus has no mapped animation.
P3_CARRY_OVER = {
    ONSLAUGHT: (HOLD_STILL, P3_OPENING_AUTOS),
    HOLD_STILL: (None, P3_OPENING_AUTOS),
    UPPERCUT: (UPPERCUT, P3_LONG_OPENING_AUTOS),
    TENDRILS: (UPPERCUT, P3_LONG_OPENING_AUTOS),
}
# Four 2026-11-14 captures: a P1 ending on Uppercut opened P2 with Hold Still, on Tendrils with
# Onslaught, on Hold Still with Tendrils. 28930 opens P2 and is counted as an auto: one capture
# had only one animated melee auto between it and Hold Still.
P2_CARRY_OVER = {UPPERCUT: ONSLAUGHT}
P2_OPENING_ATTACK = 28930
P1_TO_P2_TRANSITION = 28963
FLICK_EXCLUDED_ANIMATIONS = frozenset({UPPERCUT})
DEFAULT_FLICK_HOLD_TICKS = 1
# After the Tendrils release an unanimated melee auto followed at +2 received ticks in both
# 100% captures (1,470 and 1,076). Melee stays active through that tick.
FLICK_HOLD_TICKS = {TENDRILS_RELEASE: 3}
# Uppercut's 2,634 hitmark appeared 7 received ticks after 28938 on 2026-11-14 at 100%.
# Escape is sent two to three ticks before that hitmark; the server hit deadline is unmeasured.
UPPERCUT_ESCAPE_TICKS = (4, 5)
# Hold Still lands a smaller melee hit at +1 or +2 received ticks and a 4,000+ melee hit at +4
# (250%: 1,094 then 4,384; 500%: blocked 0 at +1 then 8,612). Resonance must miss the first
# and absorb the second. Resonance shares the GCD with Revolution, which takes every free slot
# (one every 3 ticks), and a press made after Revolution fires waits for the next slot: at 500%
# presses with 1,780 ms of GCD left at +2 and +3 never cast before the +4 hit. The press is
# therefore queued during the GCD so that Resonance takes the slot that falls in +2..+4.
RESONANCE_FIRST_HIT_TICK = 2
RESONANCE_LATEST_TICK = 3
RESONANCE_SLOT_TICKS = (2, 4)
# In the 500% P1 Hold Still the queued press cast at +3 and the +4 hit healed 4,195. In P2 the
# same queued press did not cast when the slot freed, so one immediate retry follows.
RESONANCE_MAX_ATTEMPTS = 2
TICK_MS = 600
UPPERCUT_ESCAPE_FALLBACK_NS = (2_400_000_000, 3_000_000_000)
MOVEMENT_ABILITIES = frozenset({"Surge", "Escape"})
MOVEMENT_WINDOWS = {
    UPPERCUT: (3_600_000_000, 4_000_000_000, "Uppercut movement trial"),
    HOLD_STILL: (2_900_000_000, 5_000_000_000, "Hold Still landing movement trial"),
}


class FocusLost(RuntimeError):
    """RuneScape lost foreground focus during a key press; input pauses, the run continues."""


@dataclass(frozen=True)
class Action:
    """A single ability chosen using the current special and sampled readiness."""

    ability: str
    reason: str


class PhaseOne:
    """Track confirmed animation applications and prepare for the next special."""

    def __init__(self, phase=1, carried_expected=None):
        self.next_special = NEXT_SPECIAL_BY_PHASE[phase]
        self.auto_sequences = {MELEE_AUTO_SEQUENCE}
        if phase == 2:
            self.auto_sequences.add(P2_OPENING_ATTACK)
        elif phase >= 3:
            self.auto_sequences |= PHASE_THREE_AUTOS
        self.autos = 0
        self.last_auto_ns = None
        self.last_auto_event = None
        # P1 opens with 7 autos then Tendrils. P2 opens with the special P1 would have used
        # next, except that a P1 ending on Tendrils leads to Onslaught instead of Uppercut.
        self.opening = phase == 1
        self.opening_autos = OPENING_AUTOS
        if phase == 1:
            self.expected = TENDRILS
        elif phase == 2:
            self.expected = P2_CARRY_OVER.get(carried_expected, carried_expected)
        elif phase >= 4:
            # Unmapped openings: forecasts resume once a special has been observed.
            self.expected = None
        else:
            # P3 opens with magic autos, then the special carried over from P2.
            self.opening = True
            self.expected, self.opening_autos = P3_CARRY_OVER.get(
                carried_expected, (None, OPENING_AUTOS)
            )
        self.last_freedom_stun = None
        self.stomp_checked = False
        self.stomp_pending = None
        self.special = None
        self.special_ns = 0
        self.special_event = None
        self.special_tick = None
        self.last_event = 0
        self.pending = None
        self.handled = set()
        self.forecast_handled = False
        self.forecast_attempts = 0
        self.first_hit_tick = None
        self.resonance_attempts = 0
        self.cast_result = None

    @property
    def expected_after(self):
        """Number of ordinary attacks that precede the expected special."""
        return self.opening_autos if self.opening else LOOP_AUTOS

    def observe(self, actions):
        """Apply each new animation once. Treat the Tendrils release as a count offset."""
        for action in actions:
            if action["event_sequence"] <= self.last_event:
                continue
            self.last_event = action["event_sequence"]
            sequence = action["action_sequence"]
            if sequence in self.auto_sequences:
                self.autos += 1
                self.last_auto_ns = action["observed_ns"]
                self.last_auto_event = action["event_sequence"]
            elif sequence == TENDRILS_RELEASE and self.special == TENDRILS:
                self.autos += 1
                self.special = None
            elif sequence in self.next_special:
                self.opening = False
                self.autos = 0
                self.special = sequence
                self.special_ns = action["observed_ns"]
                self.special_event = action["event_sequence"]
                self.special_tick = action.get("received_tick")
                self.handled.clear()
                self.forecast_handled = False
                self.forecast_attempts = 0
                self.first_hit_tick = None
                self.resonance_attempts = 0
                self.stomp_checked = False
                self.stomp_pending = None
                self.expected = self.next_special[sequence]

    def next_action(
        self,
        now,
        cooldowns,
        gcd_remaining_ms,
        prayer,
        *,
        received_tick=None,
        timed_freedom=False,
        flicking=False,
        stun_expiry=None,
    ):
        """Use cooldown acknowledgement and bound the number of attempts for each mechanic.

        With ``flicking``, the melee flicker owns protection, so Anticipation no longer
        requires Deflect Melee to be the current overhead. ``stun_expiry`` is the player's
        active stun timer (None when not stunned); Freedom is used only then, once per stun.
        ``timed_freedom`` is accepted for older callers and no longer changes behavior.
        """
        # Freedom is reactive: only an observed stun justifies it, and it precedes the rest.
        if (
            stun_expiry is not None
            and stun_expiry != self.last_freedom_stun
            and "Freedom" in cooldowns
            and not cooldowns["Freedom"]
        ):
            self.last_freedom_stun = stun_expiry
            return Action("Freedom", "Player stunned")
        # Resonance is tick-critical, so it precedes any acknowledgement still pending.
        if self.resonance_due(cooldowns, received_tick, gcd_remaining_ms):
            return Action("Resonance", "Hold Still second melee hit")
        if self.pending:
            self.cast_result = self.acknowledge(
                cooldowns,
                now,
                ending=self.special == HOLD_STILL and self.pending[0] == "Anticipation",
            )
            if self.cast_result is None:
                return None

        elapsed_ns = now - self.special_ns
        active_special = 0 <= elapsed_ns < SPECIAL_WINDOW_NS
        if (
            self.special == UPPERCUT
            and self.autos == 0
            and "Escape" not in self.handled
            and "Escape" in cooldowns
            and not cooldowns["Escape"]
            and self.uppercut_escape_due(elapsed_ns, received_tick)
        ):
            return Action("Escape", "Uppercut escape")
        movement = MOVEMENT_WINDOWS.get(self.special)
        if movement and self.autos == 0 and "Surge" not in self.handled:
            earliest_ns, latest_ns, reason = movement
            if earliest_ns <= elapsed_ns <= latest_ns and "Surge" in cooldowns:
                if not cooldowns["Surge"]:
                    return Action("Surge", reason)
        # The client queues an ability pressed during the GCD, so Anticipation does not wait for it.
        if (
            self.expected == HOLD_STILL
            # One auto before the stun: the second of three, or the sixth of the P3 opening.
            and self.autos == self.expected_after - 1
            and self.last_auto_ns is not None
            and 0 <= now - self.last_auto_ns < HOLD_STILL_FORECAST_WINDOW_NS
            and (prayer == Prayer.MELEE or flicking)
            and "Anticipation" in cooldowns
            and not cooldowns["Anticipation"]
            and not self.forecast_handled
        ):
            return Action("Anticipation", "Hold Still forecast after Uppercut")
        if gcd_remaining_ms != 0:
            return None
        if (
            active_special
            and self.special == TENDRILS
            and "Hurricane" not in self.handled
            and "Hurricane" in cooldowns
            and not cooldowns["Hurricane"]
        ):
            return Action("Hurricane", "Tendrils damage check")
        return None

    def uppercut_escape_due(self, elapsed_ns, received_tick):
        """Use received ticks after Uppercut when both are valid, else the elapsed-time fallback."""
        if isinstance(received_tick, int) and isinstance(self.special_tick, int):
            earliest, latest = UPPERCUT_ESCAPE_TICKS
            return earliest <= received_tick - self.special_tick <= latest
        earliest_ns, latest_ns = UPPERCUT_ESCAPE_FALLBACK_NS
        return earliest_ns <= elapsed_ns <= latest_ns

    def observe_hits(self, hit_count, received_tick):
        """Record the first player hitmark from Hold Still's +2 hit onwards."""
        if (
            hit_count
            and self.special == HOLD_STILL
            and self.first_hit_tick is None
            and isinstance(received_tick, int)
            and isinstance(self.special_tick, int)
            and received_tick - self.special_tick >= RESONANCE_FIRST_HIT_TICK
        ):
            self.first_hit_tick = received_tick

    def resonance_due(self, cooldowns, received_tick, gcd_remaining_ms=None):
        """Press when the next free GCD slot falls on Hold Still +2..+4.

        A press during the GCD queues Resonance ahead of Revolution for that slot. With a free
        GCD before +2 the press waits, because Revolution takes that early slot and the next
        one is then three ticks later. Without a GCD reading, the earlier rule applies: after
        the +2 hit is seen, or at +3.
        """
        if (
            self.special != HOLD_STILL
            or self.resonance_attempts >= RESONANCE_MAX_ATTEMPTS
            or "Resonance" not in cooldowns
            or cooldowns["Resonance"]
            or not isinstance(received_tick, int)
            or not isinstance(self.special_tick, int)
        ):
            return False
        tick_delta = received_tick - self.special_tick
        earliest_slot, latest_slot = RESONANCE_SLOT_TICKS
        if not 0 <= tick_delta <= latest_slot:
            return False
        if self.resonance_attempts:
            # The queued press did not cast: retry once the slot is free and still in range.
            return gcd_remaining_ms == 0 and earliest_slot <= tick_delta
        if not isinstance(gcd_remaining_ms, int):
            if tick_delta == RESONANCE_FIRST_HIT_TICK:
                return self.first_hit_tick is not None
            return tick_delta == RESONANCE_LATEST_TICK
        slot_delta = tick_delta + math.ceil(gcd_remaining_ms / TICK_MS)
        return earliest_slot <= slot_delta <= latest_slot

    def sent(self, action, now):
        """Wait for the sampled cooldown before treating this command as a cast."""
        if action.ability == "Resonance":
            self.resonance_attempts += 1
            # Never displace another ability's pending acknowledgement.
            if self.pending is not None:
                return
        self.pending = (action.ability, now + ACK_TIMEOUT_NS)
        if action.ability == "Anticipation":
            # An unacknowledged press may be retried while the forecast window is still open.
            self.forecast_attempts += 1
            self.forecast_handled = self.forecast_attempts >= ANTICIPATION_MAX_ATTEMPTS
        else:
            self.handled.add(action.ability)

    def acknowledge(self, timers, now, *, ending=False):
        """Settle an outstanding cooldown when input ends or the phase changes."""
        if self.pending is None:
            return None
        ability, deadline = self.pending
        if (timers.get(ability) or 0) > 0:
            observed = True
        elif now >= deadline:
            observed = False
        elif ending:
            observed = False if self.special == HOLD_STILL and ability == "Anticipation" else None
        else:
            return None
        self.pending = None
        return ability, observed


class PrayerFlicker:
    """Unvalidated practice trial: deflect on each Telos animation, Soul Split after the hold.

    Most animations select Deflect Melee; FLICK_PRAYER_BY_ANIMATION overrides the style.
    Prayer keybinds toggle, so a chord is only requested when the overhead differs from the
    target and no earlier request is still awaiting its overhead acknowledgement.
    """

    def __init__(self, excluded=FLICK_EXCLUDED_ANIMATIONS, hold_ticks=None, prayers=None):
        self.excluded = frozenset(excluded)
        self.hold_ticks = dict(FLICK_HOLD_TICKS if hold_ticks is None else hold_ticks)
        self.prayers = dict(FLICK_PRAYER_BY_ANIMATION if prayers is None else prayers)
        self.last_event = 0
        self.return_tick = None
        self.return_ns = None
        self.hold_prayer = Prayer.MELEE
        # Deflect for animations without an override; P3 switches this to Deflect Magic.
        self.default_prayer = Prayer.MELEE
        self.cue_event = None
        self.requested = None

    def observe(self, actions, tick, now):
        """Hold the cue's deflect for its tick count. Return the latest cue.

        A cue never shortens a longer hold that is already running. The latest cue selects
        the deflect style.
        """
        trigger = None
        for action in actions:
            if action["event_sequence"] <= self.last_event:
                continue
            self.last_event = action["event_sequence"]
            sequence = action["action_sequence"]
            if sequence in self.excluded:
                continue
            trigger = action
            self.hold_prayer = self.prayers.get(sequence, self.default_prayer)
            hold = self.hold_ticks.get(sequence, DEFAULT_FLICK_HOLD_TICKS)
            return_tick = None if tick is None else tick + hold
            return_ns = now + hold * TICK_FALLBACK_NS
            if self.return_ns is None or return_ns > self.return_ns:
                self.return_ns = return_ns
            if return_tick is not None and (
                self.return_tick is None or return_tick > self.return_tick
            ):
                self.return_tick = return_tick
            elif return_tick is None:
                self.return_tick = None
        if trigger is not None:
            self.cue_event = trigger["event_sequence"]
        return trigger

    def holding(self, tick, now):
        """Whether the deflect window opened by the latest cue is still running."""
        if self.return_ns is None:
            return False
        if self.return_tick is not None and tick is not None:
            due = tick >= self.return_tick
        else:
            due = now >= self.return_ns
        if due:
            self.return_tick = self.return_ns = None
        return not due

    def next_prayer(self, prayer, tick, now):
        """Return the next prayer if a switch is needed and no acknowledgement is pending."""
        target = self.hold_prayer if self.holding(tick, now) else Prayer.SOUL_SPLIT
        if self.requested is not None:
            requested, deadline = self.requested
            if prayer != requested and now < deadline:
                return None
            self.requested = None
        return None if prayer == target else target

    def sent(self, target, now):
        """Wait for the overhead to change before another toggle can be sent."""
        self.requested = (target, now + ACK_TIMEOUT_NS)


def log(event, **fields):
    """Write decision and outcome records to standard output."""
    print(json.dumps({"event": event, **fields}), flush=True)


def boss_sample(reader):
    """Read the unique Telos NPC, with independently valid health and phase evidence."""
    info, player, npcs, _ = reader.sample()
    bosses = [
        npc for npc in npcs[: info.npc_count] if npc.config_id in (ACTIVE_TELOS, DORMANT_TELOS)
    ]
    boss = bosses[0] if len(bosses) == 1 else None
    phase_raw = player.variable(Variable.TELOS_PACKED)
    phase = None if phase_raw is None else ((phase_raw >> PHASE_SHIFT) & PHASE_MASK) + 1
    hp = player.variable(Variable.HEALTH)
    return info, player, boss, phase, hp


def active_stun_expiry(reader, logic_tick):
    """Return the player's stun expiry while it lies ahead of the client logic tick, else None.

    The stun candidate (varc 3748) shares the 20 ms logic clock: on 2026-11-14 the effect
    expiries of Anticipation, Resonance and Freedom changed with their casts, and an old stun
    expiry read below the current tick. The effect remains unvalidated in the DLL.
    """
    if logic_tick is None:
        return None
    try:
        stun = reader.effects[STUN_EFFECT_ID - 1]
    except (AttributeError, IndexError, TypeError):
        return None
    if stun.effect_id != STUN_EFFECT_ID or not stun.raw_valid_mask & EFFECT_RAW_EXPIRY_VALID:
        return None
    remaining = (int(stun.raw_expiry) - int(logic_tick)) & 0xFFFFFFFF
    return int(stun.raw_expiry) if 0 < remaining <= MAX_STUN_TICKS else None


def cooldowns(reader, operator_bound=()):
    """Separate valid cooldowns from mapped timers absent in the sparse table.

    An unknown equipment match is accepted for abilities the operator bound explicitly. The
    cache lists Resonance as shield-only, so the DLL reports it as unknown with a two-handed
    weapon; the operator's binding and the later cooldown acknowledgement decide instead.
    """
    timers = {}
    for ability in reader.abilities:
        name = ability.name.decode("utf-8", errors="replace")
        if ability.equipment_match != EquipmentMatch.ALLOWED and not (
            ability.equipment_match == EquipmentMatch.UNKNOWN and name in operator_bound
        ):
            continue
        if ability.flags & AbilityFlags.TIMER_VALID:
            timers[name] = ability.remaining_ms
        elif ability.reason == Reason.NOT_SET:
            timers[name] = None
    return timers


def phase_end_hp(maximum_hp, phase):
    """Boss HP at which the given phase hands off. Phases split maximum HP into quarters.

    The quarter split is observed at 0% enrage only; other enrages assume the same ratio.
    """
    return maximum_hp * (PHASE_COUNT - phase) // PHASE_COUNT


def encounter_checkpoint(phase, health, expected_max_hp=ZERO_ENRAGE_HP):
    """Check the HP band against the phase field and flag possible font progress."""
    current, maximum = health["current"], health["max"]
    if maximum != expected_max_hp or current is None or phase not in (1, 2, 3, 4):
        return None
    # A shared boundary belongs to either neighboring phase during the handoff.
    lower = phase_end_hp(maximum, phase)
    upper = phase_end_hp(maximum, phase - 1)
    if not lower <= current <= upper:
        return None
    font_thresholds = [maximum * part // SIXTEENTHS for part in FONT_SIXTEENTHS]
    fonts_reached = sum(current <= threshold for threshold in font_thresholds) if phase == 4 else 0
    return {"phase": phase, "font_thresholds_reached": fonts_reached, "zero_hp": current == 0}


def check_active_phase(confirmed_phase, phase, health, expected_max_hp, *, saw_p2_transition=False):
    """Corroborate a forward stage with HP or the same actor's P1 handoff animation."""
    if phase not in (confirmed_phase, confirmed_phase + 1):
        raise RuntimeError(f"Unexpected Telos stage change from phase {confirmed_phase} to {phase}")
    if phase == confirmed_phase or encounter_checkpoint(phase, health, expected_max_hp):
        return
    if phase == 2 and saw_p2_transition:
        return
    raise RuntimeError(f"Phase {phase} entry disagrees with the observed boss HP")


def phase_two_melee_cue(actions, overhead):
    """Require a fresh melee animation and protection in the same sample."""
    return overhead == "melee" and any(
        action["action_sequence"] == MELEE_AUTO_SEQUENCE for action in actions
    )


def replay(path):
    """Replay phase 1 counts and retain raw later-phase cues for calibration."""
    encounter = None
    previous_phase = None
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if "telos" in row:
                raw = row["telos_raw"]
                phase = None if raw is None else ((raw >> PHASE_SHIFT) & PHASE_MASK) + 1
                session = [row["pid"], row["process_creation_time"], row["combat"]["scene_epoch"]]
                bosses = [npc for npc in row["telos"] if npc["config_id"] == ACTIVE_TELOS]
                instance = bosses[0]["instance_id"] if len(bosses) == 1 else None
                events = row["telos_actions"]
                sample_ns = row["sample_started_ns"]
                overhead = row["combat"].get("active_prayer")
            elif row.get("event") == "observation":
                phase = row["phase_candidate"]
                session = row["session"]
                instance = (
                    row["boss_instance_id"] if row["boss_config_id"] == ACTIVE_TELOS else None
                )
                events = row["npc_actions"]
                sample_ns = row["sample_ns"]
                overhead = row.get("overhead")
            elif row.get("event") == "telos_absent":
                encounter = None
                continue
            else:
                continue
            if instance is None:
                encounter = None
                continue
            current = (*session, instance)
            if current != encounter:
                phase_one = PhaseOne()
                encounter = current
                previous_phase = None
            if row["npc_action_events_lost"]:
                raise RuntimeError("Replay contains an NPC action queue overflow")
            actions = [
                action
                for action in events
                if action["instance_id"] == instance and action["config_id"] == ACTIVE_TELOS
            ]
            if phase is not None and phase > 1:
                if phase != previous_phase:
                    log("phase_candidate", phase=phase, sample_ns=sample_ns, hp=row["hp"])
                if actions:
                    log(
                        "later_phase_cues",
                        phase=phase,
                        actions=actions,
                        hp=row["hp"],
                        overhead=overhead,
                        sample_ns=sample_ns,
                        protected_melee_cue=phase == 2 and phase_two_melee_cue(actions, overhead),
                    )
                previous_phase = phase
            if previous_phase is not None:
                continue
            if not actions:
                continue
            before = phase_one.autos
            phase_one.observe(actions)
            log(
                "replay",
                sequences=[action["action_sequence"] for action in actions],
                autos_before=before,
                autos=phase_one.autos,
                expected=phase_one.expected,
                expected_after=phase_one.expected_after,
            )


def observe(reader, duration, attempt=None):
    """Record post-transition cues without foreground focus or Arduino input."""
    deadline = time.monotonic() + duration
    last_state = None
    last_status = None
    active_seen = attempt is not None and attempt.engaged
    while time.monotonic() < deadline:
        info, player, boss, phase, hp = boss_sample(reader)
        session = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
        events, lost = reader.drain_npc_action_events()
        hits, hits_lost = reader.drain_player_hit_events()
        if attempt:
            attempt.frame(
                reader,
                info,
                phase=phase,
                actions=events,
                action_loss=lost,
                hits=hits,
                hit_loss=hits_lost,
            )
        if info.status != last_status:
            log("reader_status", status=info.status, reason=info.message.decode(errors="replace"))
            last_status = info.status
        if boss is not None:
            active_seen |= boss.config_id == ACTIVE_TELOS
            actions = telos_actions(events, session, info.sequence, {boss.instance_id})
            tile = reader.position
            # Later phases add actors. Preserve their provenance until their roles are verified.
            other_npcs = (
                [
                    (npc.instance_id, npc.config_id, npc.name.decode(errors="replace"))
                    for npc in reader.npcs[: info.npc_count]
                    if npc.instance_id != boss.instance_id
                ]
                if phase is not None and phase >= 2
                else []
            )
            health = npc_health_record(boss)
            state = dict(
                session=session,
                boss_config_id=boss.config_id,
                boss_instance_id=boss.instance_id,
                boss_health=health,
                telos_raw=player.variable(Variable.TELOS_PACKED),
                phase_candidate=phase,
                checkpoint_candidate=(
                    encounter_checkpoint(phase, health, health["max"])
                    if boss.config_id == ACTIVE_TELOS
                    else None
                ),
                hp=hp,
                other_npcs=other_npcs,
                overhead=Prayer(reader.active_prayer()).name.lower(),
                tile=(tile.x, tile.y, tile.plane) if tile.valid else None,
            )
            if state != last_state or actions or hits or lost or hits_lost:
                log(
                    "observation",
                    sample_ns=info.monotonic_ns,
                    sample_sequence=info.sequence,
                    sample_status=info.status,
                    npc_actions=actions,
                    npc_action_events_lost=lost,
                    player_hits=player_hit_splats_record(reader, hits, hits_lost),
                    **state,
                )
            last_state = state
        elif last_state is not None:
            log("telos_absent", session=session, sample_ns=info.monotonic_ns)
            last_state = None
        if attempt and active_seen:
            if hp == 0:
                return "player_hp_zero"
            if (
                info.status in (Status.AVAILABLE, Status.PARTIAL)
                and info.reason != Reason.READ_FAILED
            ):
                if boss is None or boss.config_id == DORMANT_TELOS:
                    return "telos_disappeared"
        time.sleep(POLL_SECONDS)
    return "time_limit" if attempt else 0


def run(reader, args, board, attempt=None):
    """Track an attached practice encounter and dispatch only from fresh native state."""
    from game_console import GameWindow, frame_target, move_pointer

    emit = attempt.write if attempt else log
    keys = ManualPrayerKeys()
    phase_one = PhaseOne()
    initial, _, _, _, _ = boss_sample(reader)
    if initial.status not in (Status.AVAILABLE, Status.PARTIAL):
        reason = initial.message.decode(errors="replace")
        raise RuntimeError(f"Cannot start in the current client scene ({reason})")
    identity = (initial.pid, initial.process_creation_time, reader.combat.scene_epoch)
    manual_engage = getattr(args, "manual_engage", False)
    window = keys.game_window(identity)
    if not window:
        # Started from a terminal: wait (without input) for the operator to click into RuneScape.
        log("waiting_for_foreground")
        while not window:
            if keys.get_async_key_state(F12) < 0:
                raise RuntimeError("F12 stop")
            time.sleep(POLL_SECONDS)
            window = keys.game_window(identity)

    def check_focus():
        """Inside a key press: stop the press (tap releases held keys) if focus is gone."""
        if keys.get_async_key_state(F12) < 0:
            raise RuntimeError("F12 stop")
        if keys.game_window(identity) != window:
            raise FocusLost("RuneScape lost foreground focus")

    input_allowed = True

    def update_focus():
        """Allow input only while RuneScape is focused; observation never pauses.

        While unfocused, animations, hits and phases are still tracked so the rotation counts
        stay correct; send_chord and the stomp dodge simply do nothing. Only F12 stops.
        """
        nonlocal input_allowed
        if keys.get_async_key_state(F12) < 0:
            raise RuntimeError("F12 stop")
        focused = keys.game_window(identity) == window
        if focused != input_allowed:
            emit(
                "input_resumed" if focused else "input_paused",
                **({} if focused else {"reason": "RuneScape lost foreground focus"}),
            )
            input_allowed = focused

    pointer_window = None
    # Tiles known to be floor on the current platform: player tiles and Telos's body.
    floor_tiles = set()
    floor_phase = [None]

    def remember_floor(phase, boss):
        """Collect walkable tiles for the stomp dodge (only needed when Dive is bound)."""
        if "Dive" not in bindings:
            return
        if phase != floor_phase[0]:
            floor_tiles.clear()  # Each phase is a different platform.
            floor_phase[0] = phase
        player = reader.spatial.player
        if player.valid_mask & SpatialFlags.TILE:
            floor_tiles.add((player.tile.x, player.tile.y))
        for pose in reader.npc_spatial[: reader.spatial.npc_count]:
            if pose.instance_id == boss.instance_id and pose.valid_mask & SpatialFlags.TILE:
                size = pose.footprint_size or 1
                for dx in range(size):
                    for dy in range(size):
                        floor_tiles.add((pose.tile.x + dx, pose.tile.y + dy))

    def stomp_dodge(phase_one, boss, received_tick, timers):
        """Dive out from under Telos before the Hold Still stomp, once per Hold Still."""
        nonlocal pointer_window
        if (
            not input_allowed
            or "Dive" not in bindings
            or phase_one.special != HOLD_STILL
            or phase_one.stomp_checked
            or not isinstance(received_tick, int)
            or not isinstance(phase_one.special_tick, int)
        ):
            return
        delta = received_tick - phase_one.special_tick
        if delta < STOMP_CHECK_TICKS[0]:
            return
        if delta > STOMP_CHECK_TICKS[1]:
            phase_one.stomp_checked = True
            if phase_one.stomp_pending:
                emit("stomp_dodge_result", moved=False, **phase_one.stomp_pending)
                phase_one.stomp_pending = None
            return
        player = reader.spatial.player
        pending = phase_one.stomp_pending
        if pending is not None:
            # Confirm the Dive moved the player; otherwise walk to the same tile once.
            if not player.valid_mask & SpatialFlags.TILE:
                return
            current = (player.tile.x, player.tile.y)
            if current != tuple(pending["player_tile"]):
                emit("stomp_dodge_result", moved=True, tile=current, **pending)
                phase_one.stomp_pending = None
                phase_one.stomp_checked = True
            elif (
                delta - pending["received_tick_delta"] >= STOMP_DIVE_CONFIRM_TICKS
                and not pending["walked"]
            ):
                pending["walked"] = True
                walked = click_point(tuple(pending["screen_point"]), select_dive=False)
                emit("stomp_walk_fallback", sent=walked, target=pending["target"])
            return
        pose = next(
            (
                p
                for p in reader.npc_spatial[: reader.spatial.npc_count]
                if p.instance_id == boss.instance_id
            ),
            None,
        )
        if (
            pose is None
            or not pose.valid_mask & SpatialFlags.TILE
            or not player.valid_mask & SpatialFlags.TILE
            or not player.valid_mask & SpatialFlags.FINE_POSITION
        ):
            return  # Positions unavailable this sample; try again within the window.
        boss_tile = (pose.tile.x, pose.tile.y)
        player_tile = (player.tile.x, player.tile.y)
        size = pose.footprint_size or 1
        centre_distance = max(
            abs(player_tile[0] - (boss_tile[0] + (size - 1) / 2)),
            abs(player_tile[1] - (boss_tile[1] + (size - 1) / 2)),
        )
        if not in_stomp_area(boss_tile, size, player_tile):
            phase_one.stomp_checked = True
            emit(
                "stomp_check",
                in_area=False,
                player_tile=player_tile,
                boss_tile=boss_tile,
                centre_distance=centre_distance,
                received_tick_delta=delta,
            )
            return
        if timers.get("Dive"):
            return  # Dive still cooling down; check again next sample.
        targets = known_floor_targets(boss_tile, size, player_tile, floor_tiles)
        if not targets:
            emit("stomp_dodge_skipped", reason="No known floor tile outside the stomp area")
            phase_one.stomp_checked = True
            return
        for target in targets[:STOMP_TARGET_ATTEMPTS]:
            point = project_tile(reader, target, player.tile.plane, player.fine_height)
            if point is None:
                continue
            sent = click_point(point, select_dive=True)
            if sent is None:
                continue  # Pointer could not reach this target; try the next direction.
            record = {
                "player_tile": player_tile,
                "boss_tile": boss_tile,
                "footprint": size,
                "centre_distance": centre_distance,
                "target": target,
                "screen_point": point,
                "received_tick_delta": delta,
                "walked": False,
            }
            emit("stomp_dodge", sent=sent, **record)
            if sent:
                phase_one.stomp_pending = record
            else:
                phase_one.stomp_checked = True
            return

    def click_point(point, *, select_dive):
        """Move the pointer to a physical pixel and left-click, after Dive if requested.

        Returns True when sent, False when focus was lost, None when the pointer could not be
        placed (window moved, pixel covered). Only F12 propagates.
        """
        nonlocal pointer_window
        try:
            if pointer_window is None:
                pointer_window = GameWindow()
            move_pointer(board, pointer_window, pointer_window.bounds(), point)
            if select_dive:
                # P only selects Dive; the left click on the pointed tile performs it.
                if not send_chord(bindings["Dive"]):
                    return False
                time.sleep(DIVE_SELECT_SECONDS)
            pointer_window.check_point(*pointer_window.cursor())
            board.command("CLICK L", check_focus)
        except FocusLost:
            emit("input_interrupted", reason="RuneScape lost foreground focus")
            return False
        except RuntimeError as error:
            if "F12" in str(error):
                raise
            emit("stomp_dodge_skipped", reason=str(error), screen_point=point)
            return None
        return True

    def send_chord(chord):
        """Press a chord; return False when RuneScape is unfocused or focus was lost mid-press."""
        if not input_allowed:
            return False
        try:
            board.tap(chord, KEY_HOLD_SECONDS, check_focus, time.sleep)
        except FocusLost:
            emit("input_interrupted", reason="RuneScape lost foreground focus")
            return False
        return True

    game = None
    if not manual_engage:
        game = GameWindow()
        if args.approach == args.melee:
            raise ValueError("Approach and Deflect Melee require different screenshot points")
        # Validate both points before changing the character's overhead.
        frame_target(game, args.frame, *args.approach)
        frame_target(game, args.frame, *args.melee)

    def click_frame(point, expected_boss, *, require_melee=False):
        bounds, target = frame_target(game, args.frame, *point)
        check_focus()
        move_pointer(board, game, bounds, target)
        if game.bounds() != bounds:
            raise RuntimeError("RuneScape moved while the pointer was positioning")
        game.check_point(*game.cursor())
        latest, latest_player, current_boss, _, latest_hp = boss_sample(reader)
        if (
            (latest.pid, latest.process_creation_time, reader.combat.scene_epoch) != identity
            or latest.status not in (Status.AVAILABLE, Status.PARTIAL)
            or not 0 <= reader.monotonic_ns() - latest.monotonic_ns <= MAX_SAMPLE_AGE_NS
            or latest_player.clock_stalled_ns > MAX_SAMPLE_AGE_NS
            or latest_hp is None
            or latest_hp <= 0
            or current_boss is None
            or current_boss.config_id != DORMANT_TELOS
            or current_boss.instance_id != expected_boss
        ):
            raise RuntimeError("Dormant encounter changed while positioning the pointer")
        current_prayer = Prayer(reader.active_prayer())
        if require_melee and current_prayer != Prayer.MELEE:
            raise RuntimeError("Deflect Melee changed before the approach click")
        if not require_melee and current_prayer == Prayer.MELEE:
            return
        prayer_points = latest_player.variable(Variable.PRAYER_PACKED)
        if prayer_points is None or not prayer_points & PRAYER_POINTS_MASK:
            raise RuntimeError("Prayer points unavailable before the encounter click")
        if game.bounds() != bounds:
            raise RuntimeError("RuneScape moved before the encounter click")
        game.check_point(*game.cursor())
        board.command("CLICK L", check_focus)

    labels = {"Freedom": args.freedom, "Anticipation": args.anticipation}
    if args.hurricane:
        labels["Hurricane"] = args.hurricane
    if args.surge:
        labels["Surge"] = args.surge
    if getattr(args, "escape", None):
        labels["Escape"] = args.escape
    if getattr(args, "resonance", None):
        labels["Resonance"] = args.resonance
    if getattr(args, "dive", None):
        labels["Dive"] = args.dive
    if args.reacquire:
        labels["Target Cycle"], labels["Opening Attack"] = args.reacquire
    layout = None
    if getattr(args, "displayed_keys", False):
        layout, scan_for_key = window_keyboard_layout(window)
        bindings = {
            ability: resolve_layout_chord(label, scan_for_key) for ability, label in labels.items()
        }
    else:
        bindings = {ability: parse_chord(label) for ability, label in labels.items()}
    if len(set(bindings.values())) != len(bindings):
        raise ValueError("Combat abilities require distinct bindings")
    prayer_bindings = {parse_chord(f"Ctrl+F{number}") for number in range(1, 5)}
    if any(
        binding in prayer_bindings or binding[-1].virtual_key == F12
        for binding in bindings.values()
    ):
        raise ValueError("Combat bindings must exclude prayer chords and F12")
    flicker = PrayerFlicker() if getattr(args, "flick_melee", False) else None
    prayer_chords = {
        Prayer.MELEE: parse_chord(
            getattr(args, "flick_melee_key", None) or DEFAULT_FLICK_MELEE_KEY
        ),
        Prayer.MAGIC: parse_chord(
            getattr(args, "flick_magic_key", None) or DEFAULT_FLICK_MAGIC_KEY
        ),
        Prayer.SOUL_SPLIT: parse_chord(
            getattr(args, "flick_soul_split_key", None) or DEFAULT_FLICK_SOUL_SPLIT_KEY
        ),
    }
    if flicker is not None and (
        len(set(prayer_chords.values())) != len(prayer_chords)
        or set(prayer_chords.values()) & set(bindings.values())
        or any(chord[-1].virtual_key == F12 for chord in prayer_chords.values())
    ):
        raise ValueError("Flick prayer keys must be distinct from each other, abilities and F12")

    def received_tick_counter():
        tick = reader.encounter.game_tick
        return tick.counter if tick.valid else None

    def flick_step(actions, prayer, now, sample_ns):
        """Advance the melee flicker and send at most one prayer chord."""
        tick = received_tick_counter()
        cue = flicker.observe(actions, tick, now)
        if cue is not None:
            emit(
                "prayer_flick_cue",
                animation=cue["action_sequence"],
                received_tick=tick,
                cue_native_sequence=cue["event_sequence"],
            )
        target = flicker.next_prayer(prayer, tick, now)
        if target is None:
            return
        if reader.monotonic_ns() - sample_ns > MAX_SAMPLE_AGE_NS:
            return  # Retried from the next fresh sample.
        if not send_chord(prayer_chords[target]):
            return
        sent_ns = reader.monotonic_ns()
        flicker.sent(target, sent_ns)
        emit(
            "prayer_flick_sent",
            prayer=target.name.lower(),
            previous=prayer.name.lower(),
            received_tick=tick,
            cue_native_sequence=flicker.cue_event,
        )

    if attempt:
        attempt.write(
            "configured_abilities",
            displayed_bindings=labels if layout else None,
            bindings={
                ability: "+".join(key.name for key in chord) for ability, chord in bindings.items()
            },
        )
    # The controller runs until the player dies or F12; --duration is an optional bound.
    duration = getattr(args, "duration", None)
    deadline = time.monotonic() + duration if duration else math.inf
    initiated = False
    active_instance = None
    p2_transition_cue = None
    practice_enrage = getattr(args, "practice_enrage", 0)
    expected_max_hp = ZERO_ENRAGE_HP if practice_enrage == 0 else None
    engaged_at = 0
    phase_one_started = False
    next_prayer_check = 0
    next_approach = 0
    previous_tile = None
    previous_prayer = None
    pending_cue = None
    surge_origin = None
    surge_sent_ns = None
    surge_cue = None
    phase_two_at = None
    phase_three_at = None
    phase_four_at = {}
    reacquire_at = None
    reacquire_hp = None
    warned = set()

    def warn_once(reason):
        if reason not in warned:
            warned.add(reason)
            emit("waiting", reason=reason)

    while time.monotonic() < deadline:
        update_focus()
        info, player, boss, phase, hp = boss_sample(reader)
        events, lost = reader.drain_npc_action_events()
        hits, hits_lost = reader.drain_player_hit_events()
        if attempt:
            attempt.frame(
                reader,
                info,
                phase=phase,
                actions=events,
                action_loss=lost,
                hits=hits,
                hit_loss=hits_lost,
            )
        if (info.pid, info.process_creation_time) != identity[:2]:
            # A restarted client has nothing left to control.
            raise RuntimeError("Client process changed")
        if reader.combat.scene_epoch != identity[2]:
            emit("encounter_reset", reason="Scene changed")
            return ENCOUNTER_RESET
        now = reader.monotonic_ns()
        if (
            info.status not in (Status.AVAILABLE, Status.PARTIAL)
            or not (0 <= now - info.monotonic_ns <= MAX_SAMPLE_AGE_NS)
            or player.clock_stalled_ns > MAX_SAMPLE_AGE_NS
        ):
            # Never act on stale data; the next fresh sample continues.
            time.sleep(POLL_SECONDS)
            continue
        if hp == 0:
            raise RuntimeError("Player HP reached zero")
        if hp is None or hp < 0:
            time.sleep(POLL_SECONDS)
            continue
        if lost or hits_lost:
            # Counts may be off by the lost events; specials re-synchronise the rotation.
            emit("event_queue_overflow", npc_actions_lost=lost, player_hits_lost=hits_lost)
        prayer = Prayer(reader.active_prayer())
        prayer_raw = player.variable(Variable.PRAYER_PACKED)
        if not initiated and boss is not None and boss.config_id == ACTIVE_TELOS:
            # Started mid-fight: join without the opening count; specials re-synchronise.
            initiated = True
            emit("joined_active_encounter", phase=phase)
        if not initiated and boss is not None:
            if prayer_raw is None or not prayer_raw & PRAYER_POINTS_MASK:
                warn_once("Restore prayer points before entering the encounter")
                time.sleep(POLL_SECONDS)
                continue
            if prayer != Prayer.MELEE:
                if now >= next_prayer_check:
                    sent = True
                    if manual_engage:
                        sent = send_chord(prayer_chords[Prayer.MELEE])
                    else:
                        click_frame(args.melee, boss.instance_id)
                    if sent:
                        emit("baseline_prayer_requested", prayer="melee")
                        # Unacknowledged presses are simply retried after the timeout.
                        next_prayer_check = reader.monotonic_ns() + ACK_TIMEOUT_NS
                time.sleep(POLL_SECONDS)
                continue
        if boss is None or boss.config_id != ACTIVE_TELOS:
            if active_instance is not None:
                emit("encounter_reset", reason="Active Telos disappeared")
                return ENCOUNTER_RESET
            if not initiated and boss is not None and boss.config_id == DORMANT_TELOS:
                if attempt and next_prayer_check:
                    attempt.write("baseline_prayer_acknowledged", prayer="melee")
                initiated = True
                if manual_engage:
                    # The operator walks in; the session duration bounds the wait.
                    emit("waiting_for_engage", overhead=prayer.name.lower())
                else:
                    click_frame(args.approach, boss.instance_id, require_melee=True)
                    next_approach = reader.monotonic_ns() + APPROACH_TIMEOUT_NS
                    emit("approach", overhead=prayer.name.lower())
            if initiated and next_approach and now >= next_approach:
                # The automatic approach did not wake Telos: click again.
                emit("approach_retry")
                initiated = False
                next_approach = 0
            time.sleep(POLL_SECONDS)
            continue

        if active_instance is None:
            active_instance = boss.instance_id
            engaged_at = now
        if boss.instance_id != active_instance:
            emit("encounter_reset", reason="Telos instance changed")
            return ENCOUNTER_RESET

        actions = telos_actions(events, identity, info.sequence, {active_instance})
        if phase in (1, 2) and phase_two_at is None:
            for action in actions:
                if action["action_sequence"] == P1_TO_P2_TRANSITION:
                    p2_transition_cue = action["event_sequence"]
        hp_known = bool(boss.flags & NPCFlags.HEALTH) and boss.health_max > 1
        if hp_known and expected_max_hp is None:
            # Non-zero enrage: lock the first observed maximum for the rest of the attempt.
            expected_max_hp = boss.health_max
            emit("boss_max_hp_locked", boss_max_hp=expected_max_hp, enrage=practice_enrage)
        if hp_known and boss.health_max != expected_max_hp:
            # P5 and enrage scaling can change the maximum: re-lock rather than stop.
            emit("boss_max_hp_locked", boss_max_hp=boss.health_max, previous=expected_max_hp)
            expected_max_hp = boss.health_max
        if phase not in HANDLED_PHASES or not hp_known:
            # Unreadable phase or HP: act again from the next sample that has them.
            time.sleep(POLL_SECONDS)
            continue
        if phase_one_started and phase <= 3:
            confirmed_phase = (
                3 if phase_three_at is not None else 2 if phase_two_at is not None else 1
            )
            try:
                check_active_phase(
                    confirmed_phase,
                    phase,
                    npc_health_record(boss),
                    expected_max_hp,
                    saw_p2_transition=p2_transition_cue is not None,
                )
            except RuntimeError as error:
                warn_once(str(error))
        # Every reached phase is handled; only the P2 target-cycle trial stops input earlier.
        continue_phase_three = args.continue_phase_two
        if flicker is not None and (phase in (1, 2) or (phase >= 3 and continue_phase_three)):
            flicker.default_prayer = DEFAULT_FLICK_PRAYER_BY_PHASE[phase]
            flick_step(actions, prayer, now, info.monotonic_ns)
        if not phase_one_started:
            if hp_known:
                emit("engaged", boss_hp=boss.health_current, phase=phase, player_hp=hp)
                phase_one_started = True
            else:
                phase_one.observe(actions)
                time.sleep(POLL_SECONDS)
                continue
        if phase >= 4 and continue_phase_three:
            if phase_four_at.get(phase) is None:
                emit(f"phase_{phase}_observed", boss_hp=boss.health_current, player_hp=hp)
                phase_four_at[phase] = now
                pending_cue = None
                # Fonts, golems and bombs are not handled yet; flicks and specials continue.
                phase_one = PhaseOne(phase=phase, carried_expected=phase_one.expected)
        elif phase == 3 and continue_phase_three:
            if phase_three_at is None:
                emit("phase_three_observed", boss_hp=boss.health_current, player_hp=hp)
                phase_three_at = now
                pending_cue = None
                # P3 attacks are magic only, with their own special order.
                phase_one = PhaseOne(phase=3, carried_expected=phase_one.expected)
        elif phase == 3:
            if (
                not args.continue_phase_two
                or encounter_checkpoint(phase, npc_health_record(boss), expected_max_hp) is None
            ):
                raise RuntimeError("Unexpected phase 3 checkpoint")
            emit("phase_three_observed", boss_hp=boss.health_current, player_hp=hp)
            if attempt and phase_one.pending:
                ability, confirmed = phase_one.acknowledge(
                    cooldowns(reader, bindings), now, ending=True
                )
                attempt.write(
                    "cooldown_acknowledgement",
                    ability=ability,
                    observed=confirmed,
                    reason="input_ended",
                    cue_native_sequence=pending_cue,
                )
            if args.observe_transition:
                board.close()
                reason = observe(reader, max(0, deadline - time.monotonic()), attempt)
                if attempt:
                    attempt.end_reason = reason
            return 2
        if phase == 2:
            first_phase_two_sample = phase_two_at is None
            if first_phase_two_sample:
                emit(
                    "phase_two_observed",
                    boss_hp=boss.health_current,
                    phase=phase,
                    player_hp=hp,
                    transition_cue_native_sequence=p2_transition_cue,
                )
                if attempt and phase_one.pending:
                    ability, confirmed = phase_one.acknowledge(
                        cooldowns(reader, bindings), now, ending=True
                    )
                    attempt.write(
                        "cooldown_acknowledgement",
                        ability=ability,
                        observed=confirmed,
                        reason="phase_changed",
                        cue_native_sequence=pending_cue,
                    )
                    pending_cue = None
                phase_two_at = now
                if args.continue_phase_two:
                    # No P1 forecast or pending input is evidence in the new arena.
                    phase_one = PhaseOne(phase=2, carried_expected=phase_one.expected)
            if first_phase_two_sample or actions or hits or hits_lost:
                log(
                    "observation",
                    session=identity,
                    boss_config_id=boss.config_id,
                    boss_instance_id=boss.instance_id,
                    phase_candidate=phase,
                    boss_health=npc_health_record(boss),
                    hp=hp,
                    overhead=prayer.name.lower(),
                    sample_ns=info.monotonic_ns,
                    npc_actions=actions,
                    npc_action_events_lost=lost,
                    player_hits=player_hit_splats_record(reader, hits, hits_lost),
                )
            if args.reacquire:
                if reacquire_at is not None:
                    damage_seen = boss.health_current < reacquire_hp
                    if damage_seen or now - reacquire_at >= REACQUIRE_TIMEOUT_NS:
                        emit(
                            "phase_two_reacquire_result",
                            boss_hp=boss.health_current,
                            damage_observed=damage_seen,
                        )
                    else:
                        time.sleep(POLL_SECONDS)
                        continue
                elif boss.health_current < phase_end_hp(expected_max_hp, 1):
                    emit("phase_two_reacquire_skipped", reason="Boss HP already decreased")
                elif phase_two_melee_cue(actions, prayer.name.lower()):
                    if reader.monotonic_ns() - info.monotonic_ns > MAX_SAMPLE_AGE_NS:
                        time.sleep(POLL_SECONDS)
                        continue
                    if not (
                        send_chord(bindings["Target Cycle"])
                        and send_chord(bindings["Opening Attack"])
                    ):
                        continue
                    reacquire_at = reader.monotonic_ns()
                    reacquire_hp = boss.health_current
                    emit("phase_two_reacquire_sent", boss_hp=reacquire_hp)
                    time.sleep(POLL_SECONDS)
                    continue
                elif now - phase_two_at < REACQUIRE_CUE_TIMEOUT_NS:
                    time.sleep(POLL_SECONDS)
                    continue
                else:
                    emit(
                        "phase_two_reacquire_skipped",
                        reason="No fresh melee cue with Deflect Melee",
                    )
            if not args.continue_phase_two:
                if args.observe_transition:
                    board.close()
                    reason = observe(reader, max(0, deadline - time.monotonic()), attempt)
                    if attempt:
                        attempt.end_reason = reason
                return 2
        # Specials still resolve after boss HP reaches the phase threshold: a 250% Uppercut at
        # 454,018 HP landed after the 450,000 boundary, so responses continue until the phase
        # field changes. Inputs during the transition lockout simply have no effect.
        autos_before = phase_one.autos
        timed_freedom = getattr(args, "timed_freedom", False)
        received_tick = None
        if timed_freedom or "Escape" in bindings or "Resonance" in bindings:
            tick = reader.encounter.game_tick
            if tick.valid:
                received_tick = tick.counter
            actions = [dict(action, received_tick=received_tick) for action in actions]
        phase_one.observe(actions)
        phase_one.observe_hits(len(hits), received_tick)
        position = reader.position
        tile = (position.x, position.y, position.plane) if position.valid else None
        if surge_sent_ns is not None:
            tiles_known = tile is not None and surge_origin is not None
            displaced = tiles_known and tile != surge_origin
            if displaced or now - surge_sent_ns >= ACK_TIMEOUT_NS:
                emit(
                    "surge_displacement",
                    origin=surge_origin,
                    tile=tile,
                    # None: the player tile was unavailable, so displacement is unknown.
                    observed=displaced if tiles_known else None,
                    **({"cue_native_sequence": surge_cue} if attempt else {"sample_ns": now}),
                )
                surge_sent_ns = None
        if actions or hits or hits_lost or tile != previous_tile or prayer != previous_prayer:
            log(
                "combat",
                npc_actions=actions,
                autos_before=autos_before,
                autos=phase_one.autos,
                expected=phase_one.expected,
                expected_after=phase_one.expected_after,
                player_hp=hp,
                boss_hp=boss.health_current,
                phase=phase,
                sample_ns=info.monotonic_ns,
                tile=tile,
                overhead=prayer.name.lower(),
                player_hits=player_hit_splats_record(reader, hits, hits_lost),
            )
            previous_tile, previous_prayer = tile, prayer

        timers = {
            name: value for name, value in cooldowns(reader, bindings).items() if name in bindings
        }
        gcd_remaining_ms = player.gcd_remaining_ms if player.gcd_reason == Reason.NONE else None
        action = phase_one.next_action(
            now,
            timers,
            gcd_remaining_ms,
            prayer,
            received_tick=received_tick,
            timed_freedom=timed_freedom,
            flicking=flicker is not None,
            stun_expiry=active_stun_expiry(reader, getattr(info, "logic_tick", None)),
        )
        if phase_one.cast_result:
            ability, confirmed = phase_one.cast_result
            if attempt:
                attempt.write(
                    "cooldown_acknowledgement",
                    ability=ability,
                    observed=confirmed,
                    cue_native_sequence=pending_cue,
                )
            log("cooldown_acknowledgement", ability=ability, observed=confirmed, sample_ns=now)
            pending_cue = None
            phase_one.cast_result = None
        if action:
            # Movement does not depend on the tile; a missing tile only makes displacement unknown.
            if reader.monotonic_ns() - info.monotonic_ns > MAX_SAMPLE_AGE_NS:
                # Decided from an aged sample: decide again from the next fresh one.
                time.sleep(POLL_SECONDS)
                continue
            pending_cue = (
                phase_one.last_auto_event
                if action.ability == "Anticipation"
                else phase_one.special_event
            )
            if attempt:
                attempt.write(
                    "ability_requested",
                    ability=action.ability,
                    reason=action.reason,
                    cue_native_sequence=pending_cue,
                    # Shows whether a Revolution GCD could delay this press past its target tick.
                    gcd_remaining_ms=gcd_remaining_ms,
                    received_tick=received_tick,
                )
            if not send_chord(bindings[action.ability]):
                # Interrupted by focus loss; the decision is made again after focus returns.
                if action.ability == "Freedom":
                    phase_one.last_freedom_stun = None
                time.sleep(POLL_SECONDS)
                continue
            sent_ns = reader.monotonic_ns()
            phase_one.sent(action, sent_ns)
            if attempt:
                attempt.write(
                    "ability_sent",
                    ability=action.ability,
                    reason=action.reason,
                    sent_ns=attempt.relative_ns(sent_ns),
                    cue_native_sequence=pending_cue,
                )
            if action.ability in MOVEMENT_ABILITIES:
                surge_origin = tile
                surge_sent_ns = sent_ns
                surge_cue = pending_cue
            log("ability_sent", ability=action.ability, reason=action.reason, sent_ns=sent_ns)
        remember_floor(phase, boss)
        stomp_dodge(phase_one, boss, received_tick, timers)
        time.sleep(POLL_SECONDS)
    emit("timeout", active=active_instance is not None)
    return 1


ACTION_BAR_KEY_ARGUMENTS = {
    "freedom": "Freedom",
    "anticipation": "Anticipation",
    "escape": "Escape",
    "resonance": "Resonance",
    "dive": "Dive",
    "flick_melee_key": "Deflect Melee",
    "flick_magic_key": "Deflect Magic",
    "flick_soul_split_key": "Soul Split",
}
FLICK_KEY_FALLBACKS = {
    "flick_melee_key": DEFAULT_FLICK_MELEE_KEY,
    "flick_magic_key": DEFAULT_FLICK_MAGIC_KEY,
    "flick_soul_split_key": DEFAULT_FLICK_SOUL_SPLIT_KEY,
}


def resolve_enrage(reader, args):
    """Use the enrage from VARP 6359 unless --practice-enrage overrides it."""
    reader.sample()
    enrage = reader.encounter.enrage
    observed = enrage.value if enrage.valid else None
    if args.practice_enrage is None:
        if observed is None:
            raise RuntimeError("Telos enrage unavailable in memory; pass --practice-enrage")
        args.practice_enrage = observed
    log("enrage_resolved", enrage=args.practice_enrage, from_memory=observed)


def resolve_action_bar_keys(reader, args, attempt=None):
    """Fill unset key arguments from the displayed action bars and record their source.

    Explicit command-line keys win. Labels are displayed glyphs, so letter keys are translated
    through the RuneScape window's keyboard layout. Freedom and Anticipation are required.
    """
    reader.sample()
    bindings = displayed_bindings(reader)
    sources = {}
    for argument, name in ACTION_BAR_KEY_ARGUMENTS.items():
        if getattr(args, argument, None):
            sources[argument] = "command_line"
        elif name in bindings:
            setattr(args, argument, bindings[name].key_label)
            sources[argument] = f"action_bar {bindings[name].display_bar}:{bindings[name].slot}"
            args.displayed_keys = True
        elif argument in FLICK_KEY_FALLBACKS:
            setattr(args, argument, FLICK_KEY_FALLBACKS[argument])
            sources[argument] = "default"
    missing = [
        ACTION_BAR_KEY_ARGUMENTS[argument]
        for argument in ("freedom", "anticipation")
        if not getattr(args, argument, None)
    ]
    resolved = {argument: getattr(args, argument, None) for argument in ACTION_BAR_KEY_ARGUMENTS}
    log("keys_resolved", keys=resolved, sources=sources)
    if attempt:
        attempt.write("keys_resolved", keys=resolved, sources=sources)
    if missing:
        raise RuntimeError(f"Put {', '.join(missing)} on a displayed action bar or pass its key")


def main():
    """Start with bar 18 active and Practice Mode 0% confirmed in the client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument(
        "--duration",
        type=int,
        help="Optional session limit in seconds; by default only death or F12 ends the run",
    )
    parser.add_argument("--port", help="Arduino COM port. Omit for observation only.")
    parser.add_argument("--replay", type=Path, help="Read a local Telos observation JSONL capture")
    parser.add_argument(
        "--record-attempt",
        action="store_true",
        help="Write an attempt log under training-data/ (always on with --port)",
    )
    parser.add_argument("--frame", type=Path, help="Fresh game_console.py screenshot")
    parser.add_argument(
        "--approach", type=int, nargs=2, help="Arena floor pixel near dormant Telos"
    )
    parser.add_argument("--melee", type=int, nargs=2, help="Deflect Melee action-bar pixel")
    parser.add_argument("--practice-0-confirmed", action="store_true")
    parser.add_argument(
        "--practice-enrage",
        type=int,
        help="Override the enrage read from memory (VARP 6359). Non-zero values lock the first "
        "observed maximum HP and assume the 0%% quarter phase split",
    )
    # Prayer flicking, timed Freedom and post-input observation are always on; these old
    # switches are still accepted so earlier commands keep working.
    parser.add_argument("--observe-transition", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--flick-melee", action="store_true", help=argparse.SUPPRESS)
    # Unset keys are read from the displayed action bars (rs3_reader.action_bar_keys).
    parser.add_argument("--flick-melee-key", help="Deflect Melee key (default: from action bar)")
    parser.add_argument("--flick-magic-key", help="Deflect Magic key (default: from action bar)")
    parser.add_argument("--flick-soul-split-key", help="Soul Split key (default: from action bar)")
    parser.add_argument("--freedom", help="Freedom key (default: from action bar)")
    parser.add_argument("--anticipation", help="Anticipation key (default: from action bar)")
    # Without screenshot points the operator wakes Telos (the default); the old switch remains.
    parser.add_argument("--manual-engage", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--displayed-keys",
        action="store_true",
        help="Translate displayed key labels using the RuneScape keyboard layout",
    )
    parser.add_argument("--hurricane", help="Current Hurricane binding, if equipped on the bar")
    parser.add_argument("--timed-freedom", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--surge", help="Optional Surge binding for experimental movement")
    parser.add_argument(
        "--escape", help="Optional Escape binding, sent 4-5 received ticks after Uppercut"
    )
    parser.add_argument(
        "--dive", help="Dive key, used to leave Telos's footprint before the Hold Still stomp"
    )
    parser.add_argument(
        "--resonance",
        help="Optional Resonance binding, sent after Hold Still's +2 hit to absorb the +4 hit",
    )
    # Phases 2 and 3 are always handled; the old opt-in flag is still accepted as a no-op.
    parser.add_argument("--continue-phase-two", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--reacquire",
        nargs=2,
        metavar=("TARGET_CYCLE", "BASIC_ATTACK"),
        help="Experimental one-shot P2 target cycle and basic attack; input then stops at P2",
    )
    args = parser.parse_args()
    if args.replay and args.port:
        parser.error("Replay does not open the Arduino")
    if args.replay and args.record_attempt:
        parser.error("An attempt log requires a live session")
    # Input continues through every handled phase (to the P4 checkpoint) unless the one-shot
    # target-cycle trial is selected, which needs input to end at P2.
    args.continue_phase_two = not args.reacquire
    args.flick_melee = args.timed_freedom = args.observe_transition = True
    if args.port:
        args.record_attempt = True
    if args.duration is not None and args.duration <= 0:
        parser.error("Duration must be positive")
    approach_points = (args.frame, args.approach, args.melee)
    # The operator wakes Telos unless every screenshot point for the automatic approach is set.
    args.manual_engage = not any(approach_points)
    if args.port and not args.manual_engage and not all(approach_points):
        parser.error("The automatic approach needs --frame, --melee and --approach together")
    board = stream = attempt = None
    end_reason = "error"
    outcome = "incomplete"
    try:
        if args.replay:
            replay(args.replay)
            return 0
        with Reader(pid=args.pid, worker_hz=0) as reader:
            if args.port:
                resolve_enrage(reader, args)
            if args.port or args.record_attempt:
                directory = Path(__file__).resolve().parents[1] / "training-data"
                directory.mkdir(exist_ok=True)
                date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                path = directory / f"telos-{date}.jsonl"
                stream = path.open("x", encoding="utf-8", newline="\n")
                attempt = AttemptLog(
                    stream,
                    mode="practice" if args.port else "observation",
                    enrage=args.practice_enrage,
                    monotonic_ns=reader.monotonic_ns,
                )
                print(f"Attempt log {path}", flush=True)
            if args.port:
                resolve_action_bar_keys(reader, args, attempt)
            if not args.port:
                observed_end = observe(reader, args.duration or MAX_PRACTICE_SECONDS, attempt)
                end_reason = observed_end if attempt else "observation_ended"
                result = 0
            else:
                board = ArduinoBackend(args.port)
                result = run(reader, args, board, attempt)
                # A new scene or Telos instance starts a fresh encounter in the same session.
                while result == ENCOUNTER_RESET:
                    result = run(reader, args, board, attempt)
                end_reason = attempt.end_reason or ("phase_limit" if result == 2 else "time_limit")
            return result
    except KeyboardInterrupt:
        end_reason = "interrupted"
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        log("stopped", reason=str(error))
        if attempt:
            if attempt.engaged and attempt.previous.get("hp") == 0:
                end_reason = "player_hp_zero"
            attempt.write("stopped", reason=str(error))
        return 1
    finally:
        try:
            if board:
                board.close()
        finally:
            if stream:
                with stream:
                    if attempt:
                        if attempt.engaged and attempt.previous.get("hp") == 0:
                            outcome = "player_hp_zero"
                        attempt.write("attempt_ended", result=outcome, reason=end_reason)


if __name__ == "__main__":
    raise SystemExit(main())
