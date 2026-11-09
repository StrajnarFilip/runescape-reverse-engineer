"""Version-pinned native-memory decoding, independently implemented.

The domain registry and sparse value tables were recovered from this executable
with Ghidra. All remote operations are reads; game functions are never called.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import time

from winmem import MemoryReadError
from entities import NPCReader, PlayerPositionReader

def number(value):
    return int(value, 0) if isinstance(value, str) else value

class ProfileError(RuntimeError):
    pass

@dataclass(frozen=True)
class RawVariable:
    id: int
    value: int
    node: int
    kind: int

def extract_bits(value, lsb, msb):
    if not 0 <= lsb <= msb < 32:
        raise ValueError("Invalid bit field")
    return ((value & 0xffffffff) >> lsb) & ((1 << (msb-lsb+1)) - 1)

def signed_delta(a, b):
    # Interpret the wrapped 32-bit difference as a signed interval.
    return ((a-b+0x80000000) & 0xffffffff)-0x80000000

def decode_gcd(start, end, clock, tick_ms=20):
    if start is None or end is None:
        return {"remaining_ms": None, "duration_ms": None, "reason": "not_set"}
    duration = signed_delta(end, start)
    elapsed = signed_delta(clock, start)
    if not 0 <= duration <= 10000 or elapsed < 0:
        return {"remaining_ms": None, "duration_ms": None, "reason": "inconsistent_clock"}
    return {"remaining_ms": max(0, duration-elapsed)*tick_ms,
            "duration_ms": duration*tick_ms, "reason": None}

class RS3:
    def __init__(self, memory, profile=None):
        self.memory = memory
        self.identity = memory.identity()
        if profile is None:
            for filename in ("profile_951_3.json", "profile_6ada3f59.json"):
                path = Path(__file__).resolve().parents[2] / "profiles" / filename
                candidate = json.loads(path.read_text(encoding="utf-8"))
                if self.identity["sha256"] == candidate["sha256"]:
                    profile = candidate
                    break
        if profile is None or self.identity["sha256"] != profile["sha256"]:
            raise ProfileError("Unsupported executable SHA-256; no offsets were used")
        self.profile = profile
        for signature in profile["signatures"]:
            expected = bytes.fromhex(signature["bytes"])
            actual = memory.read(memory.module.base + number(signature["rva"]), len(expected))
            if actual != expected:
                raise ProfileError("Loaded code does not match the verified profile")
        self._last_values = {}
        self._last_changed = {}
        self._last_clock = self._clock_changed_at = None
        self.npc_reader = NPCReader(memory, profile) if "npcs" in profile else None
        position = profile.get("player_position")
        self.position_reader = PlayerPositionReader(memory, position) if position else None

    def domain_capture(self, name):
        spec = self.profile["domains"][name]
        registry = self.memory.module.base + number(self.profile["registry_rva"])
        node = self.memory.pointer(registry + self.profile["registry_root_offset"])
        visited = set()
        while node and node not in visited and len(visited) < 64:
            visited.add(node)
            key = self.memory.i32(node + self.profile["tree_key_offset"])
            if key == spec["id"]:
                function = self.memory.pointer(node + self.profile["function_pointer_offset"])
                vtable = self.memory.pointer(function)
                handler = self.memory.pointer(vtable + 16)
                if handler != self.memory.module.base + number(spec["handler_rva"]):
                    raise ProfileError("Domain callback does not match the profile")
                # The registered callback carries the context pointer in its captured state.
                capture = self.memory.pointer(function + self.profile["function_capture_offset"])
                if not capture:
                    raise MemoryReadError("Domain context unavailable")
                return capture
            offset = self.profile["tree_right_offset"] if key < spec["id"] else self.profile["tree_left_offset"]
            node = self.memory.pointer(node + offset)
        raise MemoryReadError("Domain is unavailable or its registry changed")

    def read_variable(self, domain, varid, capture=None):
        spec = self.profile["domains"][domain]
        capture = self.domain_capture(domain) if capture is None else capture
        table = self.memory.pointer(capture + number(spec["table_offset"]))
        count = self.memory.unpack(capture + number(spec["count_offset"]), "<Q")[0]
        if not table or not 1 <= count <= 1000000:
            raise MemoryReadError("Variable table unavailable")
        # EASTL's end-of-table marker can be UINTPTR_MAX, not a dereferenceable pointer.
        sentinel = self.memory.unpack(table + count * 8, "<Q")[0]
        node = self.memory.unpack(table + (varid % count) * 8, "<Q")[0]
        offsets = self.profile["node"]
        visited = set()
        while node and node != sentinel:
            if node in visited or len(visited) >= 1024:
                raise MemoryReadError("Variable-chain cycle or excessive length")
            visited.add(node)
            key = self.memory.u32(node + offsets["key_offset"])
            if key == varid:
                kind = self.memory.read(node + offsets["type_offset"], 1)[0]
                if kind == 0:
                    value = self.memory.i32(node + offsets["value_offset"])
                elif kind == 1:
                    value = self.memory.unpack(node + offsets["value_offset"], "<q")[0]
                else:
                    raise MemoryReadError("Expected a numeric gameplay variable")
                # Reject a node freed/reused during the read.
                if self.memory.u32(node + offsets["key_offset"]) != varid:
                    raise MemoryReadError("Variable node changed while reading")
                return RawVariable(varid, value, node, kind)
            node = self.memory.unpack(node + offsets["next_offset"], "<Q")[0]
        return None  # An absent sparse entry is not automatically a numeric zero.

    def _field(self, name, value, now, reason=None):
        if value is None:
            self._last_values.pop(name, None)
            self._last_changed.pop(name, None)
            return {"value": None, "reason": reason or "not_set", "last_change_age_ms": None}
        if name not in self._last_values or self._last_values[name] != value:
            self._last_values[name] = value
            self._last_changed[name] = now
        return {"value": value, "reason": None,
                "last_change_age_ms": round((now-self._last_changed[name])*1000, 1)}

    def _record(self, started, fields):
        return {
            "schema_version": 1, "timestamp_ms": time.time_ns()//1000000,
            "monotonic_ms": round(time.monotonic()*1000, 3), "pid": self.memory.pid,
            "executable_sha256": self.identity["sha256"],
            "client_version": self.profile["version"], "source": "native_memory",
            "status": "partial", "read_duration_ms": round((time.perf_counter()-started)*1000, 3),
            **fields,
        }

    def snapshot(self, include_raw=False):
        started = time.perf_counter()
        if "domains" not in self.profile:
            position = self.position_reader.read()
            snapshot = self._record(started, {
                "position": position, "health": None, "prayer": None,
                "adrenaline_percent": None, "gcd": None, "telos": None, "npcs": None, "hit_splats": None,
            })
            if position is None:
                snapshot.update(status="unavailable", reason="Player tile unavailable")
            return snapshot
        player = self.domain_capture("player")
        client = self.domain_capture("client")
        if self.memory.pointer(client) != self.memory.module.base + number(self.profile["client_vtable_rva"]):
            raise ProfileError("Client context does not match the profile")
        manager = self.memory.pointer(client + number(self.profile["skills_manager_offset"]))
        if not manager:
            raise MemoryReadError("Player statistics unavailable")
        entries = self.memory.pointer(manager + self.profile["skills_entries_offset"])
        if not entries:
            raise MemoryReadError("Player statistics unavailable")
        prayer_level = self.memory.i32(entries + 5*self.profile["skill_stride"] + self.profile["skill_base_level_offset"])
        if not 1 <= prayer_level <= 255:
            raise MemoryReadError("Invalid player-statistics table")
        raw = {}
        ids = self.profile["variables"]
        for name, varid in ids.items():
            domain = "client" if name.startswith("gcd_client_") else "player"
            result = self.read_variable(domain, varid, client if domain == "client" else player)
            raw[name] = None if result is None else result.value
        state = self.memory.pointer(client + self.profile["state_pointer_offset"])
        if not state:
            raise MemoryReadError("Client clock unavailable")
        logic_clock = self.memory.i32(state + number(self.profile["logic_clock_offset"]))
        # A world transition can replace the contexts during this sequence.
        if self.domain_capture("player") != player or self.domain_capture("client") != client:
            raise MemoryReadError("World context changed while reading")
        now = time.perf_counter()
        clock_key = (state, logic_clock)
        if self._last_clock != clock_key:
            self._last_clock, self._clock_changed_at = clock_key, now
        clock_stalled = round((now-self._clock_changed_at)*1000, 1)
        prayer = None if raw["prayer_packed"] is None else extract_bits(raw["prayer_packed"], 0, 14) / 10
        adrenaline = None if raw["adrenaline"] is None else raw["adrenaline"] / 10
        if raw["health_current"] is not None and not 0 <= raw["health_current"] <= 1000000:
            raise MemoryReadError("Implausible life points")
        if adrenaline is not None and not 0 <= adrenaline <= 1000:
            raise MemoryReadError("Implausible adrenaline")
        gcd = decode_gcd(raw["gcd_client_start"], raw["gcd_client_end"], logic_clock,
                         self.profile["logic_tick_ms"])
        gcd.update({"clock_raw": logic_clock, "start_raw": raw["gcd_start"], "end_raw": raw["gcd_end"],
                    "duration_raw": raw["gcd_duration"], "client_start_raw": raw["gcd_client_start"],
                    "client_end_raw": raw["gcd_client_end"]})
        try:
            npcs, hits = self.npc_reader.read(state, logic_clock)
        except OSError as error:
            npcs = {"status": "unavailable", "entities": None, "reason": str(error)}
            hits = {"status": "unavailable", "active": None, "newly_observed": [], "reason": str(error)}
        snapshot = self._record(started, {
            "position": self.position_reader.read() if self.position_reader else None,
            "freshness": {"observed_at_ms": time.time_ns()//1000000,
                          "client_logic_tick": logic_clock, "clock_stalled_ms": clock_stalled,
                          "server_update_age_ms": None},
            "health": {"current": self._field("health", raw["health_current"], now), "max": raw["health_max"]},
            "prayer": {"current": self._field("prayer", prayer, now),
                       "display_current": None if prayer is None else int(prayer), "max": prayer_level*10},
            "adrenaline_percent": self._field("adrenaline", adrenaline, now),
            "gcd": gcd,
            "npcs": npcs, "hit_splats": hits,
            "telos": {"present": None, "animation_id": None, "action": None,
                      "phase": None, "stage_raw": None if raw["telos_packed"] is None else extract_bits(raw["telos_packed"],21,23),
                      "reason": "entity_mapping_pending"},
        })
        if include_raw:
            snapshot["raw_variables"] = raw
        return snapshot
