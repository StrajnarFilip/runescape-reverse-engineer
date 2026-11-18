"""Bounded, native-only physical EZK / Dragon scimitar EoF calibration at a training dummy.

The operator/agent selects a dummy before the probe acts. All subsequent input
decisions use the C snapshot. Each special is attempted once; a missing effect
transition ends the probe and never causes an automatic retry.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from rs3_reader.bot.actuator_sendinput import NativeKeyAction, NativeSnapshotDevice
from rs3_reader.bot.attempt_log import AttemptLog
from rs3_reader.bot.damage_cadence import BASIC_ABILITIES, ready_damage
from rs3_reader.bot.p1_rotation import GCD_PRECAST_MS
from rs3_reader.bot.world_state import WorldState
from rs3_reader.native import (
    AbilityFlags,
    Container,
    EquipmentMatch,
    InventoryFlags,
    ItemFlags,
    NPCFlags,
    Reader,
    Reason,
    Status,
    Variable,
)
from rs3_reader.telemetry import NativeTelemetry, spatial_actor_record
from rs3_reader.telos_prayer import ManualPrayerKeys

EXPECTED_HASH = "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45"
DUMMY_CONFIG = 16027
EZK_ITEM = 52543
RED_EOF_ITEM = 51065
WEAPON_SPECIAL = 28430
EOF_SPECIAL = 11748
PHYSICAL_EFFECT = 15
SCIMITAR_EFFECT = 16
EFFECT_NOT_SET = 3
MAX_AGE_NS = 100_000_000
EFFECT_TIMEOUT_NS = 4_000_000_000
EQUIP_TIMEOUT_NS = 2_000_000_000
SAMPLE_SECONDS = 0.05
GCD_LEAD_MS = GCD_PRECAST_MS
CALIBRATION_RESERVE_TENTHS = 1000


def effect_endpoint(reader, effect_id):
    """Copy a raw C endpoint; an unset variable stays unknown, not zero."""
    for effect in reader.effects[: reader.effects_info.count]:
        if effect.effect_id == effect_id:
            return effect.raw_expiry if effect.raw_valid_mask & 1 else None
    return None


def equipped(reader, slot, item_id):
    """Require readable equipment and exact unambiguous native identity."""
    if not reader.inventory_info.flags & InventoryFlags.EQUIPMENT_READ:
        return False
    return any(
        item.container_id == Container.EQUIPMENT
        and item.slot == slot
        and item.item_id == item_id
        and item.quantity == 1
        and not item.flags & ItemFlags.AMBIGUOUS_IDENTITY
        for item in reader.items[
            : reader.inventory_info.backpack_count + reader.inventory_info.equipment_count
        ]
    )


def selected_dummy(reader):
    """Accept one exact same-frame dummy target, never a boss or merely nearby actor."""
    target = spatial_actor_record(reader, reader.spatial.player)["target"]
    value = target.get("value")
    if target.get("reason") is not None or not isinstance(value, dict):
        return None
    if value.get("kind") != "npc" or value.get("config_id") != DUMMY_CONFIG:
        return None
    matches = [
        npc
        for npc in reader.npcs[: reader.info.npc_count]
        if npc.instance_id == value.get("instance_id")
        and npc.runtime_id == value.get("runtime_id")
        and npc.config_id == DUMMY_CONFIG
        and npc.flags & NPCFlags.CONFIG
        and npc.name == b"Training dummy"
    ]
    return value if len(matches) == 1 else None


def role_descriptor(reader, *, ability_id=None, item_id=None, name=None):
    """Resolve and prepare a unique current native UI role; no saved key is trusted."""
    found = []
    for bar in range(1, 6):
        for slot in range(1, 15):
            try:
                label = reader.read_action_label(bar, slot)
            except RuntimeError:
                continue
            if label.flags != 15 or label.sample_sequence != reader.info.sequence:
                continue
            if (label.pid, label.process_creation_time) != (
                reader.info.pid,
                reader.info.process_creation_time,
            ):
                continue
            if (item_id is not None and label.item_id == item_id) or (
                ability_id is not None and label.name == name.encode() and label.item_id == -1
            ):
                found.append((bar, slot, label))
    if len(found) != 1:
        raise RuntimeError("Native calibration role missing or ambiguous")
    bar, slot, label = found[0]
    descriptor = NativeKeyAction(virtual_key=label.virtual_key, modifiers=label.modifiers)
    if ability_id is not None:
        descriptor.kind, descriptor.ability_id = 1, ability_id
    else:
        candidates = [
            item
            for item in reader.items[: reader.inventory_info.backpack_count]
            if item.item_id == item_id
            and item.quantity == 1
            and item.token
            and not item.flags & ItemFlags.AMBIGUOUS_IDENTITY
        ]
        if len(candidates) != 1:
            raise RuntimeError("Unique carried calibration item unavailable")
        item = candidates[0]
        descriptor.kind, descriptor.slot = 2, item.slot
        descriptor.expected_item_id, descriptor.expected_token = item.item_id, item.token
    return descriptor, {
        "bar": bar,
        "slot": slot,
        "virtual_key": label.virtual_key,
        "modifiers": label.modifiers,
        "source": "native_ui_label",
    }


def ready_basic(reader):
    """Choose a version-pinned, zero-cost melee basic with known equipment eligibility."""
    return ready_damage(reader, basic_only=True)


def run(reader, device, keys, attempt, *, duration=120, basic_casts=0, damage_casts=0):
    """Perform at most one physical and one EoF press under a fixed native session."""
    identity = None
    phase = "physical"
    pending = None
    previous_status = None
    basics_observed = 0
    cadence_casts = basic_casts or damage_casts
    acquisition_sent_ns = None
    start = time.monotonic()
    telemetry = NativeTelemetry(reader=reader)
    while time.monotonic() - start < duration:
        reader.sample()
        info = reader.info
        actions, action_loss = reader.drain_npc_action_events()
        hits, hit_loss = reader.drain_player_hit_events()
        attempt.frame(
            reader,
            info,
            phase=None,
            actions=actions,
            action_loss=action_loss,
            hits=hits,
            hit_loss=hit_loss,
        )
        session = info.pid, info.process_creation_time, reader.combat.scene_epoch
        if identity is None:
            identity = session
        now = reader.monotonic_ns()
        if keys.get_async_key_state(0x7B) < 0:
            return "stop_key"
        if session != identity or info.executable_sha256.decode() != EXPECTED_HASH:
            return "session_or_executable_changed"
        if (
            info.status not in (Status.AVAILABLE, Status.PARTIAL)
            or not 0 <= now - info.monotonic_ns <= MAX_AGE_NS
        ):
            return "snapshot_unavailable_or_stale"
        if any(
            value != 0
            for value in (
                action_loss,
                hit_loss,
                attempt.inventory_lost,
                attempt.snapshot_events.npc_hits_lost,
            )
        ):
            return "event_queue_loss"
        if reader.player.variable(Variable.HEALTH) != 10400:
            return "unexpected_player_health"
        if keys.game_window(session) is None:
            return "pinned_window_not_foreground"
        target = selected_dummy(reader)
        if target is None and pending is None:
            if acquisition_sent_ns is None:
                dummies = [
                    npc
                    for npc in reader.npcs[: info.npc_count]
                    if npc.config_id == DUMMY_CONFIG
                    and npc.name == b"Training dummy"
                    and npc.flags & NPCFlags.CONFIG
                ]
                if not dummies:
                    return "training_dummy_unavailable"
                descriptor = NativeKeyAction(
                    kind=3,
                    virtual_key=ord("A"),
                    expected_instance_id=dummies[0].instance_id,
                    expected_config_id=DUMMY_CONFIG,
                )
                record = telemetry.record_current(attempt.snapshot_events)
                world = WorldState.from_telemetry(record, now_ns=reader.monotonic_ns())
                attempt.write(
                    "calibration_target_cycle_requested",
                    key="A",
                    source="user_declared_target_cycle_binding",
                )
                device.dispatch_key_batch([descriptor], world)
                acquisition_sent_ns = reader.monotonic_ns()
            elif now - acquisition_sent_ns >= EQUIP_TIMEOUT_NS:
                return "dummy_target_not_acquired"
            time.sleep(SAMPLE_SECONDS)
            continue
        if pending:
            kind, before, sent_ns, sent_tick = pending
            if kind in ("basic", "damage"):
                entry = next(
                    (
                        row
                        for row in reader.abilities[: reader.inventory_info.ability_count]
                        if row.struct_id == before["id"]
                    ),
                    None,
                )
                if (
                    entry is not None
                    and entry.reason == Reason.NONE
                    and entry.flags & AbilityFlags.TIMER_VALID
                    and entry.remaining_ms > 0
                    and entry.start_tick != before["start"]
                ):
                    attempt.write(
                        (
                            "calibration_damage_observed"
                            if damage_casts
                            else "calibration_basic_observed"
                        ),
                        ability_id=before["id"],
                        start_tick=entry.start_tick,
                    )
                    pending = None
                    basics_observed += 1
                    if cadence_casts and basics_observed >= cadence_casts:
                        return (
                            "damage_cadence_recorded" if damage_casts else "basic_cadence_recorded"
                        )
                elif now - sent_ns >= EQUIP_TIMEOUT_NS:
                    return "basic_cooldown_not_observed"
            elif kind == "equip":
                if equipped(reader, 2, RED_EOF_ITEM):
                    attempt.write("calibration_equipment_observed", item_id=RED_EOF_ITEM)
                    pending, phase = None, "eof"
                elif now - sent_ns >= EQUIP_TIMEOUT_NS:
                    return "equipment_not_observed"
            else:
                endpoint = effect_endpoint(
                    reader, PHYSICAL_EFFECT if kind == "physical" else SCIMITAR_EFFECT
                )
                if target is None:
                    return "calibration_target_lost"
                if endpoint is not None and endpoint != before:
                    attempt.write(
                        "special_effect_transition",
                        special=kind,
                        endpoint_before=before,
                        endpoint_after=endpoint,
                        sent_native_ns=sent_ns,
                        observed_native_ns=info.monotonic_ns,
                        sent_logic_tick=sent_tick,
                        observed_logic_tick=info.logic_tick,
                        difference_from_observed_tick=endpoint - info.logic_tick,
                        semantics_validated=False,
                    )
                    pending = None
                    if kind == "eof":
                        return "both_effect_transitions_recorded"
                    phase = "equip_eof"
                elif now - sent_ns >= EFFECT_TIMEOUT_NS:
                    return "special_effect_transition_not_observed:" + kind
        if pending is None:
            adrenaline = reader.player.variable(Variable.ADRENALINE)
            ready = target is not None and equipped(reader, 3, EZK_ITEM)
            basic = (
                (ready_damage(reader) if damage_casts else ready_basic(reader)) if ready else None
            )
            if (
                ready
                and phase in ("physical", "eof")
                and adrenaline is not None
                and (
                    cadence_casts
                    or adrenaline < CALIBRATION_RESERVE_TENTHS
                    or reader.player.gcd_reason == Reason.NOT_SET
                )
                and basic is not None
            ):
                ability_id, name, baseline_start = basic
                descriptor, binding = role_descriptor(reader, ability_id=ability_id, name=name)
                kind, endpoint = (
                    "damage" if damage_casts else "basic",
                    {"id": ability_id, "start": baseline_start},
                )
            elif cadence_casts:
                # Cadence mode never falls through into equipment or specials
                # while all currently eligible basics are cooling.
                time.sleep(SAMPLE_SECONDS)
                continue
            elif phase == "equip_eof" and ready:
                if equipped(reader, 2, RED_EOF_ITEM):
                    phase = "eof"
                    continue
                descriptor, binding = role_descriptor(reader, item_id=RED_EOF_ITEM)
                kind, endpoint = "equip", None
            elif (
                ready
                and adrenaline is not None
                and adrenaline >= CALIBRATION_RESERVE_TENTHS
                and reader.player.gcd_reason == 0
                and reader.player.gcd_remaining_ms <= GCD_LEAD_MS
            ):
                if phase == "eof" and not equipped(reader, 2, RED_EOF_ITEM):
                    return "red_eof_not_equipped"
                effect = PHYSICAL_EFFECT if phase == "physical" else SCIMITAR_EFFECT
                endpoint = effect_endpoint(reader, effect)
                source_effect = next(
                    (
                        row
                        for row in reader.effects[: reader.effects_info.count]
                        if row.effect_id == effect
                    ),
                    None,
                )
                if endpoint is None and (
                    source_effect is None or source_effect.reason != EFFECT_NOT_SET
                ):
                    return "baseline_effect_endpoint_unknown"
                if phase == "physical" and endpoint is not None and endpoint > info.logic_tick:
                    # First labelled capture paired this endpoint with 3000 client
                    # ticks. Wait for that observed cooldown before the repeat.
                    time.sleep(SAMPLE_SECONDS)
                    continue
                descriptor, binding = role_descriptor(
                    reader,
                    ability_id=WEAPON_SPECIAL if phase == "physical" else EOF_SPECIAL,
                    name="Weapon Special Attack" if phase == "physical" else "Essence of Finality",
                )
                kind = phase
            else:
                status = (phase, target is not None, adrenaline)
                if status != previous_status:
                    attempt.write(
                        "calibration_waiting",
                        phase=phase,
                        dummy_target=target,
                        adrenaline_tenths=adrenaline,
                    )
                    previous_status = status
                time.sleep(SAMPLE_SECONDS)
                continue
            if selected_dummy(reader) != target or keys.game_window(session) is None:
                return "target_or_focus_changed_before_input"
            record = telemetry.record_current(attempt.snapshot_events)
            world = WorldState.from_telemetry(record, now_ns=reader.monotonic_ns())
            attempt.write(
                "calibration_input_requested",
                special=kind,
                binding=binding,
                target=target,
                adrenaline_tenths=adrenaline,
                raw_endpoint_before=endpoint,
                gcd_remaining_ms=(
                    reader.player.gcd_remaining_ms
                    if reader.player.gcd_reason == Reason.NONE
                    else None
                ),
                gcd_reason=reader.player.gcd_reason,
                first_use_attempt=reader.player.gcd_reason == Reason.NOT_SET,
            )
            sent_ns = reader.monotonic_ns()
            device.dispatch_key_batch([descriptor], world)
            attempt.write(
                "calibration_input_accepted",
                special=kind,
                sent_native_ns=sent_ns,
                sent_logic_tick=info.logic_tick,
                game_cast_confirmed=False,
            )
            pending = kind, endpoint, sent_ns, info.logic_tick
        time.sleep(SAMPLE_SECONDS)
    return "time_limit"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=int, default=120)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--basic-casts",
        type=int,
        default=0,
        help="Record this many basic casts instead of sending specials",
    )
    modes.add_argument(
        "--damage-casts",
        type=int,
        default=0,
        help="Record non-channel damage casts, including affordable ultimates and Hurricane",
    )
    args = parser.parse_args()
    if (
        args.pid <= 0
        or not 1 <= args.duration <= 180
        or not 0 <= args.basic_casts <= 20
        or not 0 <= args.damage_casts <= 20
    ):
        parser.error("Positive PID and duration 1..180 are required")
    directory = Path(__file__).resolve().parents[1] / "training-data"
    path = directory / (
        "telos-special-effects-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + ".jsonl"
    )
    reason = "runner_exception"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        with Reader(pid=args.pid, worker_hz=0) as reader:
            attempt = AttemptLog(
                stream,
                mode="special_effect_calibration",
                enrage=None,
                monotonic_ns=reader.monotonic_ns,
            )
            print(json.dumps({"log": str(path)}), flush=True)
            device = None
            try:
                device = NativeSnapshotDevice(reader)
                reason = run(
                    reader,
                    device,
                    ManualPrayerKeys(),
                    attempt,
                    duration=args.duration,
                    basic_casts=args.basic_casts,
                    damage_casts=args.damage_casts,
                )
            except (RuntimeError, OSError, ValueError, KeyboardInterrupt) as error:
                attempt.write("calibration_error", error=str(error))
            finally:
                if device:
                    device.close()
                attempt.write(
                    "attempt_ended", result=reason, encounter_attempt=False, complete_kill=False
                )
            print(json.dumps({"result": reason, "log": str(path)}), flush=True)
    return (
        0
        if reason
        in (
            "both_effect_transitions_recorded",
            "basic_cadence_recorded",
            "damage_cadence_recorded",
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
