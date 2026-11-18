"""Native-snapshot damage choices for experimental practice, without acquisition.

This supplements the ordered opening; it is not an optimal phase rotation or a
claim of full castability. Channeled attacks and offensive buffs remain with
their explicit sequence owners, so a filler cannot interrupt them implicitly.
"""

from dataclasses import dataclass

from ..native import (
    AbilityFlags,
    EquipmentMatch,
    NPCFlags,
    Reason,
    SpatialFlags,
    SpatialTargetKind,
    Status,
    Variable,
)
from .p1_rotation import GCD_PRECAST_MS

BASIC_ABILITIES = (
    (14701, "Greater Fury"),
    (14679, "Adaptive Strike"),
    (44244, "Dismember"),
    (52781, "Rend"),
    (14700, "Punish"),
)
DAMAGE_ABILITIES = (
    (14688, "Meteor Strike"),
    (14686, "Overpower"),
    (14685, "Hurricane"),
    *BASIC_ABILITIES,
)
ACKNOWLEDGEMENT_TIMEOUT_NS = 2_500_000_000
ORIGINAL_CLIENT_SHA256 = "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45"
MAX_SAMPLE_AGE_NS = 100_000_000
INSTANCE_VARP = 6359
INSTANCE_BIT = 1 << 29
ENRAGE_MASK = 4095
ACTIVE_TELOS_CONFIG_ID = 22891


def current_telos_target(reader, boss, now_ns):
    """Require fresh original-profile instance evidence and the exact loaded target."""
    info, encounter, actor = reader.info, reader.encounter, reader.spatial.player
    if (
        info.status not in (Status.AVAILABLE, Status.PARTIAL)
        or info.executable_sha256.decode() != ORIGINAL_CLIENT_SHA256
        or not 0 <= now_ns - info.monotonic_ns <= MAX_SAMPLE_AGE_NS
        or encounter.sample_sequence != info.sequence
        or encounter.pid != info.pid
        or encounter.process_creation_time != info.process_creation_time
        or encounter.scene_epoch != reader.combat.scene_epoch
        or encounter.status not in (Status.AVAILABLE, Status.PARTIAL)
        or not actor.valid_mask & SpatialFlags.TARGET
        or actor.target_reason != Reason.NONE
        or actor.target_kind != SpatialTargetKind.NPC
        or actor.target_runtime_id != boss.runtime_id
        or not 0 <= now_ns - actor.observed_ns <= MAX_SAMPLE_AGE_NS
        or boss.config_id != ACTIVE_TELOS_CONFIG_ID
        or boss.flags & (NPCFlags.CONFIG | NPCFlags.HEALTH) != NPCFlags.CONFIG | NPCFlags.HEALTH
        or boss.health_current <= 0
    ):
        return False
    variables = [
        row for row in encounter.raw[: encounter.raw_count] if row.varp_id == INSTANCE_VARP
    ]
    if len(variables) != 1:
        return False
    row = variables[0]
    return (
        row.valid == 1
        and row.reason == 0
        and row.value & INSTANCE_BIT != 0
        and 0 <= row.value & ENRAGE_MASK <= 4000
        and 0 <= now_ns - row.observed_ns <= MAX_SAMPLE_AGE_NS
        and sum(
            (npc.instance_id, npc.runtime_id, npc.config_id)
            == (boss.instance_id, boss.runtime_id, boss.config_id)
            for npc in reader.npcs[: info.npc_count]
        )
        == 1
    )


def ready_damage(reader, *, basic_only=False, reserve_tenths=0, available_names=None):
    """Select an affordable non-channel attack using only the supplied C snapshot.

    NOT_SET authorizes a bounded first-use attempt, never a fabricated zero.
    A known personal cooldown must expire by the queued GCD slot. Equipment
    eligibility, book membership and resource requirements all remain required.
    """
    first_use = reader.player.gcd_reason == Reason.NOT_SET
    if not first_use and (
        reader.player.gcd_reason != Reason.NONE
        or not 0 <= reader.player.gcd_remaining_ms <= GCD_PRECAST_MS
    ):
        return None
    adrenaline = reader.player.variable(Variable.ADRENALINE)
    if adrenaline is None:
        return None
    own_timer_limit = 0 if first_use else reader.player.gcd_remaining_ms
    candidates = BASIC_ABILITIES if basic_only else DAMAGE_ABILITIES
    for identity, name in candidates:
        if available_names is not None and name not in available_names:
            continue
        rows = [
            row
            for row in reader.abilities[: reader.inventory_info.ability_count]
            if row.struct_id == identity
        ]
        if len(rows) != 1:
            continue
        row = rows[0]
        cost = row.adrenaline_requirement_raw
        if (
            row.flags & AbilityFlags.IN_BOOK
            and row.equipment_match == EquipmentMatch.ALLOWED
            and (not basic_only or cost == 0)
            and adrenaline >= cost + (reserve_tenths if cost else 0)
            and (
                row.reason == Reason.NOT_SET
                or (
                    row.reason == Reason.NONE
                    and row.flags & AbilityFlags.TIMER_VALID
                    and 0 <= row.remaining_ms <= own_timer_limit
                )
            )
        ):
            return identity, name, row.start_tick
    return None


@dataclass(frozen=True)
class PendingDamage:
    """One accepted attempt awaiting its exact later native ability timer."""

    ability_id: int
    name: str
    baseline_start: int
    sample_sequence: int
    sent_ns: int
    session: tuple


class DamageCadence:
    """Keep one pending input; missing outcomes latch a fault instead of retrying."""

    def __init__(self):
        self.pending = None
        self.fault = None

    def sent(self, reader, choice, sent_ns):
        """Retain accepted input; the transport still has not proved a cast."""
        if self.pending is not None or self.fault is not None:
            raise RuntimeError("Damage input already pending or faulted")
        self.pending = PendingDamage(
            *choice,
            reader.info.sequence,
            sent_ns,
            (reader.info.pid, reader.info.process_creation_time, reader.combat.scene_epoch),
        )

    def observe(self, reader):
        """Return one cast outcome without sampling or draining any native queue."""
        pending = self.pending
        if pending is None:
            return None
        info = reader.info
        session = info.pid, info.process_creation_time, reader.combat.scene_epoch
        outcome = None
        if session != pending.session:
            outcome = "session_changed"
        elif info.monotonic_ns - pending.sent_ns >= ACKNOWLEDGEMENT_TIMEOUT_NS:
            outcome = "cast_not_observed"
        elif info.sequence > pending.sample_sequence and info.monotonic_ns > pending.sent_ns:
            rows = [
                row
                for row in reader.abilities[: reader.inventory_info.ability_count]
                if row.struct_id == pending.ability_id
            ]
            if (
                len(rows) == 1
                and rows[0].reason == Reason.NONE
                and rows[0].flags & AbilityFlags.TIMER_VALID
                and rows[0].remaining_ms > 0
                and rows[0].start_tick != pending.baseline_start
            ):
                outcome = "cast_confirmed"
        if outcome is None:
            return None
        self.pending = None
        if outcome != "cast_confirmed":
            self.fault = outcome
        return dict(
            event="damage_cast_outcome",
            ability_id=pending.ability_id,
            ability=pending.name,
            sent_native_ns=pending.sent_ns,
            observed_native_ns=info.monotonic_ns,
            outcome=outcome,
        )

    def finish(self, reason):
        """Retain an unconfirmed final action when the practice runner ends."""
        if self.pending is None:
            return None
        pending, self.pending = self.pending, None
        self.fault = reason
        return dict(
            event="damage_cast_outcome",
            ability_id=pending.ability_id,
            ability=pending.name,
            sent_native_ns=pending.sent_ns,
            observed_native_ns=None,
            outcome="unconfirmed_at_attempt_end",
            reason=reason,
        )
