"""Convert native snapshots into the JSON telemetry schema used by the tracker and logs.

The schema (``schema_version`` 1) is a stable contract for captures and the
tracker. Unknown values are ``None`` with a reason, never an assumed zero.
"""

from __future__ import annotations

import time
import ctypes
import math

from .inventory_records import extend_record
from .native import (
    LOGIC_TICK_MS,
    PRAYER_POINTS_MASK,
    VARIABLE_NAMES,
    HitState,
    HazardFlags,
    NPCFlags,
    NPCActionFlags,
    Reader,
    Reason,
    Status,
    decode_text,
    ScreenPoint,
    SpatialFlags,
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


INTERFACE_REASONS = (
    None,
    "unmapped",
    "not_set",
    "read_failed",
    "unsupported_profile",
    "content_unmapped",
    "key_unmapped",
    "frame_unavailable",
)
INTERFACE_KINDS = ("instance", "enrage", "loot", "bank_preset", "death")


def interface_records(reader):
    """Return native bindings and partial, individually validated interface state."""
    info = getattr(reader, "interface_info", None)
    if info is None:
        unknown = {"status": "unavailable", "reason": "module_unavailable"}
        return dict(unknown, open=None), dict(unknown, entries=None)
    entries = []
    for slot in reader.action_bar[: info.action_bar_count]:
        chord = None
        if slot.flags & 256:
            chord = ("Ctrl+" if slot.key_modifiers & 2 else "") + f"F{slot.key_code}"
        entries.append(
            {
                "display_bar": slot.display_bar,
                "bar": slot.preset_bar if slot.flags & 1 else None,
                "slot": slot.slot,
                "type": slot.content_type if slot.flags & 2 else None,
                "content_id": slot.content_id if slot.flags & 2 else None,
                "item_id": slot.item_id if slot.flags & 8 else None,
                "ability_struct_id": slot.ability_struct_id if slot.flags & (16 | 32) else None,
                "kind": (
                    "item"
                    if slot.flags & 8
                    else "prayer" if slot.flags & 32 else "ability" if slot.flags & 16 else None
                ),
                "name": (decode_text(slot.name) or None) if slot.flags & (8 | 16 | 32) else None,
                "key": chord,
                "key_code": slot.key_code if slot.flags & 64 else None,
                "modifier_code": slot.modifier_code if slot.flags & 128 else None,
                "reason": INTERFACE_REASONS[slot.reason],
                "binding_reason": INTERFACE_REASONS[slot.binding_reason],
                "source": "native_memory",
                "raw": {
                    "source_var": slot.source_var or None,
                    "object_var": slot.object_var or None,
                    "key_var": slot.key_var or None,
                    "modifier_var": slot.modifier_var or None,
                    "packed": slot.raw_packed if slot.flags & 2 else None,
                    "object": slot.raw_object if slot.flags & 4 else None,
                    "keys": slot.raw_key_packed if slot.flags & 64 else None,
                    "modifiers": slot.raw_modifier_packed if slot.flags & 128 else None,
                },
            }
        )
    required = []
    for item in reader.interfaces[: info.interface_count]:
        known = item.reason == 0 and item.open in (0, 1)
        required.append(
            {
                "kind": INTERFACE_KINDS[item.kind - 1],
                "open": bool(item.open) if known else None,
                "interface_id": item.interface_id if known else None,
                "scope": (
                    {2: "initial_enrage_choice", 4: "bank_root_attachment"}.get(item.kind)
                    if known
                    else None
                ),
                "heading": (
                    "Choose initial enrage." if known and item.kind == 2 and item.open else None
                ),
                "visible": None,
                "preset_controls_visible": None,
                "options": None,
                "click_point": None,
                "reason": INTERFACE_REASONS[item.reason],
                "source": "native_memory",
            }
        )
    any_known = any(item["open"] is not None for item in required)
    ui = {
        "status": "partial" if any_known else "unavailable",
        "open": [dict(item) for item in required if item["open"] is True] if any_known else None,
        "complete": False,
        "reason": (
            "other_interfaces_visibility_and_options_unmapped"
            if any_known
            else (
                "open_interface_registry_unmapped"
                if info.interface_count
                else INTERFACE_REASONS[info.reason]
            )
        ),
        "source": "native_memory",
        "required": required,
    }
    bars = {
        "status": PLAYER_HIT_STATUS_NAMES.get(info.status, "unavailable"),
        "entries": entries if info.action_bar_count else None,
        "complete": bool(info.action_bar_complete),
        "reason": None if info.action_bar_count else INTERFACE_REASONS[info.reason],
        "source": "native_memory",
    }
    return ui, bars
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


def npc_action_event_record(event):
    """Keep the native queue identity/time; never label an animation as an attack."""
    return {
        "sequence": event.sequence,
        "observed_ns": event.observed_ns,
        "sample_sequence": event.sample_sequence,
        "scene_epoch": event.scene_epoch,
        "process_creation_time": event.process_creation_time,
        "instance_id": event.instance_id,
        "pid": event.pid,
        "runtime_id": event.runtime_id,
        "config_id": event.config_id if event.config_id >= 0 else None,
        "previous_action_sequence": event.previous_action_sequence,
        "action_sequence": event.action_sequence,
        "client_logic_tick": event.logic_tick,
        "application_stamp": (
            event.application_stamp if event.flags & NPCActionFlags.HAS_APPLICATION_STAMP else None
        ),
        "flags": event.flags,
    }


def npc_records(reader, info, npcs, hits):
    """Return (entity records, active hit records) of the loaded NPCs."""
    entities, active_hits = [], []
    identity_keys = ("runtime_id", "instance_id", "config_id", "name")
    spatial = {
        reader.npc_spatial[index].instance_id: reader.npc_spatial[index]
        for index in range(reader.spatial.npc_count)
    }
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
        pose = spatial.get(npc.instance_id)
        if pose is not None:
            entity.update(spatial_actor_record(reader, pose))
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


def spatial_actor_record(reader, actor):
    """Serialize C spatial observations; unknown targets do not mean no target."""
    age = (
        nanoseconds_to_milliseconds(max(0, reader.monotonic_ns() - actor.observed_ns), 3)
        if actor.observed_ns
        else None
    )

    def field(value, reason):
        return {
            "value": value,
            "age_ms": age,
            "source": "native_spatial",
            "reason": (
                None
                if value is not None
                else {
                    0: "not_observed",
                    2: "read_failed",
                    3: "unsupported_profile",
                    100: "unmapped",
                }.get(reason or reader.spatial.reason, "spatial_reason_" + str(reason))
            ),
        }

    result = {
        "tile": field(
            (
                {"x": actor.tile.x, "y": actor.tile.y, "plane": actor.tile.plane}
                if actor.valid_mask & SpatialFlags.TILE
                else None
            ),
            actor.tile_reason,
        ),
        "facing": field(
            (
                {"angle": actor.facing_degrees, "unit": "degrees"}
                if actor.valid_mask & SpatialFlags.FACING
                else None
            ),
            actor.facing_reason,
        ),
        "target": field(None, actor.target_reason),
    }
    if actor.valid_mask & SpatialFlags.FINE_POSITION:
        point = ScreenPoint()
        reader.check_result(
            reader.library.rs3_project_actor(
                ctypes.byref(reader.spatial.camera),
                ctypes.byref(actor),
                0,
                ctypes.byref(point),
                ctypes.sizeof(point),
            )
        )
        result["screen_point"] = {
            "x": point.x if point.valid else None,
            "y": point.y if point.valid else None,
            "valid": bool(point.valid),
            "reason": point.reason,
            "units": "physical_screen_pixels",
        }
    return result


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


def hazards_record(reader):
    """Serialize C candidates; identity/pose evidence does not validate a mechanic."""
    info = reader.hazard_info
    kinds = (
        "unknown",
        "green_beam",
        "black_beam",
        "red_beam",
        "font",
        "falling_rock",
        "tendrils",
        "anima_bomb",
        "projectile",
        "golem",
    )
    variants = (None, "pure", "corrupt", "volcanic")
    reasons = {
        0: None,
        400: "hazard_channel_unmapped",
        401: "hazard_identity_unmapped",
        402: "hazard_spatial_unavailable",
        403: "hazard_wrong_scene",
        404: "hazard_candidate_overflow",
        405: "hazard_mechanics_need_calibration",
    }

    def reason_name(reason):
        return reasons.get(reason, "native_reason_" + str(reason))

    available = info.status in (Status.AVAILABLE, Status.PARTIAL)
    age_ms = (
        max(0, reader.monotonic_ns() - info.observed_ns) / 1e6
        if available and info.observed_ns
        else None
    )
    entries = []
    for hazard in reader.hazards[: info.count] if available else ():
        flags = HazardFlags(hazard.flags)
        entries.append(
            {
                "id": "npc:" + str(hazard.instance_id),
                "instance_id": hazard.instance_id,
                "runtime_id": hazard.runtime_id,
                "config_id": hazard.config_id if hazard.config_id >= 0 else None,
                "kind": kinds[hazard.kind] if hazard.kind < len(kinds) else "unknown",
                "variant": variants[hazard.variant] if hazard.variant < len(variants) else None,
                "identity_validated": bool(flags & HazardFlags.IDENTITY),
                "validated": bool(flags & HazardFlags.MECHANICS_VALIDATED),
                "present": True,
                "presence_scope": "loaded_npc_registry",
                "same_world": True if flags & HazardFlags.TILE else None,
                "active": None,
                "dangerous": None,
                "tile": (
                    {"x": hazard.tile.x, "y": hazard.tile.y, "plane": hazard.tile.plane}
                    if flags & HazardFlags.TILE
                    else None
                ),
                "tile_reason": reason_name(hazard.tile_reason),
                "actor_footprint_size": (
                    hazard.footprint_size if flags & HazardFlags.FOOTPRINT else None
                ),
                "area": None,
                "start_client_tick": (
                    hazard.start_client_tick if flags & HazardFlags.CLIENT_START else None
                ),
                "end_client_tick": (
                    hazard.end_client_tick if flags & HazardFlags.CLIENT_END else None
                ),
                "timing_reason": reason_name(hazard.timing_reason),
                "role": None,
                "role_reason": reason_name(hazard.role_reason),
                "age_ms": age_ms,
                "reason": reason_name(hazard.reason),
                "source": "native_memory",
            }
        )
    return {
        "status": PLAYER_HIT_STATUS_NAMES.get(info.status, "unavailable"),
        "entries": entries,
        "complete": False,
        "scope": "loaded_npc_registry_candidates",
        "observed_channels": info.observed_channels,
        "complete_channels": info.complete_channels,
        "unmapped_channels": info.unmapped_channels,
        "candidate_count": info.candidate_count,
        "dropped_count": info.dropped_count,
        "unlocated_count": info.unlocated_count,
        "sample_sequence": info.sample_sequence,
        "observed_ns": info.observed_ns,
        "pid": info.pid,
        "process_creation_time": info.process_creation_time,
        "scene_epoch": info.scene_epoch,
        "age_ms": age_ms,
        "source": "native_memory",
        "reason": reason_name(info.reason),
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
        record["ui"], record["action_bar"] = interface_records(self.reader)
        record["combat"] = combat_record(self.reader)
        record["player"] = spatial_actor_record(self.reader, self.reader.spatial.player)
        record.setdefault("player", {})["effects"] = effects_record(self.reader)
        record["player_hit_splats"] = player_hit_splats
        record["hazards"] = hazards_record(self.reader)
        # Observations from earlier frames keep their own process and timestamp.
        record["hit_splats"] = {
            "status": "unavailable",
            "active": None,
            "newly_observed": new_events,
            "events_lost": events_lost,
        }
        return extend_encounter_record(extend_record(record, self.reader), self.reader)

    def sample(self, include_raw=False):
        """Return one telemetry record. include_raw adds the raw variable values."""
        reader = self.reader
        info, player, npcs, hits = reader.sample()
        # Drain independently of frame availability so earlier observations survive.
        events, events_lost = reader.drain_events()
        new_events = [event_record(event) for event in events]
        npc_actions, npc_actions_lost = reader.drain_npc_action_events()
        npc_action_records = [npc_action_event_record(event) for event in npc_actions]
        player_hit_events, player_hit_events_lost = reader.drain_player_hit_events()
        player_hit_splats = player_hit_splats_record(
            reader, player_hit_events, player_hit_events_lost
        )
        native = self._native_diagnostics(info, events_lost)
        tile = reader.position
        position = {"x": tile.x, "y": tile.y, "plane": tile.plane} if tile.valid else None

        available = info.status in (Status.AVAILABLE, Status.PARTIAL)
        if not available or not player.prayer_base_level:
            record = self._without_player(
                info, native, position, new_events, events_lost, player_hit_splats, available
            )
            record["npc_action_events"] = npc_action_records
            record["npc_action_events_lost"] = npc_actions_lost
            return record

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
            "player": spatial_actor_record(reader, reader.spatial.player),
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
        record["ui"], record["action_bar"] = interface_records(reader)
        record.setdefault("player", {})["effects"] = effects_record(reader)
        if include_raw:
            record["raw_variables"] = raw_variables
        record["hazards"] = hazards_record(reader)
        record["npc_action_events"] = npc_action_records
        record["npc_action_events_lost"] = npc_actions_lost
        return extend_encounter_record(extend_record(record, reader), reader)


def extend_encounter_record(record, reader):
    """Add P2 C observations; raw candidates never establish semantic values."""
    state = reader.encounter
    now_ns = reader.monotonic_ns()

    def observation(field):
        return {
            "value": field.value if field.valid else None,
            "age_ms": (
                nanoseconds_to_milliseconds(max(0, now_ns - field.observed_ns), 3)
                if field.observed_ns
                else None
            ),
            "source": "native_encounter",
            "reason": (
                None
                if field.valid
                else {
                    1: "not_set",
                    2: "read_failed",
                    8: "invalid_value",
                    100: "unmapped",
                    101: "not_active",
                }.get(field.reason or state.reason, "not_observed")
            ),
        }

    telos = record.get("telos") or {}
    for name in ("phase", "enrage", "streak", "meter", "freedom"):
        telos[name] = observation(getattr(state, name))
    if state.freedom.valid:
        telos["freedom"]["value"] = bool(state.freedom.value)
    record["telos"] = telos
    tick = state.game_tick
    record["game_tick"] = {
        "value": (
            {
                "index": tick.counter,
                "phase_ms": tick.phase_ms if math.isfinite(tick.phase_ms) else None,
                "uncertainty_ms": (
                    tick.uncertainty_ms if math.isfinite(tick.uncertainty_ms) else None
                ),
            }
            if tick.valid
            else None
        ),
        "source": "native_encounter",
        "age_ms": (
            nanoseconds_to_milliseconds(max(0, now_ns - tick.observed_ns), 3)
            if tick.observed_ns
            else None
        ),
        "reason": (
            None
            if tick.valid
            else {
                2: "read_failed",
                102: "awaiting_tick_transition",
                103: "tick_counter_stalled",
                104: "tick_counter_reset",
            }.get(tick.reason, "unmapped")
        ),
    }
    record["encounter"] = {
        "sample_sequence": state.sample_sequence,
        "scene_epoch": state.scene_epoch,
        "process_creation_time": state.process_creation_time,
        "pid": state.pid,
        "status": PLAYER_HIT_STATUS_NAMES.get(state.status, "unavailable"),
        "reason": state.reason,
        "stage_raw": observation(state.stage_raw),
        "raw_candidates": {
            str(state.raw[index].varp_id): observation(state.raw[index])
            for index in range(state.raw_count)
        },
        "semantics_validated": False,
        "validated_fields": [name for name in ("phase", "enrage") if getattr(state, name).valid],
    }
    return record
