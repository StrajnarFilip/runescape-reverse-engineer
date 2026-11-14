"""Observe Telos or run the phase 1 melee controller in a staged 0% practice instance."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rs3_reader.bot.attempt_log import AttemptLog
from rs3_reader.native import (
    PRAYER_POINTS_MASK,
    AbilityFlags,
    EquipmentMatch,
    NPCFlags,
    Reader,
    Reason,
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
HOLD_STILL_PREP_AUTOS = 2
HOLD_STILL_FORECAST_WINDOW_NS = 2_400_000_000
ANTICIPATION_MAX_ATTEMPTS = 2
PHASE_COUNT = 4
FONT_SIXTEENTHS = (3, 2, 1)
SIXTEENTHS = 16
MAX_SAMPLE_AGE_NS = 100_000_000
SPECIAL_WINDOW_NS = 3_000_000_000
HOLD_STILL_HEAVY_HIT_TICK = 4
HOLD_STILL_LAST_FREEDOM_TICK = 7
ACK_TIMEOUT_NS = 1_200_000_000
KEY_HOLD_SECONDS = 0.05
POLL_SECONDS = 0.025
MAX_PRACTICE_SECONDS = 300
ZERO_ENRAGE_HP = 400_000
F12 = 0x7B
APPROACH_TIMEOUT_NS = 30_000_000_000
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
}
# P3 attacks are magic only (wiki; confirmed by the account owner). 28959 repeated every
# 4 received ticks in the 250% P3 capture, so it is counted as the P3 auto there.
PHASE_THREE_AUTOS = frozenset({MAGIC_ATTACK, 28930})
# Four 2026-11-14 captures: a P1 ending on Uppercut opened P2 with Hold Still, on Tendrils with
# Onslaught, on Hold Still with Tendrils. 28930 opens P2 and is counted as an auto: one capture
# had only one animated melee auto between it and Hold Still.
P2_CARRY_OVER = {UPPERCUT: ONSLAUGHT}
P2_OPENING_ATTACK = 28930
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
        elif phase == 3:
            self.auto_sequences |= PHASE_THREE_AUTOS
        self.autos = 0
        self.last_auto_ns = None
        self.last_auto_event = None
        # P1 opens with 7 autos then Tendrils. P2 opens with the special P1 would have used
        # next, except that a P1 ending on Tendrils leads to Onslaught instead of Uppercut.
        self.opening = phase == 1
        if phase == 1:
            self.expected = TENDRILS
        elif phase == 2:
            self.expected = P2_CARRY_OVER.get(carried_expected, carried_expected)
        else:
            # The P3 opening depends on the P2 ending in ways not yet captured.
            self.expected = None
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
        return OPENING_AUTOS if self.opening else LOOP_AUTOS

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
    ):
        """Use cooldown acknowledgement and bound the number of attempts for each mechanic.

        With ``flicking``, the melee flicker owns protection, so Anticipation no longer
        requires Deflect Melee to be the current overhead.
        """
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
        freedom_window = active_special
        if timed_freedom:
            tick_delta = (
                received_tick - self.special_tick
                if received_tick is not None and self.special_tick is not None
                else None
            )
            freedom_window = (
                tick_delta is not None
                and HOLD_STILL_HEAVY_HIT_TICK <= tick_delta <= HOLD_STILL_LAST_FREEDOM_TICK
                and gcd_remaining_ms == 0
            )
        if (
            freedom_window
            and self.special == HOLD_STILL
            and "Freedom" not in self.handled
            and "Freedom" in cooldowns
            and not cooldowns["Freedom"]
        ):
            reason = "Hold Still post-hit GCD trial" if timed_freedom else "Hold Still animation"
            return Action("Freedom", reason)
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
            and self.autos == HOLD_STILL_PREP_AUTOS
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
        """Return the prayer to request now, or None while it is active or awaiting acknowledgement."""
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
    window = keys.game_window(identity)
    if not window:
        raise RuntimeError("RuneScape must be foreground before starting")

    def check_focus():
        if keys.get_async_key_state(F12) < 0:
            raise RuntimeError("F12 stop")
        if keys.game_window(identity) != window:
            raise RuntimeError("RuneScape lost foreground focus")

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
        Prayer.MELEE: parse_chord(getattr(args, "flick_melee_key", DEFAULT_FLICK_MELEE_KEY)),
        Prayer.MAGIC: parse_chord(getattr(args, "flick_magic_key", DEFAULT_FLICK_MAGIC_KEY)),
        Prayer.SOUL_SPLIT: parse_chord(
            getattr(args, "flick_soul_split_key", DEFAULT_FLICK_SOUL_SPLIT_KEY)
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
            raise RuntimeError("Native sample aged out before the prayer switch")
        board.tap(prayer_chords[target], KEY_HOLD_SECONDS, check_focus, time.sleep)
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
    deadline = time.monotonic() + args.duration
    initiated = False
    active_instance = None
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
    reacquire_at = None
    reacquire_hp = None
    while time.monotonic() < deadline:
        check_focus()
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
        if (info.pid, info.process_creation_time, reader.combat.scene_epoch) != identity:
            raise RuntimeError("Client session changed")
        now = reader.monotonic_ns()
        if (
            info.status not in (Status.AVAILABLE, Status.PARTIAL)
            or not (0 <= now - info.monotonic_ns <= MAX_SAMPLE_AGE_NS)
            or player.clock_stalled_ns > MAX_SAMPLE_AGE_NS
        ):
            raise RuntimeError("Native sample is unavailable or stale")
        if hp is None or hp <= 0:
            raise RuntimeError("Player HP reached zero" if hp == 0 else "Player HP unavailable")
        if lost or hits_lost:
            raise RuntimeError("Combat event queue overflow")
        prayer = Prayer(reader.active_prayer())
        prayer_raw = player.variable(Variable.PRAYER_PACKED)
        if not initiated and boss is not None and boss.config_id == ACTIVE_TELOS:
            raise RuntimeError("Start while Telos is dormant to capture the opening attack count")
        if not initiated and boss is not None:
            if prayer_raw is None or not prayer_raw & PRAYER_POINTS_MASK:
                raise RuntimeError("Restore prayer points before entering the encounter")
            if prayer != Prayer.MELEE:
                if now >= next_prayer_check:
                    if next_prayer_check:
                        raise RuntimeError("Deflect Melee did not activate on the configured slot")
                    click_frame(args.melee, boss.instance_id)
                    emit("baseline_prayer_requested", prayer="melee")
                    next_prayer_check = reader.monotonic_ns() + ACK_TIMEOUT_NS
                time.sleep(POLL_SECONDS)
                continue
        if boss is None or boss.config_id != ACTIVE_TELOS:
            if active_instance is not None:
                raise RuntimeError("Active Telos disappeared")
            if not initiated and boss is not None and boss.config_id == DORMANT_TELOS:
                if attempt and next_prayer_check:
                    attempt.write("baseline_prayer_acknowledged", prayer="melee")
                click_frame(args.approach, boss.instance_id, require_melee=True)
                initiated = True
                next_approach = reader.monotonic_ns() + APPROACH_TIMEOUT_NS
                emit("approach", overhead=prayer.name.lower())
            if initiated and next_approach and now >= next_approach:
                raise RuntimeError("Approach did not wake Telos within the expected interval")
            time.sleep(POLL_SECONDS)
            continue

        if active_instance is None:
            active_instance = boss.instance_id
            engaged_at = now
        if boss.instance_id != active_instance:
            raise RuntimeError("Telos instance changed during the attempt")

        actions = telos_actions(events, identity, info.sequence, {active_instance})
        hp_known = bool(boss.flags & NPCFlags.HEALTH) and boss.health_max > 1
        if hp_known and expected_max_hp is None:
            # Non-zero enrage: lock the first observed maximum for the rest of the attempt.
            expected_max_hp = boss.health_max
            emit("boss_max_hp_locked", boss_max_hp=expected_max_hp, enrage=practice_enrage)
        if hp_known and boss.health_max != expected_max_hp:
            raise RuntimeError(
                f"Expected {expected_max_hp:,} maximum Telos HP, observed {boss.health_max}"
            )
        # Every reached phase is handled; only the P2 target-cycle trial stops input earlier.
        continue_phase_three = args.continue_phase_two
        if flicker is not None and (phase in (1, 2) or (phase == 3 and continue_phase_three)):
            if phase == 3:
                flicker.default_prayer = Prayer.MAGIC
            flick_step(actions, prayer, now, info.monotonic_ns)
        if not phase_one_started:
            if phase == 1 and hp_known:
                emit("engaged", boss_hp=boss.health_current, phase=phase, player_hp=hp)
                phase_one_started = True
            elif now - engaged_at >= ENGAGE_TIMEOUT_NS:
                raise RuntimeError(
                    f"Opening phase or boss HP unavailable (phase={phase}, max={boss.health_max})"
                )
            else:
                phase_one.observe(actions)
                time.sleep(POLL_SECONDS)
                continue
        if phase == 4 and continue_phase_three:
            # Input ends at the P4 checkpoint; fonts and golems have no handling yet.
            emit("phase_four_observed", boss_hp=boss.health_current, player_hp=hp)
            if args.observe_transition:
                board.close()
                reason = observe(reader, max(0, deadline - time.monotonic()), attempt)
                if attempt:
                    attempt.end_reason = reason
            return 2
        if phase not in (1, 2, 3) or not hp_known:
            raise RuntimeError("Encounter phase unavailable or outside the calibrated range")
        if phase == 3 and continue_phase_three:
            if encounter_checkpoint(phase, npc_health_record(boss), expected_max_hp) is None:
                raise RuntimeError("Phase 3 bits disagree with the observed boss HP")
            if phase_three_at is None:
                emit("phase_three_observed", boss_hp=boss.health_current, player_hp=hp)
                phase_three_at = now
                pending_cue = None
                # P3 attacks are magic only, with their own special order.
                phase_one = PhaseOne(phase=3)
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
            if encounter_checkpoint(phase, npc_health_record(boss), expected_max_hp) is None:
                raise RuntimeError("Phase 2 bits disagree with the observed boss HP")
            first_phase_two_sample = phase_two_at is None
            if first_phase_two_sample:
                emit("phase_two_observed", boss_hp=boss.health_current, phase=phase, player_hp=hp)
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
                        raise RuntimeError("Native sample aged out before target cycling")
                    board.tap(bindings["Target Cycle"], KEY_HOLD_SECONDS, check_focus, time.sleep)
                    board.tap(bindings["Opening Attack"], KEY_HOLD_SECONDS, check_focus, time.sleep)
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
                raise RuntimeError("Native sample aged out before submitting the action")
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
            board.tap(bindings[action.ability], KEY_HOLD_SECONDS, check_focus, time.sleep)
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
        time.sleep(POLL_SECONDS)
    emit("timeout", active=active_instance is not None)
    return 1


def main():
    """Start with bar 18 active and Practice Mode 0% confirmed in the client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--duration", type=int, default=MAX_PRACTICE_SECONDS)
    parser.add_argument("--port", help="Arduino COM port. Omit for observation only.")
    parser.add_argument("--replay", type=Path, help="Read a local Telos observation JSONL capture")
    parser.add_argument("--record-attempt", action="store_true", help="Write under training-data/")
    parser.add_argument("--frame", type=Path, help="Fresh game_console.py screenshot")
    parser.add_argument(
        "--approach", type=int, nargs=2, help="Arena floor pixel near dormant Telos"
    )
    parser.add_argument("--melee", type=int, nargs=2, help="Deflect Melee action-bar pixel")
    parser.add_argument("--practice-0-confirmed", action="store_true")
    parser.add_argument(
        "--practice-enrage",
        type=int,
        default=0,
        help="Confirmed Practice Mode enrage. Non-zero values lock the first observed maximum HP "
        "and assume the 0%% quarter phase split; special timings are only calibrated at 0%%",
    )
    parser.add_argument(
        "--observe-transition",
        action="store_true",
        help="Record later cues after phase 1 input stops",
    )
    parser.add_argument(
        "--flick-melee",
        action="store_true",
        help="Unvalidated trial: Deflect Melee (Deflect Magic for 28959) on every Telos "
        "animation except Uppercut, then Soul Split after the hold (phases 1-2)",
    )
    parser.add_argument("--flick-melee-key", default=DEFAULT_FLICK_MELEE_KEY)
    parser.add_argument(
        "--flick-magic-key",
        default=DEFAULT_FLICK_MAGIC_KEY,
        help="Deflect Magic key used for the 28959 magic attack",
    )
    parser.add_argument("--flick-soul-split-key", default=DEFAULT_FLICK_SOUL_SPLIT_KEY)
    parser.add_argument("--freedom", help="Current Freedom binding")
    parser.add_argument("--anticipation", help="Current Anticipation binding")
    parser.add_argument(
        "--displayed-keys",
        action="store_true",
        help="Translate displayed key labels using the RuneScape keyboard layout",
    )
    parser.add_argument("--hurricane", help="Current Hurricane binding, if equipped on the bar")
    parser.add_argument(
        "--timed-freedom",
        action="store_true",
        help="Try Freedom at GCD zero, 4–7 received ticks after Hold Still",
    )
    parser.add_argument("--surge", help="Optional Surge binding for experimental movement")
    parser.add_argument(
        "--escape", help="Optional Escape binding, sent 4-5 received ticks after Uppercut"
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
    if not 0 < args.duration <= MAX_PRACTICE_SECONDS:
        parser.error(f"Duration must be between 1 and {MAX_PRACTICE_SECONDS} seconds")
    if args.port and not all((args.frame, args.approach, args.melee, args.practice_0_confirmed)):
        parser.error(
            "Arduino input requires --practice-0-confirmed, --frame, --melee and --approach"
        )
    if args.port and not (args.freedom and args.anticipation):
        parser.error("Set --freedom and --anticipation from the current action bar")
    board = stream = attempt = None
    end_reason = "error"
    outcome = "incomplete"
    try:
        if args.replay:
            replay(args.replay)
            return 0
        with Reader(pid=args.pid, worker_hz=0) as reader:
            if args.port or args.record_attempt:
                directory = Path(__file__).resolve().parents[1] / "training-data"
                directory.mkdir(exist_ok=True)
                date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                path = directory / f"telos-{date}.jsonl"
                stream = path.open("x", encoding="utf-8", newline="\n")
                attempt = AttemptLog(
                    stream,
                    mode="practice" if args.port else "observation",
                    enrage=args.practice_enrage if args.practice_0_confirmed else None,
                    monotonic_ns=reader.monotonic_ns,
                )
                print(f"Attempt log {path}", flush=True)
            if not args.port:
                observed_end = observe(reader, args.duration, attempt)
                end_reason = observed_end if attempt else "observation_ended"
                result = 0
            else:
                board = ArduinoBackend(args.port)
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
