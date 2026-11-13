"""Observe Telos or run the phase 1 melee controller in a staged 0% practice instance."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

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
from rs3_reader.rotation_keys import ArduinoBackend, parse_chord
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
PHASE_TWO_HP = 300_000
MAX_SAMPLE_AGE_NS = 100_000_000
SPECIAL_WINDOW_NS = 3_000_000_000
ACK_TIMEOUT_NS = 1_200_000_000
KEY_HOLD_SECONDS = 0.05
POLL_SECONDS = 0.025
MAX_PRACTICE_SECONDS = 300
ZERO_ENRAGE_HP = 400_000
F12 = 0x7B
APPROACH_TIMEOUT_NS = 10_000_000_000
ENGAGE_TIMEOUT_NS = 2_000_000_000
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
        self.opening = True
        self.expected = TENDRILS
        self.special = None
        self.special_ns = 0
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
            elif sequence == TENDRILS_RELEASE and self.special == TENDRILS:
                self.autos += 1
                self.special = None
            elif sequence in (TENDRILS, UPPERCUT, HOLD_STILL):
                self.opening = False
                self.autos = 0
                self.special = sequence
                self.special_ns = action["observed_ns"]
                self.handled.clear()
                self.forecast_handled = False
                self.expected = {
                    TENDRILS: UPPERCUT,
                    UPPERCUT: HOLD_STILL,
                    HOLD_STILL: TENDRILS,
                }[sequence]

    def next_action(self, now, cooldowns, gcd_remaining_ms, prayer):
        """Use cooldown acknowledgement and bound the number of attempts for each mechanic."""
        if self.pending:
            ability, deadline = self.pending
            if (cooldowns.get(ability) or 0) > 0:
                self.cast_result = (ability, True)
                self.pending = None
            elif now >= deadline or (self.special == HOLD_STILL and ability != "Freedom"):
                self.cast_result = (ability, False)
                self.pending = None
            else:
                return None

        elapsed_ns = now - self.special_ns
        active_special = 0 <= elapsed_ns < SPECIAL_WINDOW_NS
        if (
            active_special
            and self.special == HOLD_STILL
            and "Freedom" not in self.handled
            and "Freedom" in cooldowns
            and not cooldowns["Freedom"]
        ):
            return Action("Freedom", "Hold Still animation")
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


def replay(path):
    """Reconstruct the observed attack counts without assuming ability availability."""
    encounter = None
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            raw = row["telos_raw"]
            bosses = [npc for npc in row["telos"] if npc["config_id"] == ACTIVE_TELOS]
            if len(bosses) != 1 or (raw is not None and (raw >> PHASE_SHIFT) & PHASE_MASK):
                encounter = None
                continue
            current = (
                row["pid"],
                row["process_creation_time"],
                row["combat"]["scene_epoch"],
                bosses[0]["instance_id"],
            )
            if current != encounter:
                phase_one = PhaseOne()
                encounter = current
            if row["npc_action_events_lost"]:
                raise RuntimeError("Replay contains an NPC action queue overflow")
            actions = [
                action
                for action in row["telos_actions"]
                if action["instance_id"] == current[-1] and action["config_id"] == ACTIVE_TELOS
            ]
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


def observe(reader, duration):
    """Record post-transition cues without foreground focus or Arduino input."""
    deadline = time.monotonic() + duration
    last_state = None
    last_status = None
    while time.monotonic() < deadline:
        info, player, boss, phase, hp = boss_sample(reader)
        session = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
        events, lost = reader.drain_npc_action_events()
        hits, hits_lost = reader.drain_player_hit_events()
        if info.status != last_status:
            log("reader_status", status=info.status, reason=info.message.decode(errors="replace"))
            last_status = info.status
        if boss is not None:
            actions = telos_actions(events, session, info.sequence, {boss.instance_id})
            tile = reader.position
            state = dict(
                session=session,
                boss_config_id=boss.config_id,
                boss_instance_id=boss.instance_id,
                boss_health=npc_health_record(boss),
                telos_raw=player.variable(Variable.TELOS_PACKED),
                phase_candidate=phase,
                hp=hp,
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
        time.sleep(POLL_SECONDS)
    return 0


def run(reader, args, board):
    """Track an attached practice encounter and dispatch only from fresh native state."""
    from game_console import GameWindow, frame_target, move_pointer

    keys = ManualPrayerKeys()
    phase_one = PhaseOne()
    initial, _, _, _, _ = boss_sample(reader)
    identity = (initial.pid, initial.process_creation_time, reader.combat.scene_epoch)
    window = keys.game_window(identity)
    if not window:
        raise RuntimeError("RuneScape must be foreground before starting")

    def check_focus():
        if keys.get_async_key_state(F12) < 0:
            raise RuntimeError("F12 stop")
        if keys.game_window(identity) != window:
            raise RuntimeError("RuneScape lost foreground focus")

    def click_frame(x, y):
        game = GameWindow()
        bounds, target = frame_target(game, args.frame, x, y)
        check_focus()
        move_pointer(board, game, bounds, target)
        if game.bounds() != bounds:
            raise RuntimeError("RuneScape moved while the pointer was positioning")
        game.check_point(*game.cursor())
        board.command("CLICK L", check_focus)

    bindings = {
        "Freedom": parse_chord(args.freedom),
        "Anticipation": parse_chord(args.anticipation),
        "Hurricane": parse_chord(args.hurricane),
    }
    if args.surge:
        bindings["Surge"] = parse_chord(args.surge)
    if len(set(bindings.values())) != len(bindings):
        raise ValueError("Combat abilities require distinct bindings")
    prayer_bindings = {parse_chord(f"Ctrl+F{number}") for number in range(1, 5)}
    if any(
        binding in prayer_bindings or binding[-1].virtual_key == F12
        for binding in bindings.values()
    ):
        raise ValueError("Combat bindings must exclude prayer chords and F12")
    deadline = time.monotonic() + args.duration
    initiated = False
    active_instance = None
    engaged_at = 0
    phase_one_started = False
    next_prayer_check = 0
    next_approach = 0
    previous_tile = None
    previous_prayer = None
    while time.monotonic() < deadline:
        check_focus()
        info, player, boss, phase, hp = boss_sample(reader)
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
            raise RuntimeError("Player HP is unavailable or zero")
        events, lost = reader.drain_npc_action_events()
        hits, hits_lost = reader.drain_player_hit_events()
        if lost:
            raise RuntimeError("NPC action queue overflow")
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
                    click_frame(*args.melee)
                    log("baseline_prayer_requested", prayer="melee")
                    next_prayer_check = reader.monotonic_ns() + ACK_TIMEOUT_NS
                time.sleep(POLL_SECONDS)
                continue
        if boss is None or boss.config_id != ACTIVE_TELOS:
            if active_instance is not None:
                raise RuntimeError("Active Telos disappeared")
            if not initiated and boss is not None and boss.config_id == DORMANT_TELOS:
                click_frame(*args.approach)
                initiated = True
                next_approach = reader.monotonic_ns() + APPROACH_TIMEOUT_NS
                log("approach", overhead=prayer.name.lower())
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
                log("engaged", boss_hp=boss.health_current, phase=phase, player_hp=hp)
                phase_one_started = True
            elif now - engaged_at >= ENGAGE_TIMEOUT_NS:
                raise RuntimeError(
                    f"Opening phase or boss HP unavailable (phase={phase}, max={boss.health_max})"
                )
            else:
                phase_one.observe(actions)
                time.sleep(POLL_SECONDS)
                continue
        if phase not in (1, 2) or not hp_known:
            raise RuntimeError("Encounter phase unavailable or outside the calibrated range")
        if phase == 2:
            if boss.health_current > PHASE_TWO_HP:
                raise RuntimeError("Phase 2 bits disagree with the observed boss HP")
            log("phase_two_observed", boss_hp=boss.health_current, phase=phase, player_hp=hp)
            if args.observe_transition:
                board.close()
                observe(reader, max(0, deadline - time.monotonic()))
            return 2
        if boss.health_current <= PHASE_TWO_HP:
            time.sleep(POLL_SECONDS)
            continue

        autos_before = phase_one.autos
        phase_one.observe(actions)
        position = reader.position
        tile = (position.x, position.y, position.plane) if position.valid else None
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
        )
        if phase_one.cast_result:
            ability, confirmed = phase_one.cast_result
            log("cooldown_acknowledgement", ability=ability, observed=confirmed, sample_ns=now)
            phase_one.cast_result = None
        if action:
            if reader.monotonic_ns() - info.monotonic_ns > MAX_SAMPLE_AGE_NS:
                raise RuntimeError("Native sample aged out before submitting the action")
            board.tap(bindings[action.ability], KEY_HOLD_SECONDS, check_focus, time.sleep)
            sent_ns = reader.monotonic_ns()
            phase_one.sent(action, sent_ns)
            log("ability_sent", ability=action.ability, reason=action.reason, sent_ns=sent_ns)
        time.sleep(POLL_SECONDS)
    log("timeout", active=active_instance is not None)
    return 1


def main():
    """Start with bar 18 active and Practice Mode 0% confirmed in the client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--duration", type=int, default=MAX_PRACTICE_SECONDS)
    parser.add_argument("--port", help="Arduino COM port. Omit for observation only.")
    parser.add_argument("--replay", type=Path, help="Read a local telos_prayer.py JSONL capture")
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
    parser.add_argument("--freedom", default="U")
    parser.add_argument("--anticipation", default="A")
    parser.add_argument("--hurricane", default="8")
    parser.add_argument("--surge", help="Optional Surge binding for experimental movement")
    args = parser.parse_args()
    if args.replay and args.port:
        parser.error("Replay does not open the Arduino")
    if not 0 < args.duration <= MAX_PRACTICE_SECONDS:
        parser.error(f"Duration must be between 1 and {MAX_PRACTICE_SECONDS} seconds")
    if args.port and not all((args.frame, args.approach, args.melee, args.practice_0_confirmed)):
        parser.error(
            "Arduino input requires --practice-0-confirmed, --frame, --melee and --approach"
        )
    board = None
    try:
        if args.replay:
            replay(args.replay)
            return 0
        with Reader(pid=args.pid, worker_hz=0) as reader:
            if not args.port:
                return observe(reader, args.duration)
            board = ArduinoBackend(args.port)
            return run(reader, args, board)
    except (OSError, RuntimeError, ValueError) as error:
        log("stopped", reason=str(error))
        return 1
    finally:
        if board:
            board.close()


if __name__ == "__main__":
    raise SystemExit(main())
