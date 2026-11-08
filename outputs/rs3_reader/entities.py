"""Read the local player's tile, NPC identity, HP statistics and hitmark rings."""
import struct
from winmem import MemoryReadError

def offset(value):
    return int(value, 0) if isinstance(value, str) else value

def delta(a, b):
    return ((a-b+0x80000000) & 0xffffffff)-0x80000000

def decode_hit(values, clock, tick_ms):
    kind, amount, end, secondary_kind, secondary_amount, duration = values
    if kind < 0 or duration <= 0 or delta(end, clock) <= 0:
        return None
    if kind > 1000000 or not 0 <= amount <= 0x7fffffff or duration > 100000:
        raise MemoryReadError("Invalid hitmark record")
    start = (end-duration) & 0xffffffff
    return {"type_id": kind, "amount": amount,
            "secondary_type_id": None if secondary_kind < 0 else secondary_kind,
            "secondary_amount": None if secondary_kind < 0 or secondary_amount < 0 else secondary_amount,
            "start_client_tick": start, "end_client_tick": end & 0xffffffff,
            "duration_ticks": duration, "duration_ms": duration*tick_ms,
            "state": "active" if delta(clock, start) >= 0 else "scheduled"}

def decode_health(current, maximum):
    if maximum == 0:
        return {"current": None, "max": None, "percent": None,
                "source": "npc_stats", "reason": "not_transmitted"}
    if not 0 < maximum <= 0xffffff or not 0 <= current <= maximum:
        return {"current": None, "max": None, "percent": None,
                "source": "npc_stats", "reason": "invalid_stat_pair"}
    return {"current": current, "max": maximum, "percent": round(current/maximum*100, 2),
            "source": "npc_stats", "reason": None}

class PlayerPositionReader:
    def __init__(self, memory, spec):
        self.memory = memory
        self.spec = {name: offset(value) for name, value in spec.items()}

    def _player(self):
        m, s = self.memory, self.spec
        client = m.pointer(m.module.base + s["client_ptr_rva"])
        if not client or m.i32(client + s["state"]) != s["in_game_state"]:
            return None
        context = m.pointer(client + s["player_context"])
        if not context or m.pointer(context) != client:
            return None
        player = m.pointer(context + s["player_override"])
        if player:
            return player
        index = m.i32(context + s["player_index"])
        table = m.pointer(client + s["player_table"])
        if not table:
            return None
        start, end = m.unpack(table + s["table_range"], "<QQ")
        if not start or not 0 <= index < (end-start)//8:
            return None
        slot = m.pointer(start + index*8)
        return m.pointer(slot + s["slot_entity"]) if slot else None

    def _sample(self):
        m, s = self.memory, self.spec
        player = self._player()
        if not player:
            return None
        plane = m.i32(player + s["plane"])
        size = m.i32(player + s["size"])
        fine_x, _, fine_z = m.unpack(player + s["fine_position"], "<3f")
        # Match the client's CoordGrid footprint origin. Fine Y is height.
        x, y = fine_x/512 - size//2, fine_z/512 - size//2
        if not (0 <= plane <= 3 and size >= 1 and 0 <= x < 12800 and 0 <= y < 12800):
            return None
        return player, int(x), int(y), plane

    def read(self):
        previous = None
        for _ in range(4):
            try:
                current = self._sample()
            except MemoryReadError:
                current = None
            if current is not None and current == previous:
                _, x, y, plane = current
                return {"x": x, "y": y, "plane": plane}
            previous = current
        return None

class NPCReader:
    def __init__(self, memory, profile):
        self.memory, self.profile = memory, profile
        self.spec = profile["npcs"]
        self.instances = {}
        self.next_instance = 1
        self.seen_hits = {}
        self.context = None

    def reset(self):
        self.instances.clear()
        self.seen_hits.clear()
        self.context = None

    def string(self, address):
        data = self.memory.read(address, 24)
        tag = data[23]
        size = struct.unpack_from("<Q", data, 8)[0] if tag & 128 else 23-tag
        if not 0 <= size <= 512:
            raise MemoryReadError("Invalid NPC name length")
        if not size:
            return None
        if tag & 128:
            data = self.memory.read(struct.unpack_from("<Q", data)[0], size)
        else:
            data = data[:size]
        try:
            value = data.decode("utf-8")
        except UnicodeError as error:
            raise MemoryReadError("Invalid NPC name encoding") from error
        if any(ord(c) < 32 for c in value):
            raise MemoryReadError("Invalid NPC name")
        return value

    def node(self, table, count, sentinel, uid):
        memory = self.memory
        node = memory.unpack(table+(uid % count)*8, "<Q")[0]
        seen = set()
        while node and node != sentinel:
            if node in seen or len(seen) >= 1024:
                raise MemoryReadError("NPC bucket-chain cycle")
            seen.add(node)
            if memory.u32(node) == uid:
                return node
            node = memory.unpack(node+24, "<Q")[0]
        raise MemoryReadError("NPC left the registry")

    def hits(self, actor, clock):
        memory, spec = self.memory, self.spec
        ring = memory.pointer(actor+offset(spec["hitmarks_offset"]))
        if not ring:
            return []
        header = memory.read(ring, 40)
        capacity = struct.unpack_from("<I", header, 4)[0]
        count, data = struct.unpack_from("<QQ", header, 24)
        if not 0 <= count <= capacity <= 64 or (count and not data):
            raise MemoryReadError("Invalid NPC hitmark ring")
        raw = memory.read(data, count*24) if count else b""
        hits = []
        for slot in range(count):
            decoded = decode_hit(struct.unpack_from("<6i", raw, slot*24), clock,
                                 self.profile["logic_tick_ms"])
            if decoded is not None:
                decoded["slot"] = slot
                hits.append(decoded)
        if (memory.pointer(actor+offset(spec["hitmarks_offset"])) != ring or memory.read(ring+24, 16) != header[24:40]
                or (count and memory.read(data, count*24) != raw)):
            raise MemoryReadError("NPC hitmark ring changed while reading")
        return hits

    def read(self, state, clock):
        for attempt in range(2):
            try:
                return self._read(state, clock)
            except MemoryReadError as error:
                if attempt or str(error) != "NPC list changed while reading":
                    raise

    def _read(self, state, clock):
        memory, spec = self.memory, self.spec
        manager = memory.pointer(state+offset(spec["manager_offset"]))
        if not manager:
            self.reset()
            raise MemoryReadError("NPC manager unavailable")
        if memory.pointer(manager) != memory.module.base+offset(spec["manager_vtable_rva"]):
            raise MemoryReadError("NPC manager vtable mismatch")
        context = (state, manager)
        if context != self.context:
            self.reset()
            self.context = context
        count = memory.u32(manager+offset(spec["count_offset"]))
        if not 0 <= count <= 1024:
            raise MemoryReadError("Invalid NPC count")
        ids_address = manager+offset(spec["indices_offset"])
        ids_data = memory.read(ids_address, count*4) if count else b""
        ids = struct.unpack("<%dI" % count, ids_data)
        if len(set(ids)) != count or any(uid > 65535 for uid in ids):
            raise MemoryReadError("Invalid NPC index list")
        table = memory.pointer(manager+16)
        buckets = memory.unpack(manager+24, "<Q")[0]
        if not table or not 1 <= buckets <= 1000000:
            raise MemoryReadError("NPC table unavailable")
        sentinel = memory.unpack(table+buckets*8, "<Q")[0]
        entities, active, newly_observed, errors = [], [], [], 0
        instances = {}
        pending_seen = {}
        for uid in ids:
            try:
                node = self.node(table, buckets, sentinel, uid)
                actor = memory.pointer(node+16)
                if not actor:
                    raise MemoryReadError("NPC object unavailable")
                expected = memory.module.base+offset(spec["entity_vtable_rva"])
                if memory.pointer(actor) != expected or memory.unpack(actor+0x88, "<H")[0] != uid:
                    raise MemoryReadError("NPC identity mismatch")
                config = memory.pointer(actor+offset(spec["config_override_offset"])) or memory.pointer(actor+offset(spec["config_offset"]))
                config_id = memory.i32(config+8) if config else None
                if config_id is not None and not 0 <= config_id <= 1000000:
                    raise MemoryReadError("Invalid NPC config ID")
                name = self.string(actor+offset(spec["name_offset"]))
                health = decode_health(memory.i32(actor+offset(spec["health_current_offset"])),
                                       memory.i32(actor+offset(spec["health_max_offset"])))
                hitmarks = self.hits(actor, clock)
                if memory.pointer(node+16) != actor or memory.pointer(actor) != expected or memory.unpack(actor+0x88, "<H")[0] != uid:
                    raise MemoryReadError("NPC changed while reading")
                identity = self.instances.get(uid)
                if identity is None or identity[0] != actor:
                    identity = (actor, self.next_instance)
                    self.next_instance += 1
                instances[uid] = identity
                item = {"runtime_id": uid, "instance_id": identity[1], "config_id": config_id,
                        "name": name, "health": health, "hit_splats": hitmarks}
                entities.append(item)
                for hit in hitmarks:
                    if hit["state"] != "active":
                        continue
                    event = dict(hit, runtime_id=uid, instance_id=identity[1], config_id=config_id, name=name)
                    active.append(event)
                    key = (identity[1], hit["slot"], hit["type_id"], hit["amount"], hit["end_client_tick"],
                           hit["secondary_type_id"], hit["secondary_amount"])
                    pending_seen[key] = hit["end_client_tick"]
                    if key not in self.seen_hits:
                        newly_observed.append(event)
            except OSError:
                errors += 1
                if uid in self.instances:
                    instances[uid] = self.instances[uid]
        after_count = memory.u32(manager+offset(spec["count_offset"]))
        after_ids = memory.read(ids_address, count*4) if count else b""
        if (after_count != count or after_ids != ids_data or
                memory.pointer(state+offset(spec["manager_offset"])) != manager):
            raise MemoryReadError("NPC list changed while reading")
        self.instances = instances
        self.seen_hits = {key:end for key,end in self.seen_hits.items() if delta(clock,end) < 250}
        self.seen_hits.update(pending_seen)
        entities.sort(key=lambda e:(e["health"]["max"] is None, (e["name"] or "").casefold(), e["runtime_id"]))
        return ({"status": "partial" if errors else "available", "entities": entities,
                 "loaded_count": count, "read_count": len(entities), "skipped_count": errors,
                 "reason": "some_entities_changed" if errors else None,
                 "scope": "loaded_scene", "source": "native_memory"},
                {"status": "partial" if errors else "available", "active": active, "newly_observed": newly_observed,
                 "reason": "some_entities_changed" if errors else None,
                 "coverage": "sampled_client_hitmark_rings", "attacker_identity": "not_available"})
