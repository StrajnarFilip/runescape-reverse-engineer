"""Convert C ABI observations to the existing tracker/JSONL schema."""
import time
from native import Reader, VARIABLES
from inventory_telemetry import extend_record


def text(value):
    return value.decode("utf-8")


def hit_record(hit):
    return {"slot": hit.slot, "type_id": hit.type_id, "amount": hit.amount,
            "secondary_type_id": None if hit.secondary_type_id < 0 else hit.secondary_type_id,
            "secondary_amount": None if hit.secondary_type_id < 0 or hit.secondary_amount < 0 else hit.secondary_amount,
            "start_client_tick": hit.start_tick, "end_client_tick": hit.end_tick,
            "duration_ticks": hit.duration_ticks, "duration_ms": hit.duration_ticks*20,
            "state": "active" if hit.state == 1 else "scheduled"}


def event_record(event):
    return dict(hit_record(event.hit), native_event_sequence=event.sequence,
                observed_at_ms=event.observed_at_ns//1000000, pid=event.pid,
                process_creation_time=event.process_creation_time, instance_id=event.instance_id,
                runtime_id=event.runtime_id, config_id=None if event.config_id < 0 else event.config_id,
                name=text(event.name) or None)


class NativeTelemetry:
    def __init__(self, pid=None, worker_hz=100, library=None):
        self.reader = Reader(pid, worker_hz, library)

    def close(self):
        self.reader.close()

    @staticmethod
    def unavailable(message):
        return {"schema_version": 1, "timestamp_ms": time.time_ns()//1000000, "backend": "c",
                "pid": None, "status": "unavailable", "reason": message, "health": None,
                "prayer": None, "adrenaline_percent": None, "gcd": None, "telos": None,
                "npcs": None, "hit_splats": None, "position": None}

    def sample(self, include_raw=False):
        info, player, npcs, hits = self.reader.sample()
        # Drain independently of frame availability so earlier observations survive a failed sample.
        events, lost = self.reader.drain_events()
        new_events = [event_record(e) for e in events]
        # Snapshot age uses the same native monotonic clock as the sample timestamp.
        native = {"sample_sequence": info.sequence, "worker_hz": self.reader.worker_hz,
                  "sample_duration_ms": round(info.sample_duration_ns/1e6, 3),
                  "read_calls": info.read_calls, "read_bytes": info.read_bytes,
                  "event_sequence": self.reader.cursor, "events_lost": lost,
                  "snapshot_age_ms": round(max(0, self.reader.lib.rs3_monotonic_ns()-info.monotonic_ns)/1e6, 3)}
        tile = self.reader.position
        position = {"x": tile.x, "y": tile.y, "plane": tile.plane} if tile.valid else None
        version = text(self.reader.lib.rs3_snapshot_client_version(self.reader.snapshot))
        available = info.status in (1, 2)
        if not available or not player.prayer_base_level:
            # The position profile publishes a tile and leaves combat fields unavailable.
            reason = text(info.message) or (None if available else "Waiting for a native sample")
            result = self.unavailable(reason)
            result.update(timestamp_ms=info.timestamp_ns//1000000, pid=info.pid or None,
                          native=native, process_creation_time=info.process_creation_time,
                          position=position, status="partial" if available else "unavailable",
                          client_version=version, executable_sha256=text(info.executable_sha256),
                          source="native_memory", read_duration_ms=native["sample_duration_ms"])
            # Observations from earlier frames retain their own process/timestamp.
            result["hit_splats"] = {"status": "unavailable", "active": None,
                                   "newly_observed": new_events, "events_lost": lost}
            return extend_record(result, self.reader)
        raw = {name: player.raw_variables[i] if player.valid_variables & (1 << i) else None
               for i, name in enumerate(VARIABLES)}

        def field(value, changed):
            return {"value": value, "reason": "not_set" if value is None else None,
                    "last_change_age_ms": None if value is None else
                    round(max(0, info.monotonic_ns-changed)/1e6, 1)}

        # The low 15 bits contain prayer points in tenths. Adrenaline is in tenths of a percent.
        prayer = None if raw["prayer_packed"] is None else (raw["prayer_packed"] & 32767)/10
        adrenaline = None if raw["adrenaline"] is None else raw["adrenaline"]/10
        entities, active = [], []
        for i in range(info.npc_count):
            npc = npcs[i]
            known = bool(npc.flags & 4)
            health = {"current": npc.health_current if known else None,
                      "max": npc.health_max if known else None,
                      "percent": round(npc.health_current/npc.health_max*100, 2) if known else None,
                      "source": "npc_stats", "reason": None if known else
                      ("not_transmitted" if npc.health_reason == 7 else "invalid_stat_pair")}
            item = {"runtime_id": npc.runtime_id, "instance_id": npc.instance_id,
                    "config_id": npc.config_id if npc.flags & 2 else None,
                    "name": text(npc.name) if npc.flags & 1 else None,
                    "health": health, "hit_splats": []}
            # Each NPC references a slice of the snapshot's shared hit buffer.
            for j in range(npc.hit_offset, npc.hit_offset+npc.hit_count):
                hit = hit_record(hits[j])
                item["hit_splats"].append(hit)
                if hit["state"] == "active":
                    active.append(dict(hit, **{k: item[k] for k in ("runtime_id", "instance_id", "config_id", "name")}))
            entities.append(item)
        entities.sort(key=lambda e: (e["health"]["max"] is None, (e["name"] or "").casefold(), e["runtime_id"]))
        npc_status = "unavailable" if info.reason == 2 else ("partial" if info.skipped_npcs else "available")
        reason = text(info.message) or ("some_entities_changed" if info.skipped_npcs else None)
        gcd_reason = {0: None, 1: "not_set", 6: "inconsistent_clock"}.get(player.gcd_reason, "unavailable")
        record = {
            "schema_version": 1, "backend": "c", "timestamp_ms": info.timestamp_ns//1000000,
            "monotonic_ms": round(info.monotonic_ns/1e6, 3), "pid": info.pid,
            "process_creation_time": info.process_creation_time, "native": native,
            "executable_sha256": text(info.executable_sha256), "client_version": version,
            "source": "native_memory", "status": "partial", "read_duration_ms": native["sample_duration_ms"],
            "position": position,
            "freshness": {"observed_at_ms": info.timestamp_ns//1000000,
                          "client_logic_tick": info.logic_tick, "clock_stalled_ms": round(player.clock_stalled_ns/1e6, 1),
                          "server_update_age_ms": None},
            "health": {"current": field(raw["health_current"], player.hp_changed_ns), "max": raw["health_max"]},
            "prayer": {"current": field(prayer, player.prayer_changed_ns),
                       "display_current": None if prayer is None else int(prayer), "max": player.prayer_base_level*10},
            "adrenaline_percent": field(adrenaline, player.adrenaline_changed_ns),
            "gcd": {"remaining_ms": None if gcd_reason else player.gcd_remaining_ms,
                    "duration_ms": None if gcd_reason else player.gcd_duration_ms, "reason": gcd_reason,
                    "clock_raw": info.logic_tick, "start_raw": raw["gcd_start"], "end_raw": raw["gcd_end"],
                    "duration_raw": raw["gcd_duration"], "client_start_raw": raw["gcd_client_start"],
                    "client_end_raw": raw["gcd_client_end"]},
            "npcs": {"status": npc_status, "entities": None if npc_status == "unavailable" else entities,
                     "loaded_count": info.loaded_npcs, "read_count": info.npc_count, "skipped_count": info.skipped_npcs,
                     "reason": reason, "scope": "loaded_scene", "source": "native_memory"},
            "hit_splats": {"status": npc_status, "active": None if npc_status == "unavailable" else active,
                           "newly_observed": new_events, "events_lost": lost, "reason": reason,
                           "coverage": "sampled_client_hitmark_rings", "attacker_identity": "not_available"},
            # Preserve the packed stage bits for research while the entity mapping is pending.
            "telos": {"present": None, "animation_id": None, "action": None, "phase": None,
                      "stage_raw": None if raw["telos_packed"] is None else (raw["telos_packed"] >> 21) & 7,
                      "reason": "entity_mapping_pending"},
        }
        if include_raw:
            record["raw_variables"] = raw
        return extend_record(record, self.reader)
