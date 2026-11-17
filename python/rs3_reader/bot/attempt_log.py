"""Write compact, versioned Telos attempt records from sampled state and controller events."""

from __future__ import annotations

import json
import time

from ..native import (
    PRAYER_POINTS_MASK,
    AbilityFlags,
    HazardFlags,
    NPCActionFlags,
    NPCFlags,
    SpatialFlags,
    Variable,
)
from ..prayer import Prayer
from ..telemetry import effects_record, hit_record, spatial_actor_record

SCHEMA_VERSION = 1
CLEANSE_MAX_AGE_CLIENT_TICKS = 50
PRAYER_ACK_TIMEOUT_NS = 1_200_000_000


class AttemptLog:
    """Write one initial state, state deltas, native events and controller outcomes."""

    def __init__(
        self,
        stream,
        *,
        mode,
        enrage,
        monotonic_ns=time.monotonic_ns,
        prayer_ack_timeout_ns=PRAYER_ACK_TIMEOUT_NS,
    ):
        self.stream = stream
        self.clock = monotonic_ns
        self.origin_ns = None
        self.sequence = 0
        self.frame_sequence = None
        self.previous = {}
        self.client_logic_tick = None
        self.game_tick = None
        self.session = None
        self.session_index = 0
        self.engaged = False
        self.last_cleanse = None
        self.pending_prayer = None
        self.prayer_ack_timeout_ns = prayer_ack_timeout_ns
        self.end_reason = None
        self.write("attempt_started", mode=mode, enrage_label=enrage)

    def relative_ns(self, value):
        """Return the offset in this attempt's native monotonic time domain."""
        return None if not value else value - self.origin_ns

    def write(self, event, **fields):
        """Assign a sequence number and persist the event before returning."""
        now_ns = self.clock()
        if self.origin_ns is None:
            self.origin_ns = now_ns
        if self.pending_prayer is not None:
            if event in ("attempt_ended", "encounter_reset"):
                self.observe_prayer(now_ns, None, ending=event)
                now_ns = self.clock()
            elif event == "prayer_flick_sent":
                self.observe_prayer(now_ns, None, ending="superseded")
                now_ns = self.clock()
        self.engaged |= event == "engaged"
        self.sequence += 1
        row = {
            "schema_version": SCHEMA_VERSION,
            "sequence": self.sequence,
            "timestamp_ms": time.time_ns() // 1_000_000,
            "elapsed_ns": self.relative_ns(now_ns),
            "game_tick": self.game_tick,
            "client_logic_tick": self.client_logic_tick,
            "frame_sequence": self.frame_sequence,
            "event": event,
            **fields,
        }
        print(json.dumps(row, separators=(",", ":"), allow_nan=False), file=self.stream, flush=True)
        if event == "prayer_flick_sent":
            self.pending_prayer = (
                self.sequence,
                fields["prayer"],
                fields.get("cue_native_sequence"),
                self.game_tick,
                now_ns,
            )

    def observe_prayer(self, observed_ns, prayer, *, ending=None):
        """Match an issued player input against the next valid client overhead observation."""
        if self.pending_prayer is None:
            return
        request, target, cue, sent_tick, sent_ns = self.pending_prayer
        if ending:
            outcome = ending
        elif observed_ns < sent_ns:
            return
        elif prayer == target and observed_ns - sent_ns <= self.prayer_ack_timeout_ns:
            outcome = "acknowledged"
        elif observed_ns - sent_ns < self.prayer_ack_timeout_ns:
            return
        else:
            outcome = "timeout"
        self.pending_prayer = None
        self.write(
            "prayer_flick_outcome",
            request_sequence=request,
            prayer=target,
            observed_prayer=prayer,
            cue_native_sequence=cue,
            sent_tick=sent_tick,
            outcome=outcome,
            observed_ns=self.relative_ns(observed_ns),
        )

    def frame(
        self, reader, info, *, phase, actions, action_loss, hits, hit_loss, intervening_prayers=None
    ):
        """Record only encounter-relevant decoded fields and preserve queue event ordering."""
        player = reader.player
        npc_hits, npc_hit_loss = reader.drain_events()
        session = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
        if session != self.session:
            self.session = session
            self.session_index += 1
        self.frame_sequence = info.sequence
        self.client_logic_tick = info.logic_tick if info.monotonic_ns else None
        encounter = reader.encounter
        previous_tick = self.game_tick
        tick = encounter.game_tick
        self.game_tick = tick.counter if tick.valid else None
        if self.game_tick != previous_tick:
            self.write(
                "received_tick", observed_ns=self.relative_ns(tick.observed_ns), reason=tick.reason
            )
        target = spatial_actor_record(reader, reader.spatial.player)["target"]
        camera = reader.spatial.camera
        prayer_packed = player.variable(Variable.PRAYER_PACKED)
        tile = reader.position
        poses = {
            pose.instance_id: pose
            for pose in reader.npc_spatial[: reader.spatial.npc_count]
            if pose.valid_mask & SpatialFlags.TILE
        }
        beams = [
            hazard
            for hazard in reader.hazards[: reader.hazard_info.count]
            if hazard.flags & HazardFlags.SCENE_LOCATION
        ]
        effects = effects_record(reader)
        for effect in effects["entries"] or ():
            # Expiry stamps and presence changes suffice to reconstruct a validated timer.
            effect.pop("remaining_ms")
        state = {
            "session": self.session_index,
            "status": info.status,
            "reason": info.reason,
            "client_version": reader.client_version(),
            "phase_candidate": phase,
            "phase": encounter.phase.value if encounter.phase.valid else None,
            "enrage": encounter.enrage.value if encounter.enrage.valid else None,
            "meter": encounter.meter.value if encounter.meter.valid else None,
            "encounter_variables": {
                raw.varp_id: raw.value if raw.valid else None
                for raw in encounter.raw[: encounter.raw_count]
            },
            "hp": player.variable(Variable.HEALTH),
            "max_hp": player.variable(Variable.MAX_HEALTH),
            "prayer_points": (
                None if prayer_packed is None else (prayer_packed & PRAYER_POINTS_MASK) / 10
            ),
            "active_prayer": Prayer(reader.active_prayer()).name.lower(),
            "prayer_flags": {
                str(varp): value if reader.combat.raw_valid_mask & (1 << index) else None
                for index, (varp, value) in enumerate(
                    zip(
                        (3272, 3275, 3273, 5859),
                        (
                            reader.combat.normal_raw,
                            reader.combat.curses_raw,
                            reader.combat.normal_necro_raw,
                            reader.combat.curses_necro_raw,
                        ),
                    )
                )
            },
            "adrenaline_tenths": player.variable(Variable.ADRENALINE),
            "tile": (tile.x, tile.y, tile.plane) if tile.valid else None,
            "player_fine_height": (
                reader.spatial.player.fine_height
                if reader.spatial.player.valid_mask & SpatialFlags.FINE_POSITION
                else None
            ),
            "combat_target": target["value"],
            "combat_target_reason": target["reason"],
            "gcd_end_client_tick": player.variable(Variable.GCD_CLIENT_END),
            "gcd_active": bool(player.gcd_remaining_ms) if player.gcd_reason == 0 else None,
            "effects": effects,
            "auto_retaliate": getattr(reader, "auto_retaliate", None),
            "ability_cooldowns": {
                ability.struct_id: (
                    ability.reason,
                    ability.start_tick if ability.flags & AbilityFlags.TIMER_VALID else None,
                    ability.end_tick if ability.flags & AbilityFlags.TIMER_VALID else None,
                    (
                        bool(ability.remaining_ms)
                        if ability.flags & AbilityFlags.TIMER_VALID
                        else None
                    ),
                )
                for ability in reader.abilities[: reader.inventory_info.ability_count]
            },
            "actors": {
                npc.instance_id: (
                    npc.config_id if npc.flags & NPCFlags.CONFIG else None,
                    npc.health_current if npc.flags & NPCFlags.HEALTH else None,
                    npc.health_max if npc.flags & NPCFlags.HEALTH else None,
                    animation.action_sequence if animation.valid else None,
                    animation.movement_sequence if animation.valid else None,
                )
                for npc, animation in zip(
                    reader.npcs[: info.npc_count], reader.npc_animations[: info.npc_count]
                )
            },
            "actor_tiles": {
                instance: (
                    pose.tile.x,
                    pose.tile.y,
                    pose.tile.plane,
                    bool(pose.valid_mask & SpatialFlags.SAME_WORLD),
                )
                for instance, pose in poses.items()
            },
            "hazard_candidates": {
                hazard.instance_id: (
                    hazard.config_id,
                    hazard.kind,
                    hazard.variant,
                    bool(hazard.flags & HazardFlags.MECHANICS_VALIDATED),
                )
                for hazard in reader.hazards[: reader.hazard_info.count]
                if not hazard.flags & HazardFlags.SCENE_LOCATION
            },
            "beam_tiles": sorted(
                (hazard.kind, hazard.tile.x, hazard.tile.y, hazard.tile.plane, hazard.config_id)
                for hazard in beams
            ),
            "beam_ground_heights": sorted(
                (
                    hazard.tile.x,
                    hazard.tile.y,
                    hazard.tile.plane,
                    reader.hazard_terrain_height(hazard),
                )
                for hazard in beams
                if hazard.flags & HazardFlags.TERRAIN_HEIGHT
            ),
            "camera_viewport": (
                (camera.viewport_width, camera.viewport_height, *camera.physical_viewport_size)
                if camera.physical_viewport_size is not None
                else None
            ),
            "hazard_observation_reason": reader.hazard_info.reason,
            "hazard_candidates_dropped": reader.hazard_info.dropped_count,
            "player_hit_status": reader.player_hit_info.status,
        }
        # Replace changed fields as units, so removals and unknown values survive replay.
        changes = {name: value for name, value in state.items() if value != self.previous.get(name)}
        if not self.previous:
            changes = state
        self.previous = state
        if changes:
            self.write("state", observed_ns=self.relative_ns(info.monotonic_ns), changes=changes)
        if info.monotonic_ns:
            self.observe_prayer(info.monotonic_ns, state["active_prayer"])
        cleanse = reader.telos_cleanse
        if self.engaged and cleanse.valid and self.client_logic_tick is not None:
            message = (self.session_index, cleanse.message_id, cleanse.client_logic_tick)
            age = (self.client_logic_tick - cleanse.client_logic_tick) & 0xFFFFFFFF
            if age <= CLEANSE_MAX_AGE_CLIENT_TICKS and message != self.last_cleanse:
                self.last_cleanse = message
                self.write(
                    "anima_stream_cleanse_observed",
                    message_id=cleanse.message_id,
                    observed_client_tick=cleanse.client_logic_tick,
                    phase_candidate=phase,
                    tile=state["tile"],
                )
        if action_loss or hit_loss or npc_hit_loss:
            self.write(
                "queue_loss", npc_actions=action_loss, player_hits=hit_loss, npc_hits=npc_hit_loss
            )
        for event in actions:
            source = (event.pid, event.process_creation_time, event.scene_epoch)
            self.write(
                "npc_animation",
                native_sequence=event.sequence,
                instance_id=event.instance_id,
                config_id=event.config_id,
                animation_id=event.action_sequence,
                previous_animation_id=event.previous_action_sequence,
                observed_ns=self.relative_ns(event.observed_ns),
                observed_client_tick=event.logic_tick,
                application_stamp=(
                    event.application_stamp
                    if event.flags & NPCActionFlags.HAS_APPLICATION_STAMP
                    else None
                ),
                source_frame_sequence=event.sample_sequence,
                same_session=source == session,
            )
        for event in hits:
            source = (event.pid, event.process_creation_time, event.scene_epoch)
            overhead = None
            if source == session:
                if event.sample_sequence == info.sequence:
                    overhead = state["active_prayer"]
                elif intervening_prayers is not None:
                    overhead = intervening_prayers.get(event.sample_sequence)
            self.write(
                "player_hitmark",
                native_sequence=event.sequence,
                observed_ns=self.relative_ns(event.observed_ns),
                source_frame_sequence=event.sample_sequence,
                same_session=source == session,
                overhead_at_sample=overhead,
                hit=hit_record(event.hit),
            )
        for event in npc_hits:
            self.write(
                "npc_hitmark",
                native_sequence=event.sequence,
                instance_id=event.instance_id,
                config_id=event.config_id,
                observed_ns=self.relative_ns(event.observed_at_ns),
                same_process=(event.pid, event.process_creation_time) == session[:2],
                hit=hit_record(event.hit),
            )
