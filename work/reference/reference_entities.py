"""Read the local player's tile and the loaded NPCs: identity, health and hit mark rings.

This is the independent Python reference for research and parity benchmarks;
the runtime decoder is the C library.
"""

import struct

from reference_memory import MemoryReadError

MAX_NAME_BYTES = 512
CLIENT_STRING_BYTES = 24
CLIENT_STRING_INLINE_CAPACITY = 23
CLIENT_STRING_HEAP_FLAG = 0x80
NPC_NODE_ACTOR_OFFSET = 16
NPC_NODE_NEXT_OFFSET = 24
NPC_ACTOR_RUNTIME_ID_OFFSET = 0x88
NPC_MANAGER_TABLE_OFFSET = 16
NPC_MANAGER_BUCKET_COUNT_OFFSET = 24
NPC_CONFIG_ID_OFFSET = 8
MAX_NPCS = 1024
MAX_RUNTIME_ID = 65535
MAX_BUCKETS = 1000000
MAX_CHAIN_LENGTH = 1024
MAX_CONFIG_ID = 1000000
MAX_NPC_HEALTH = 0xFFFFFF
HIT_RING_HEADER_BYTES = 40
HIT_RECORD_BYTES = 24
MAX_HITS_PER_NPC = 64
MAX_HIT_TYPE_ID = 1000000
MAX_HIT_DURATION_TICKS = 100000
MAX_TILE_COORDINATE = 16384
FINE_UNITS_PER_TILE = 512
# Keep reported hit identities for this many ticks after their end tick.
SEEN_HIT_RETENTION_TICKS = 250
LIST_CHANGED_MESSAGE = "NPC list changed while reading"


def parse_offset(value):
    """Return an integer from a JSON number or a "0x"-prefixed string."""
    return int(value, 0) if isinstance(value, str) else value


def signed_tick_difference(later, earlier):
    """Interpret the wrapped 32-bit difference later - earlier as a signed interval."""
    return ((later - earlier + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def decode_hit(values, clock, tick_ms):
    """Decode one ring record; return None for an empty or expired slot."""
    type_id, amount, end_tick, secondary_type_id, secondary_amount, duration_ticks = values
    if type_id < 0 or duration_ticks <= 0 or signed_tick_difference(end_tick, clock) <= 0:
        return None
    if (
        type_id > MAX_HIT_TYPE_ID
        or not 0 <= amount <= 0x7FFFFFFF
        or duration_ticks > MAX_HIT_DURATION_TICKS
    ):
        raise MemoryReadError("Invalid hitmark record")
    start_tick = (end_tick - duration_ticks) & 0xFFFFFFFF
    return {
        "type_id": type_id,
        "amount": amount,
        "secondary_type_id": None if secondary_type_id < 0 else secondary_type_id,
        "secondary_amount": (
            None if secondary_type_id < 0 or secondary_amount < 0 else secondary_amount
        ),
        "start_client_tick": start_tick,
        "end_client_tick": end_tick & 0xFFFFFFFF,
        "duration_ticks": duration_ticks,
        "duration_ms": duration_ticks * tick_ms,
        "state": "active" if signed_tick_difference(clock, start_tick) >= 0 else "scheduled",
    }


def decode_health(current, maximum):
    """Return the health record; a maximum of 0 means the server has not sent it."""
    unknown = {"current": None, "max": None, "percent": None, "source": "npc_stats"}
    if maximum == 0:
        return dict(unknown, reason="not_transmitted")
    if not 0 < maximum <= MAX_NPC_HEALTH or not 0 <= current <= maximum:
        return dict(unknown, reason="invalid_stat_pair")
    return {
        "current": current,
        "max": maximum,
        "percent": round(current / maximum * 100, 2),
        "source": "npc_stats",
        "reason": None,
    }


class PlayerPositionReader:
    """Reads the Compatibility Mode player tile."""

    ATTEMPTS = 4

    def __init__(self, memory, layout):
        self.memory = memory
        self.layout = {name: parse_offset(value) for name, value in layout.items()}

    def _find_player(self):
        memory, layout = self.memory, self.layout
        client = memory.pointer(memory.module.base + layout["client_ptr_rva"])
        if not client or memory.i32(client + layout["state"]) != layout["in_game_state"]:
            return None
        player_context = memory.pointer(client + layout["player_context"])
        if not player_context or memory.pointer(player_context) != client:
            return None
        player_override = memory.pointer(player_context + layout["player_override"])
        if player_override:
            return player_override
        player_index = memory.i32(player_context + layout["player_index"])
        player_table = memory.pointer(client + layout["player_table"])
        if not player_table:
            return None
        slots_begin, slots_end = memory.unpack(player_table + layout["table_range"], "<QQ")
        if not slots_begin or not 0 <= player_index < (slots_end - slots_begin) // 8:
            return None
        player_slot = memory.pointer(slots_begin + player_index * 8)
        return memory.pointer(player_slot + layout["slot_entity"]) if player_slot else None

    def _read_tile(self):
        memory, layout = self.memory, self.layout
        player = self._find_player()
        if not player:
            return None
        plane = memory.i32(player + layout["plane"])
        footprint_size = memory.i32(player + layout["size"])
        fine_x, _height, fine_z = memory.unpack(player + layout["fine_position"], "<3f")
        # Match the client's footprint origin. Fine Y is the height.
        tile_x = fine_x / FINE_UNITS_PER_TILE - footprint_size // 2
        tile_y = fine_z / FINE_UNITS_PER_TILE - footprint_size // 2
        if not (
            0 <= plane <= 3
            and footprint_size >= 1
            and 0 <= tile_x < MAX_TILE_COORDINATE
            and 0 <= tile_y < MAX_TILE_COORDINATE
        ):
            return None
        return player, int(tile_x), int(tile_y), plane

    def read(self):
        """Return the tile after two consecutive agreeing observations, or None."""
        previous_observation = None
        for _ in range(self.ATTEMPTS):
            try:
                observation = self._read_tile()
            except MemoryReadError:
                observation = None
            if observation is not None and observation == previous_observation:
                _, tile_x, tile_y, plane = observation
                return {"x": tile_x, "y": tile_y, "plane": plane}
            previous_observation = observation
        return None


class NPCReader:
    """Reads the loaded NPC registry and reports each active hit once."""

    def __init__(self, memory, profile):
        self.memory = memory
        self.profile = profile
        self.layout = profile["npcs"]
        self.instances = {}
        self.next_instance_id = 1
        self.seen_hits = {}
        self.registry_context = None

    def _offset(self, name):
        return parse_offset(self.layout[name])

    def reset(self):
        self.instances.clear()
        self.seen_hits.clear()
        self.registry_context = None

    def read_name(self, address):
        """Read a client string object: inline, or heap-backed when the tag's high bit is set."""
        data = self.memory.read(address, CLIENT_STRING_BYTES)
        tag = data[CLIENT_STRING_BYTES - 1]
        heap_backed = bool(tag & CLIENT_STRING_HEAP_FLAG)
        if heap_backed:
            length = struct.unpack_from("<Q", data, 8)[0]
        else:
            length = CLIENT_STRING_INLINE_CAPACITY - tag
        if not 0 <= length <= MAX_NAME_BYTES:
            raise MemoryReadError("Invalid NPC name length")
        if not length:
            return None
        if heap_backed:
            data = self.memory.read(struct.unpack_from("<Q", data)[0], length)
        else:
            data = data[:length]
        try:
            name = data.decode("utf-8")
        except UnicodeError as error:
            raise MemoryReadError("Invalid NPC name encoding") from error
        if any(ord(character) < 32 for character in name):
            raise MemoryReadError("Invalid NPC name")
        return name

    def find_node(self, table, bucket_count, sentinel, runtime_id):
        """Walk the bucket chain of runtime_id and return its node address."""
        memory = self.memory
        node = memory.unpack(table + (runtime_id % bucket_count) * 8, "<Q")[0]
        visited = set()
        while node and node != sentinel:
            if node in visited or len(visited) >= MAX_CHAIN_LENGTH:
                raise MemoryReadError("NPC bucket-chain cycle")
            visited.add(node)
            if memory.u32(node) == runtime_id:
                return node
            node = memory.unpack(node + NPC_NODE_NEXT_OFFSET, "<Q")[0]
        raise MemoryReadError("NPC left the registry")

    def read_hits(self, actor, clock):
        """Return the current hit records of an actor, verified against a torn ring."""
        memory = self.memory
        hitmarks_offset = self._offset("hitmarks_offset")
        ring = memory.pointer(actor + hitmarks_offset)
        if not ring:
            return []
        header = memory.read(ring, HIT_RING_HEADER_BYTES)
        capacity = struct.unpack_from("<I", header, 4)[0]
        count, data = struct.unpack_from("<QQ", header, 24)
        if not 0 <= count <= capacity <= MAX_HITS_PER_NPC or (count and not data):
            raise MemoryReadError("Invalid NPC hitmark ring")
        records = memory.read(data, count * HIT_RECORD_BYTES) if count else b""
        hits = []
        for slot in range(count):
            values = struct.unpack_from("<6i", records, slot * HIT_RECORD_BYTES)
            decoded = decode_hit(values, clock, self.profile["logic_tick_ms"])
            if decoded is not None:
                decoded["slot"] = slot
                hits.append(decoded)
        # The actor must still own the same ring, and the payload must be unchanged.
        if (
            memory.pointer(actor + hitmarks_offset) != ring
            or memory.read(ring + 24, 16) != header[24:40]
            or (count and memory.read(data, count * HIT_RECORD_BYTES) != records)
        ):
            raise MemoryReadError("NPC hitmark ring changed while reading")
        return hits

    def read(self, world_state, clock):
        """Return (NPC section, hit section); a list that changes is retried once."""
        for attempt in range(2):
            try:
                return self._read_once(world_state, clock)
            except MemoryReadError as error:
                if attempt or str(error) != LIST_CHANGED_MESSAGE:
                    raise

    def _read_runtime_ids(self, manager):
        memory = self.memory
        count = memory.u32(manager + self._offset("count_offset"))
        if not 0 <= count <= MAX_NPCS:
            raise MemoryReadError("Invalid NPC count")
        ids_address = manager + self._offset("indices_offset")
        ids_data = memory.read(ids_address, count * 4) if count else b""
        runtime_ids = struct.unpack(f"<{count}I", ids_data)
        out_of_range = any(runtime_id > MAX_RUNTIME_ID for runtime_id in runtime_ids)
        if len(set(runtime_ids)) != count or out_of_range:
            raise MemoryReadError("Invalid NPC index list")
        return count, ids_address, ids_data, runtime_ids

    def _read_entity(self, table, bucket_count, sentinel, runtime_id, clock):
        """Return (actor address, config ID, name, health, hit marks) of one NPC."""
        memory = self.memory
        node = self.find_node(table, bucket_count, sentinel, runtime_id)
        actor = memory.pointer(node + NPC_NODE_ACTOR_OFFSET)
        if not actor:
            raise MemoryReadError("NPC object unavailable")
        expected_vtable = memory.module.base + self._offset("entity_vtable_rva")

        def identity_matches():
            return (
                memory.pointer(actor) == expected_vtable
                and memory.unpack(actor + NPC_ACTOR_RUNTIME_ID_OFFSET, "<H")[0] == runtime_id
            )

        if not identity_matches():
            raise MemoryReadError("NPC identity mismatch")
        config = memory.pointer(actor + self._offset("config_override_offset")) or memory.pointer(
            actor + self._offset("config_offset")
        )
        config_id = memory.i32(config + NPC_CONFIG_ID_OFFSET) if config else None
        if config_id is not None and not 0 <= config_id <= MAX_CONFIG_ID:
            raise MemoryReadError("Invalid NPC config ID")
        name = self.read_name(actor + self._offset("name_offset"))
        health = decode_health(
            memory.i32(actor + self._offset("health_current_offset")),
            memory.i32(actor + self._offset("health_max_offset")),
        )
        hit_marks = self.read_hits(actor, clock)
        if memory.pointer(node + NPC_NODE_ACTOR_OFFSET) != actor or not identity_matches():
            raise MemoryReadError("NPC changed while reading")
        return actor, config_id, name, health, hit_marks

    def _read_once(self, world_state, clock):
        memory = self.memory
        manager = memory.pointer(world_state + self._offset("manager_offset"))
        if not manager:
            self.reset()
            raise MemoryReadError("NPC manager unavailable")
        if memory.pointer(manager) != memory.module.base + self._offset("manager_vtable_rva"):
            raise MemoryReadError("NPC manager vtable mismatch")
        registry_context = (world_state, manager)
        if registry_context != self.registry_context:
            self.reset()
            self.registry_context = registry_context

        count, ids_address, ids_data, runtime_ids = self._read_runtime_ids(manager)
        table = memory.pointer(manager + NPC_MANAGER_TABLE_OFFSET)
        bucket_count = memory.unpack(manager + NPC_MANAGER_BUCKET_COUNT_OFFSET, "<Q")[0]
        if not table or not 1 <= bucket_count <= MAX_BUCKETS:
            raise MemoryReadError("NPC table unavailable")
        sentinel = memory.unpack(table + bucket_count * 8, "<Q")[0]

        entities, active_hits, newly_observed, skipped_count = [], [], [], 0
        # Stage identities and seen hits until the loaded NPC list is rechecked.
        staged_instances, staged_seen_hits = {}, {}
        for runtime_id in runtime_ids:
            try:
                actor, config_id, name, health, hit_marks = self._read_entity(
                    table, bucket_count, sentinel, runtime_id, clock
                )
            except OSError:
                skipped_count += 1
                # An unreadable actor may still be present in the loaded list.
                if runtime_id in self.instances:
                    staged_instances[runtime_id] = self.instances[runtime_id]
                continue
            identity = self.instances.get(runtime_id)
            if identity is None or identity[0] != actor:
                identity = (actor, self.next_instance_id)
                self.next_instance_id += 1
            staged_instances[runtime_id] = identity
            instance_id = identity[1]
            entities.append(
                {
                    "runtime_id": runtime_id,
                    "instance_id": instance_id,
                    "config_id": config_id,
                    "name": name,
                    "health": health,
                    "hit_splats": hit_marks,
                }
            )
            for hit in hit_marks:
                if hit["state"] != "active":
                    continue
                event = dict(
                    hit,
                    runtime_id=runtime_id,
                    instance_id=instance_id,
                    config_id=config_id,
                    name=name,
                )
                active_hits.append(event)
                hit_key = (
                    instance_id,
                    hit["slot"],
                    hit["type_id"],
                    hit["amount"],
                    hit["end_client_tick"],
                    hit["secondary_type_id"],
                    hit["secondary_amount"],
                )
                staged_seen_hits[hit_key] = hit["end_client_tick"]
                if hit_key not in self.seen_hits:
                    newly_observed.append(event)

        count_after = memory.u32(manager + self._offset("count_offset"))
        ids_data_after = memory.read(ids_address, count * 4) if count else b""
        manager_after = memory.pointer(world_state + self._offset("manager_offset"))
        if count_after != count or ids_data_after != ids_data or manager_after != manager:
            raise MemoryReadError(LIST_CHANGED_MESSAGE)

        self.instances = staged_instances
        self.seen_hits = {
            key: end_tick
            for key, end_tick in self.seen_hits.items()
            if signed_tick_difference(clock, end_tick) < SEEN_HIT_RETENTION_TICKS
        }
        self.seen_hits.update(staged_seen_hits)
        entities.sort(
            key=lambda entity: (
                entity["health"]["max"] is None,
                (entity["name"] or "").casefold(),
                entity["runtime_id"],
            )
        )
        status = "partial" if skipped_count else "available"
        reason = "some_entities_changed" if skipped_count else None
        npc_section = {
            "status": status,
            "entities": entities,
            "loaded_count": count,
            "read_count": len(entities),
            "skipped_count": skipped_count,
            "reason": reason,
            "scope": "loaded_scene",
            "source": "native_memory",
        }
        hit_section = {
            "status": status,
            "active": active_hits,
            "newly_observed": newly_observed,
            "reason": reason,
            "coverage": "sampled_client_hitmark_rings",
            "attacker_identity": "not_available",
        }
        return npc_section, hit_section
