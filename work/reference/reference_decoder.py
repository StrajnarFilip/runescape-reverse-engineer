"""Version-pinned native-memory decoding, implemented independently of the C library.

The domain registry and the sparse value tables were recovered from the pinned
executable with Ghidra. Every remote operation is a read; game functions are
never called.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

from reference_entities import NPCReader, PlayerPositionReader
from reference_memory import MemoryReadError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_FILES = ("profile_951_3.json", "profile_6ada3f59.json")
SCHEMA_VERSION = 1
REGISTRY_HANDLER_VTABLE_OFFSET = 16
MAX_REGISTRY_DEPTH = 64
MAX_VARIABLE_CHAIN_LENGTH = 1024
MAX_VARIABLE_BUCKETS = 1000000
VARIABLE_TYPE_INT32 = 0
VARIABLE_TYPE_INT64 = 1
PRAYER_SKILL_INDEX = 5
MAX_SKILL_LEVEL = 255
MAX_LIFE_POINTS = 1000000
MAX_ADRENALINE_PERCENT = 1000
MAX_GLOBAL_COOLDOWN_TICKS = 10000
PRAYER_POINT_BITS = (0, 14)
TELOS_STAGE_BITS = (21, 23)


def parse_number(value):
    """Return an integer from a JSON number or a "0x"-prefixed string."""
    return int(value, 0) if isinstance(value, str) else value


class ProfileError(RuntimeError):
    """The executable or its loaded code does not match a pinned profile."""


@dataclass(frozen=True)
class RawVariable:
    id: int
    value: int
    node: int
    kind: int


def extract_bits(value, least_significant_bit, most_significant_bit):
    """Return bits least..most (inclusive) of a 32-bit value."""
    if not 0 <= least_significant_bit <= most_significant_bit < 32:
        raise ValueError("Invalid bit field")
    width = most_significant_bit - least_significant_bit + 1
    return ((value & 0xFFFFFFFF) >> least_significant_bit) & ((1 << width) - 1)


def signed_delta(later, earlier):
    """Interpret the wrapped 32-bit difference later - earlier as a signed interval."""
    return ((later - earlier + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def decode_gcd(start_tick, end_tick, clock, tick_ms=20):
    """Return the global cooldown countdown, or None values with a reason."""
    if start_tick is None or end_tick is None:
        return {"remaining_ms": None, "duration_ms": None, "reason": "not_set"}
    duration_ticks = signed_delta(end_tick, start_tick)
    elapsed_ticks = signed_delta(clock, start_tick)
    if not 0 <= duration_ticks <= MAX_GLOBAL_COOLDOWN_TICKS or elapsed_ticks < 0:
        return {"remaining_ms": None, "duration_ms": None, "reason": "inconsistent_clock"}
    return {
        "remaining_ms": max(0, duration_ticks - elapsed_ticks) * tick_ms,
        "duration_ms": duration_ticks * tick_ms,
        "reason": None,
    }


def load_matching_profile(executable_sha256):
    for file_name in PROFILE_FILES:
        profile_path = REPOSITORY_ROOT / "profiles" / file_name
        candidate = json.loads(profile_path.read_text(encoding="utf-8"))
        if candidate["sha256"] == executable_sha256:
            return candidate
    return None


class ReferenceDecoder:
    """Decodes player and NPC telemetry from a ProcessMemory of a pinned client."""

    def __init__(self, memory, profile=None):
        self.memory = memory
        self.identity = memory.identity()
        if profile is None:
            profile = load_matching_profile(self.identity["sha256"])
        if profile is None or self.identity["sha256"] != profile["sha256"]:
            raise ProfileError("Unsupported executable SHA-256; no offsets were used")
        self.profile = profile
        for signature in profile["signatures"]:
            expected = bytes.fromhex(signature["bytes"])
            actual = memory.read(memory.module.base + parse_number(signature["rva"]), len(expected))
            if actual != expected:
                raise ProfileError("Loaded code does not match the verified profile")
        self._last_values = {}
        self._last_changed = {}
        self._last_clock = None
        self._clock_changed_at = None
        self.npc_reader = NPCReader(memory, profile) if "npcs" in profile else None
        position_layout = profile.get("player_position")
        self.position_reader = (
            PlayerPositionReader(memory, position_layout) if position_layout else None
        )

    def domain_capture(self, domain_name):
        """Return the captured context of a registered domain after verifying its handler."""
        domain = self.profile["domains"][domain_name]
        profile = self.profile
        registry = self.memory.module.base + parse_number(profile["registry_rva"])
        node = self.memory.pointer(registry + profile["registry_root_offset"])
        visited = set()
        while node and node not in visited and len(visited) < MAX_REGISTRY_DEPTH:
            visited.add(node)
            key = self.memory.i32(node + profile["tree_key_offset"])
            if key == domain["id"]:
                function_object = self.memory.pointer(node + profile["function_pointer_offset"])
                function_vtable = self.memory.pointer(function_object)
                handler = self.memory.pointer(function_vtable + REGISTRY_HANDLER_VTABLE_OFFSET)
                if handler != self.memory.module.base + parse_number(domain["handler_rva"]):
                    raise ProfileError("Domain callback does not match the profile")
                # The registered callback carries the context pointer in its captured state.
                context = self.memory.pointer(function_object + profile["function_capture_offset"])
                if not context:
                    raise MemoryReadError("Domain context unavailable")
                return context
            if key < domain["id"]:
                child_offset = profile["tree_right_offset"]
            else:
                child_offset = profile["tree_left_offset"]
            node = self.memory.pointer(node + child_offset)
        raise MemoryReadError("Domain is unavailable or its registry changed")

    def read_variable(self, domain_name, variable_id, context=None):
        """Return a RawVariable, or None when the sparse table has no entry."""
        domain = self.profile["domains"][domain_name]
        context = self.domain_capture(domain_name) if context is None else context
        table = self.memory.pointer(context + parse_number(domain["table_offset"]))
        bucket_count = self.memory.unpack(context + parse_number(domain["count_offset"]), "<Q")[0]
        if not table or not 1 <= bucket_count <= MAX_VARIABLE_BUCKETS:
            raise MemoryReadError("Variable table unavailable")
        # The end-of-table marker can be UINTPTR_MAX, not a dereferenceable pointer.
        sentinel = self.memory.unpack(table + bucket_count * 8, "<Q")[0]
        node = self.memory.unpack(table + (variable_id % bucket_count) * 8, "<Q")[0]
        node_layout = self.profile["node"]
        visited = set()
        while node and node != sentinel:
            if node in visited or len(visited) >= MAX_VARIABLE_CHAIN_LENGTH:
                raise MemoryReadError("Variable-chain cycle or excessive length")
            visited.add(node)
            key = self.memory.u32(node + node_layout["key_offset"])
            if key == variable_id:
                kind = self.memory.read(node + node_layout["type_offset"], 1)[0]
                if kind == VARIABLE_TYPE_INT32:
                    value = self.memory.i32(node + node_layout["value_offset"])
                elif kind == VARIABLE_TYPE_INT64:
                    value = self.memory.unpack(node + node_layout["value_offset"], "<q")[0]
                else:
                    raise MemoryReadError("Expected a numeric gameplay variable")
                # Reject a node freed or reused during the read.
                if self.memory.u32(node + node_layout["key_offset"]) != variable_id:
                    raise MemoryReadError("Variable node changed while reading")
                return RawVariable(variable_id, value, node, kind)
            node = self.memory.unpack(node + node_layout["next_offset"], "<Q")[0]
        return None  # An absent sparse entry is not a numeric zero.

    def _changing_value(self, name, value, now, reason=None):
        """Return a value with the age of its last observed change."""
        if value is None:
            self._last_values.pop(name, None)
            self._last_changed.pop(name, None)
            return {"value": None, "reason": reason or "not_set", "last_change_age_ms": None}
        if name not in self._last_values or self._last_values[name] != value:
            self._last_values[name] = value
            self._last_changed[name] = now
        return {
            "value": value,
            "reason": None,
            "last_change_age_ms": round((now - self._last_changed[name]) * 1000, 1),
        }

    def _record(self, started, fields):
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp_ms": time.time_ns() // 1_000_000,
            "monotonic_ms": round(time.monotonic() * 1000, 3),
            "pid": self.memory.pid,
            "executable_sha256": self.identity["sha256"],
            "client_version": self.profile["version"],
            "source": "native_memory",
            "status": "partial",
            "read_duration_ms": round((time.perf_counter() - started) * 1000, 3),
            **fields,
        }

    def _tile_only_snapshot(self, started):
        position = self.position_reader.read()
        snapshot = self._record(
            started,
            {
                "position": position,
                "health": None,
                "prayer": None,
                "adrenaline_percent": None,
                "gcd": None,
                "telos": None,
                "npcs": None,
                "hit_splats": None,
            },
        )
        if position is None:
            snapshot.update(status="unavailable", reason="Player tile unavailable")
        return snapshot

    def _prayer_base_level(self, client):
        profile = self.profile
        client_vtable = self.memory.module.base + parse_number(profile["client_vtable_rva"])
        if self.memory.pointer(client) != client_vtable:
            raise ProfileError("Client context does not match the profile")
        skills_manager_offset = parse_number(profile["skills_manager_offset"])
        skills_manager = self.memory.pointer(client + skills_manager_offset)
        if not skills_manager:
            raise MemoryReadError("Player statistics unavailable")
        skill_entries = self.memory.pointer(skills_manager + profile["skills_entries_offset"])
        if not skill_entries:
            raise MemoryReadError("Player statistics unavailable")
        prayer_entry = skill_entries + PRAYER_SKILL_INDEX * profile["skill_stride"]
        prayer_level = self.memory.i32(prayer_entry + profile["skill_base_level_offset"])
        if not 1 <= prayer_level <= MAX_SKILL_LEVEL:
            raise MemoryReadError("Invalid player-statistics table")
        return prayer_level

    def _read_raw_variables(self, player, client):
        raw_variables = {}
        for name, variable_id in self.profile["variables"].items():
            domain_name = "client" if name.startswith("gcd_client_") else "player"
            context = client if domain_name == "client" else player
            variable = self.read_variable(domain_name, variable_id, context)
            raw_variables[name] = None if variable is None else variable.value
        return raw_variables

    def _read_npcs(self, world_state, logic_clock):
        try:
            return self.npc_reader.read(world_state, logic_clock)
        except OSError as error:
            reason = str(error)
            npcs = {"status": "unavailable", "entities": None, "reason": reason}
            hits = {"status": "unavailable", "active": None, "newly_observed": [], "reason": reason}
            return npcs, hits

    def snapshot(self, include_raw=False):
        """Return one telemetry record in the shared schema."""
        started = time.perf_counter()
        if "domains" not in self.profile:
            return self._tile_only_snapshot(started)

        player = self.domain_capture("player")
        client = self.domain_capture("client")
        prayer_level = self._prayer_base_level(client)
        raw_variables = self._read_raw_variables(player, client)
        world_state = self.memory.pointer(client + self.profile["state_pointer_offset"])
        if not world_state:
            raise MemoryReadError("Client clock unavailable")
        logic_clock_offset = parse_number(self.profile["logic_clock_offset"])
        logic_clock = self.memory.i32(world_state + logic_clock_offset)
        # A world transition can replace the contexts during this sequence.
        if self.domain_capture("player") != player or self.domain_capture("client") != client:
            raise MemoryReadError("World context changed while reading")

        now = time.perf_counter()
        clock_key = (world_state, logic_clock)
        if self._last_clock != clock_key:
            self._last_clock, self._clock_changed_at = clock_key, now
        clock_stalled_ms = round((now - self._clock_changed_at) * 1000, 1)

        prayer_packed = raw_variables["prayer_packed"]
        adrenaline_tenths = raw_variables["adrenaline"]
        prayer_points = None
        if prayer_packed is not None:
            prayer_points = extract_bits(prayer_packed, *PRAYER_POINT_BITS) / 10
        adrenaline = None if adrenaline_tenths is None else adrenaline_tenths / 10
        life_points = raw_variables["health_current"]
        if life_points is not None and not 0 <= life_points <= MAX_LIFE_POINTS:
            raise MemoryReadError("Implausible life points")
        if adrenaline is not None and not 0 <= adrenaline <= MAX_ADRENALINE_PERCENT:
            raise MemoryReadError("Implausible adrenaline")

        gcd = decode_gcd(
            raw_variables["gcd_client_start"],
            raw_variables["gcd_client_end"],
            logic_clock,
            self.profile["logic_tick_ms"],
        )
        gcd.update(
            {
                "clock_raw": logic_clock,
                "start_raw": raw_variables["gcd_start"],
                "end_raw": raw_variables["gcd_end"],
                "duration_raw": raw_variables["gcd_duration"],
                "client_start_raw": raw_variables["gcd_client_start"],
                "client_end_raw": raw_variables["gcd_client_end"],
            }
        )
        npcs, hits = self._read_npcs(world_state, logic_clock)
        telos_packed = raw_variables["telos_packed"]
        telos_stage = None
        if telos_packed is not None:
            telos_stage = extract_bits(telos_packed, *TELOS_STAGE_BITS)
        snapshot = self._record(
            started,
            {
                "position": self.position_reader.read() if self.position_reader else None,
                "freshness": {
                    "observed_at_ms": time.time_ns() // 1_000_000,
                    "client_logic_tick": logic_clock,
                    "clock_stalled_ms": clock_stalled_ms,
                    "server_update_age_ms": None,
                },
                "health": {
                    "current": self._changing_value("health", life_points, now),
                    "max": raw_variables["health_max"],
                },
                "prayer": {
                    "current": self._changing_value("prayer", prayer_points, now),
                    "display_current": None if prayer_points is None else int(prayer_points),
                    "max": prayer_level * 10,
                },
                "adrenaline_percent": self._changing_value("adrenaline", adrenaline, now),
                "gcd": gcd,
                "npcs": npcs,
                "hit_splats": hits,
                "telos": {
                    "present": None,
                    "animation_id": None,
                    "action": None,
                    "phase": None,
                    "stage_raw": telos_stage,
                    "reason": "entity_mapping_pending",
                },
            },
        )
        if include_raw:
            snapshot["raw_variables"] = raw_variables
        return snapshot
