"""Convert native snapshots into the JSON telemetry schema used by the tracker and logs.

The schema (``schema_version`` 1) is a stable contract for captures and the
tracker. Unknown values are ``None`` with a reason, never an assumed zero.
"""

from __future__ import annotations

import time

from .inventory_records import extend_record
from .native import (
    LOGIC_TICK_MS,
    PRAYER_POINTS_MASK,
    VARIABLE_NAMES,
    HitState,
    NPCFlags,
    Reader,
    Reason,
    Status,
    decode_text,
)
from .prayer import Prayer

SCHEMA_VERSION = 1
PRAYER_VARIABLE_IDS = (3272, 3275, 3273, 5859)
GCD_REASON_NAMES = {
    Reason.NONE: None,
    Reason.NOT_SET: "not_set",
    Reason.INCONSISTENT_CLOCK: "inconsistent_clock",
}
PLAYER_HIT_STATUS_NAMES = {
    Status.AVAILABLE: "available",
    Status.PARTIAL: "partial",
    Status.UNAVAILABLE: "unavailable",
}
# Bits 21..23 of the packed Telos variable hold the stage; the mapping is unverified.
TELOS_STAGE_SHIFT = 21
TELOS_STAGE_MASK = 0x7


def nanoseconds_to_milliseconds(nanoseconds, digits):
    return round(nanoseconds / 1e6, digits)


EFFECT_NAMES = (
    "berserk",
    "bloodlust",
    "overload",
    "adrenaline_renewal",
    "anima_virus",
    "stun",
    "bind",
    "resonance",
    "anticipation",
    "freedom",
    "devotion",
    "barricade",
    "immortality",
    "disruption_shield",
)
EFFECT_REASONS = (
    None,
    "unmapped",
    "unvalidated_source",
    "not_set",
    "read_failed",
    "unsupported_profile",
    "frame_unavailable",
    "effect_presence_unmapped",
)


def effects_record(reader):
    """Keep uncalibrated native candidates out of semantic effect fields."""
    info = getattr(reader, "effects_info", None)
    if info is None:
        return {"status": "unavailable", "entries": None, "reason": "module_unavailable"}
    entries = []
    for effect in reader.effects[: info.count]:
        candidate = {
            "domain": {1: "varplayer", 2: "varclient"}.get(effect.source),
            "expiry_var": effect.expiry_var or None,
            "stacks_var": effect.stacks_var or None,
            "active_var": effect.active_var or None,
            "raw_expiry": effect.raw_expiry if effect.raw_valid_mask & 1 else None,
            "raw_stacks": effect.raw_stacks if effect.raw_valid_mask & 2 else None,
            "raw_active": effect.raw_active if effect.raw_valid_mask & 4 else None,
            "expiry_validated": bool(effect.flags & 4),
            "presence_validated": bool(effect.flags & 1),
        }
        entries.append(
            {
                "id": EFFECT_NAMES[effect.effect_id - 1],
                "active": bool(effect.active) if effect.flags & 1 else None,
                "stacks": effect.stacks if effect.flags & 2 else None,
                "remaining_ms": effect.remaining_ms if effect.flags & 4 else None,
                "reason": EFFECT_REASONS[effect.reason],
                "source": "native_memory",
                "candidate": candidate,
            }
        )
    return {
        "status": PLAYER_HIT_STATUS_NAMES.get(info.status, "unavailable"),
        "entries": entries if info.count else None,
        "complete": bool(info.complete),
        "reason": EFFECT_REASONS[info.reason],
        "source": "native_memory",
    }


def combat_record(reader):
    """Return the overhead prayer state of the reader's current snapshot."""
    state = reader.combat
    active_prayer = reader.active_prayer()
    raw_values = (
        state.normal_raw,
        state.curses_raw,
        state.normal_necro_raw,
        state.curses_necro_raw,
    )
    raw_flags = {
        str(variable_id): raw_values[index] if state.raw_valid_mask & (1 << index) else None
        for index, variable_id in enumerate(PRAYER_VARIABLE_IDS)
    }
    prayer_known = active_prayer != Prayer.UNKNOWN
    return {
        "scene_epoch": state.scene_epoch,
        "scene_state": state.scene_state,
        "active_prayer": Prayer(active_prayer).name.lower() if prayer_known else None,
        "known_mask": state.known_mask,
        "active_mask": state.active_mask,
        "raw_flags": raw_flags,
        "reason": None if prayer_known else "active_prayer_not_established",
        "attack_classifier": "not_calibrated",
        "source": "native_memory",
    }


def hit_record(hit):
    """Return the JSON record of one hit mark."""
    secondary_absent = hit.secondary_type_id < 0
    return {
        "slot": hit.slot,
        "type_id": hit.type_id,
        "amount": hit.amount,
        "secondary_type_id": None if secondary_absent else hit.secondary_type_id,
        "secondary_amount": (
            None if secondary_absent or hit.secondary_amount < 0 else hit.secondary_amount
        ),
        "start_client_tick": hit.start_tick,
        "end_client_tick": hit.end_tick,
        "duration_ticks": hit.duration_ticks,
        "duration_ms": hit.duration_ticks * LOGIC_TICK_MS,
        "state": "active" if hit.state == HitState.ACTIVE else "scheduled",
    }


def event_record(event):
    """Return the JSON record of one hit observation with its recipient."""
    return dict(
        hit_record(event.hit),
        native_event_sequence=event.sequence,
        observed_at_ms=event.observed_at_ns // 1_000_000,
        pid=event.pid,
        process_creation_time=event.process_creation_time,
        instance_id=event.instance_id,
        runtime_id=event.runtime_id,
        config_id=None if event.config_id < 0 else event.config_id,
        name=decode_text(event.name) or None,
    )


def player_hit_event_record(event):
    """Return the JSON record of one newly observed local player hit with its provenance."""
    return dict(
        hit_record(event.hit),
        event_sequence=event.sequence,
        observed_ns=event.observed_ns,
        sample_sequence=event.sample_sequence,
        scene_epoch=event.scene_epoch,
        pid=event.pid,
        process_creation_time=event.process_creation_time,
        instance_id=event.instance_id,
        player_index=event.player_index,
    )


def player_hit_splats_record(reader, events, events_lost):
    """Return the local player hitmark channel; attackers are never inferred."""
    info = reader.player_hit_info
    available = info.status == Status.AVAILABLE
    return {
        "status": PLAYER_HIT_STATUS_NAMES.get(info.status, "unavailable"),
        "reason": info.reason,
        "player_index": info.player_index if available else None,
        "instance_id": info.instance_id,
        "events_lost": events_lost,
        "hits": [hit_record(hit) for hit in reader.player_hits[: info.hit_count]],
        # Queued events keep their own process and timestamp across failed frames.
        "newly_observed": [player_hit_event_record(event) for event in events],
        "attacker_identity": None,
        "source": "native_memory",
    }


def npc_health_record(npc):
    known = bool(npc.flags & NPCFlags.HEALTH)
    if known:
        reason = None
    elif npc.health_reason == Reason.NOT_TRANSMITTED:
        reason = "not_transmitted"
    else:
        reason = "invalid_stat_pair"
    return {
        "current": npc.health_current if known else None,
        "max": npc.health_max if known else None,
        "percent": round(npc.health_current / npc.health_max * 100, 2) if known else None,
        "source": "npc_stats",
        "reason": reason,
    }


def npc_animation_record(animation):
    return {
        "action_sequence": animation.action_sequence if animation.valid else None,
        "movement_sequence": animation.movement_sequence if animation.valid else None,
        "reason": None if animation.valid else "animation_layout_unavailable_or_changed",
        "classification": None,
    }


def npc_records(reader, info, npcs, hits):
    """Return (entity records, active hit records) of the loaded NPCs."""
    entities, active_hits = [], []
    identity_keys = ("runtime_id", "instance_id", "config_id", "name")
    for index in range(info.npc_count):
        npc = npcs[index]
        entity = {
            "runtime_id": npc.runtime_id,
            "instance_id": npc.instance_id,
            "config_id": npc.config_id if npc.flags & NPCFlags.CONFIG else None,
            "name": decode_text(npc.name) if npc.flags & NPCFlags.NAME else None,
            "health": npc_health_record(npc),
            "hit_splats": [],
        }
        entity["animation"] = npc_animation_record(reader.npc_animations[index])
        # Each NPC references a slice of the snapshot's shared hit buffer.
        for hit_index in range(npc.hit_offset, npc.hit_offset + npc.hit_count):
            hit = hit_record(hits[hit_index])
            entity["hit_splats"].append(hit)
            if hit["state"] == "active":
                active_hits.append(dict(hit, **{key: entity[key] for key in identity_keys}))
        entities.append(entity)
    entities.sort(
        key=lambda entity: (
            entity["health"]["max"] is None,
            (entity["name"] or "").casefold(),
            entity["runtime_id"],
        )
    )
    return entities, active_hits


def changing_value(value, changed_ns, sample_monotonic_ns):
    """Return a value with the age of its last observed change."""
    return {
        "value": value,
        "reason": "not_set" if value is None else None,
        "last_change_age_ms": (
            None
            if value is None
            else nanoseconds_to_milliseconds(max(0, sample_monotonic_ns - changed_ns), 1)
        ),
    }


class NativeTelemetry:
    """Samples a native Reader and returns schema-version-1 records."""

    def __init__(self, pid=None, worker_hz=100, library=None):
        self.reader = Reader(pid, worker_hz, library)

    def close(self):
        self.reader.close()

    @staticmethod
    def unavailable(message):
        """Return a record that carries no game data."""
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp_ms": time.time_ns() // 1_000_000,
            "backend": "c",
            "pid": None,
            "status": "unavailable",
            "reason": message,
            "health": None,
            "prayer": None,
            "adrenaline_percent": None,
            "gcd": None,
            "telos": None,
            "npcs": None,
            "hit_splats": None,
            "position": None,
        }

    def _native_diagnostics(self, info, events_lost):
        reader = self.reader
        return {
            "sample_sequence": info.sequence,
            "worker_hz": reader.worker_hz,
            "sample_duration_ms": nanoseconds_to_milliseconds(info.sample_duration_ns, 3),
            "read_calls": info.read_calls,
            "read_bytes": info.read_bytes,
            "event_sequence": reader.hit_event_cursor,
            "events_lost": events_lost,
            # Snapshot age uses the same native monotonic clock as the sample timestamp.
            "snapshot_age_ms": nanoseconds_to_milliseconds(
                max(0, reader.monotonic_ns() - info.monotonic_ns), 3
            ),
        }

    def _without_player(
        self, info, native, position, new_events, events_lost, player_hit_splats, available
    ):
        """Return a record for a frame without player fields, such as a tile-only frame."""
        reason = decode_text(info.message) or (None if available else "Waiting for a native sample")
        record = self.unavailable(reason)
        record.update(
            timestamp_ms=info.timestamp_ns // 1_000_000,
            pid=info.pid or None,
            native=native,
            process_creation_time=info.process_creation_time,
            position=position,
            status="partial" if available else "unavailable",
            client_version=self.reader.client_version(),
            executable_sha256=decode_text(info.executable_sha256),
            source="native_memory",
            read_duration_ms=native["sample_duration_ms"],
        )
        record.setdefault("player", {})["effects"] = effects_record(self.reader)
        record["combat"] = combat_record(self.reader)
        record["player_hit_splats"] = player_hit_splats
        # Observations from earlier frames keep their own process and timestamp.
        record["hit_splats"] = {
            "status": "unavailable",
            "active": None,
            "newly_observed": new_events,
            "events_lost": events_lost,
        }
        return extend_record(record, self.reader)

    def sample(self, include_raw=False):
        """Return one telemetry record. include_raw adds the raw variable values."""
        reader = self.reader
        info, player, npcs, hits = reader.sample()
        # Drain independently of frame availability so earlier observations survive.
        events, events_lost = reader.drain_events()
        new_events = [event_record(event) for event in events]
        player_hit_events, player_hit_events_lost = reader.drain_player_hit_events()
        player_hit_splats = player_hit_splats_record(
            reader, player_hit_events, player_hit_events_lost
        )
        native = self._native_diagnostics(info, events_lost)
        tile = reader.position
        position = {"x": tile.x, "y": tile.y, "plane": tile.plane} if tile.valid else None

        available = info.status in (Status.AVAILABLE, Status.PARTIAL)
        if not available or not player.prayer_base_level:
            return self._without_player(
                info, native, position, new_events, events_lost, player_hit_splats, available
            )

        raw_variables = {name: player.variable(index) for index, name in enumerate(VARIABLE_NAMES)}
        prayer_packed = raw_variables["prayer_packed"]
        adrenaline_tenths = raw_variables["adrenaline"]
        # Prayer points and adrenaline are stored in tenths.
        prayer_points = None if prayer_packed is None else (prayer_packed & PRAYER_POINTS_MASK) / 10
        adrenaline = None if adrenaline_tenths is None else adrenaline_tenths / 10
        telos_packed = raw_variables["telos_packed"]

        entities, active_hits = npc_records(reader, info, npcs, hits)
        npc_status = (
            "unavailable"
            if info.reason == Reason.READ_FAILED
            else ("partial" if info.skipped_npcs else "available")
        )
        npc_reason = decode_text(info.message) or (
            "some_entities_changed" if info.skipped_npcs else None
        )
        gcd_reason = GCD_REASON_NAMES.get(player.gcd_reason, "unavailable")
        npcs_unavailable = npc_status == "unavailable"

        record = {
            "schema_version": SCHEMA_VERSION,
            "backend": "c",
            "timestamp_ms": info.timestamp_ns // 1_000_000,
            "monotonic_ms": nanoseconds_to_milliseconds(info.monotonic_ns, 3),
            "pid": info.pid,
            "process_creation_time": info.process_creation_time,
            "native": native,
            "executable_sha256": decode_text(info.executable_sha256),
            "client_version": reader.client_version(),
            "source": "native_memory",
            "status": "partial",
            "read_duration_ms": native["sample_duration_ms"],
            "position": position,
            "combat": combat_record(reader),
            "player_hit_splats": player_hit_splats,
            "freshness": {
                "observed_at_ms": info.timestamp_ns // 1_000_000,
                "client_logic_tick": info.logic_tick,
                "clock_stalled_ms": nanoseconds_to_milliseconds(player.clock_stalled_ns, 1),
                "server_update_age_ms": None,
            },
            "health": {
                "current": changing_value(
                    raw_variables["health_current"], player.hp_changed_ns, info.monotonic_ns
                ),
                "max": raw_variables["health_max"],
            },
            "prayer": {
                "current": changing_value(
                    prayer_points, player.prayer_changed_ns, info.monotonic_ns
                ),
                "display_current": None if prayer_points is None else int(prayer_points),
                "max": player.prayer_base_level * 10,
            },
            "adrenaline_percent": changing_value(
                adrenaline, player.adrenaline_changed_ns, info.monotonic_ns
            ),
            "gcd": {
                "remaining_ms": None if gcd_reason else player.gcd_remaining_ms,
                "duration_ms": None if gcd_reason else player.gcd_duration_ms,
                "reason": gcd_reason,
                "clock_raw": info.logic_tick,
                "start_raw": raw_variables["gcd_start"],
                "end_raw": raw_variables["gcd_end"],
                "duration_raw": raw_variables["gcd_duration"],
                "client_start_raw": raw_variables["gcd_client_start"],
                "client_end_raw": raw_variables["gcd_client_end"],
            },
            "npcs": {
                "status": npc_status,
                "entities": None if npcs_unavailable else entities,
                "loaded_count": info.loaded_npcs,
                "read_count": info.npc_count,
                "skipped_count": info.skipped_npcs,
                "reason": npc_reason,
                "scope": "loaded_scene",
                "source": "native_memory",
            },
            "hit_splats": {
                "status": npc_status,
                "active": None if npcs_unavailable else active_hits,
                "newly_observed": new_events,
                "events_lost": events_lost,
                "reason": npc_reason,
                "coverage": "sampled_client_hitmark_rings",
                "attacker_identity": "not_available",
            },
            # Preserve the packed stage bits for research while the entity mapping is pending.
            "telos": {
                "present": None,
                "animation_id": None,
                "action": None,
                "phase": None,
                "stage_raw": (
                    None
                    if telos_packed is None
                    else (telos_packed >> TELOS_STAGE_SHIFT) & TELOS_STAGE_MASK
                ),
                "reason": "entity_mapping_pending",
            },
        }
        record.setdefault("player", {})["effects"] = effects_record(reader)
        if include_raw:
            record["raw_variables"] = raw_variables
        return extend_record(record, reader)
