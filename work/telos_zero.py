"""Observe Telos or run the phase 1 melee controller in a staged 0% practice instance."""

from __future__ import annotations

import argparse
import json
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
PHASE_TWO_HP = 300_000
PHASE_THREE_HP = 200_000
PHASE_FOUR_HP = 100_000
FONT_THRESHOLDS = (75_000, 50_000, 25_000)
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
APPROACH_TIMEOUT_NS = 10_000_000_000
ENGAGE_TIMEOUT_NS = 2_000_000_000
REACQUIRE_TIMEOUT_NS = 3_600_000_000
REACQUIRE_CUE_TIMEOUT_NS = 10_000_000_000
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

    def __init__(self):
        self.autos = 0
        self.last_auto_ns = None
        self.last_auto_event = None
        self.opening = True
        self.expected = TENDRILS
        self.special = None
        self.special_ns = 0
        self.special_event = None
        self.special_tick = None
        self.last_event = 0
        self.pending = None
        self.handled = set()
        self.forecast_handled = False
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
            if sequence == MELEE_AUTO_SEQUENCE:
                self.autos += 1
                self.last_auto_ns = action["observed_ns"]
                self.last_auto_event = action["event_sequence"]
            elif sequence == TENDRILS_RELEASE and self.special == TENDRILS:
                self.autos += 1
                self.special = None
            elif sequence in (TENDRILS, UPPERCUT, HOLD_STILL):
                self.opening = False
                self.autos = 0
                self.special = sequence
                self.special_ns = action["observed_ns"]
                self.special_event = action["event_sequence"]
                self.special_tick = action.get("received_tick")
                self.handled.clear()
                self.forecast_handled = False
                self.expected = {
                    TENDRILS: UPPERCUT,
                    UPPERCUT: HOLD_STILL,
                    HOLD_STILL: TENDRILS,
                }[sequence]

    def next_action(
        self, now, cooldowns, gcd_remaining_ms, prayer, *, received_tick=None, timed_freedom=False
    ):
        """Use cooldown acknowledgement and bound the number of attempts for each mechanic."""
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
        movement = MOVEMENT_WINDOWS.get(self.special)
        if movement and self.autos == 0 and "Surge" not in self.handled:
            earliest_ns, latest_ns, reason = movement
            if earliest_ns <= elapsed_ns <= latest_ns and "Surge" in cooldowns:
                if not cooldowns["Surge"]:
                    return Action("Surge", reason)
        if gcd_remaining_ms != 0:
            return None
        if (
            self.expected == HOLD_STILL
            and self.autos == HOLD_STILL_PREP_AUTOS
            and self.last_auto_ns is not None
            and 0 <= now - self.last_auto_ns < HOLD_STILL_FORECAST_WINDOW_NS
            and prayer == Prayer.MELEE
            and "Anticipation" in cooldowns
            and not cooldowns["Anticipation"]
            and not self.forecast_handled
        ):
            return Action("Anticipation", "Hold Still forecast after Uppercut")
        if (
            active_special
            and self.special == TENDRILS
            and "Hurricane" not in self.handled
            and "Hurricane" in cooldowns
            and not cooldowns["Hurricane"]
        ):
            return Action("Hurricane", "Tendrils damage check")
        return None

    def sent(self, action, now):
        """Wait for the sampled cooldown before treating this command as a cast."""
        self.pending = (action.ability, now + ACK_TIMEOUT_NS)
        if action.ability == "Anticipation":
            self.forecast_handled = True
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


def cooldowns(reader):
    """Separate valid cooldowns from mapped timers absent in the sparse table."""
    timers = {}
    for ability in reader.abilities:
        if ability.equipment_match != EquipmentMatch.ALLOWED:
            continue
        name = ability.name.decode("utf-8", errors="replace")
        if ability.flags & AbilityFlags.TIMER_VALID:
            timers[name] = ability.remaining_ms
        elif ability.reason == Reason.NOT_SET:
            timers[name] = None
    return timers


def encounter_checkpoint(phase, health):
    """Check the 0% HP band against the phase field and flag possible font progress."""
    current, maximum = health["current"], health["max"]
    if maximum != ZERO_ENRAGE_HP or current is None or phase not in (1, 2, 3, 4):
        return None
    # A shared boundary belongs to either neighboring phase during the handoff.
    lower, upper = {
        1: (PHASE_TWO_HP, ZERO_ENRAGE_HP),
        2: (PHASE_THREE_HP, PHASE_TWO_HP),
        3: (PHASE_FOUR_HP, PHASE_THREE_HP),
        4: (0, PHASE_FOUR_HP),
    }[phase]
    if not lower <= current <= upper:
        return None
    fonts_reached = sum(current <= threshold for threshold in FONT_THRESHOLDS) if phase == 4 else 0
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
                    encounter_checkpoint(phase, health) if boss.config_id == ACTIVE_TELOS else None
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
        if hp_known and boss.health_max != ZERO_ENRAGE_HP:
            raise RuntimeError(f"Expected 400,000 maximum Telos HP, observed {boss.health_max}")
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
        if phase not in (1, 2, 3) or not hp_known:
            raise RuntimeError("Encounter phase unavailable or outside the calibrated range")
        if phase == 3:
            if (
                not args.continue_phase_two
                or encounter_checkpoint(phase, npc_health_record(boss)) is None
            ):
                raise RuntimeError("Unexpected phase 3 checkpoint")
            emit("phase_three_observed", boss_hp=boss.health_current, player_hp=hp)
            if attempt and phase_one.pending:
                ability, confirmed = phase_one.acknowledge(cooldowns(reader), now, ending=True)
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
            if encounter_checkpoint(phase, npc_health_record(boss)) is None:
                raise RuntimeError("Phase 2 bits disagree with the observed boss HP")
            first_phase_two_sample = phase_two_at is None
            if first_phase_two_sample:
                emit("phase_two_observed", boss_hp=boss.health_current, phase=phase, player_hp=hp)
                if attempt and phase_one.pending:
                    ability, confirmed = phase_one.acknowledge(cooldowns(reader), now, ending=True)
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
                    phase_one = PhaseOne()
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
                elif boss.health_current < PHASE_TWO_HP:
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
        phase_boundary = PHASE_TWO_HP if phase == 1 else PHASE_THREE_HP
        if boss.health_current <= phase_boundary:
            time.sleep(POLL_SECONDS)
            continue

        autos_before = phase_one.autos
        timed_freedom = getattr(args, "timed_freedom", False)
        received_tick = None
        if timed_freedom:
            tick = reader.encounter.game_tick
            if tick.valid:
                received_tick = tick.counter
            actions = [dict(action, received_tick=received_tick) for action in actions]
        phase_one.observe(actions)
        position = reader.position
        tile = (position.x, position.y, position.plane) if position.valid else None
        if surge_sent_ns is not None:
            displaced = tile is not None and tile != surge_origin
            if displaced or now - surge_sent_ns >= ACK_TIMEOUT_NS:
                emit(
                    "surge_displacement",
                    origin=surge_origin,
                    tile=tile,
                    observed=displaced,
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

        timers = {name: value for name, value in cooldowns(reader).items() if name in bindings}
        action = phase_one.next_action(
            now,
            timers,
            player.gcd_remaining_ms if player.gcd_reason == Reason.NONE else None,
            prayer,
            received_tick=received_tick,
            timed_freedom=timed_freedom,
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
            if action.ability == "Surge" and tile is None:
                time.sleep(POLL_SECONDS)
                continue
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
            if action.ability == "Surge":
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
        "--observe-transition",
        action="store_true",
        help="Record later cues after phase 1 input stops",
    )
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
        "--continue-phase-two",
        action="store_true",
        help="Try shared defenses against observed specials through phase 2",
    )
    parser.add_argument(
        "--reacquire",
        nargs=2,
        metavar=("TARGET_CYCLE", "BASIC_ATTACK"),
        help="Try one phase 2 target cycle and basic attack using configured keys",
    )
    args = parser.parse_args()
    if args.replay and args.port:
        parser.error("Replay does not open the Arduino")
    if args.replay and args.record_attempt:
        parser.error("An attempt log requires a live session")
    if args.continue_phase_two and args.reacquire:
        parser.error("Choose either phase 2 defenses or the one-shot target cycle trial")
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
                    enrage=0 if args.practice_0_confirmed else None,
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
