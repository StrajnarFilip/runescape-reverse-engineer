"""Immutable, DLL-independent views of native telemetry (contract version 1).

``Observation.value is None`` means unknown. False, zero and a complete empty
tuple are known values. ``age_ms`` is observation age, NEVER time since the last
value change. Missing age stays unknown; ``is_fresh`` then returns False.
Collection completeness is separate: a partial NPC/effect list cannot establish
that an omitted entity/effect is absent. No values carry forward between frames.

P1 owns NPC tile/facing/target and player facing/target; P2 owns Telos decoded
variables and the 600 ms game-tick reference; P3 owns player effects; P4 owns
hazards; P5 owns UI and action-bar bindings. These placeholders remain unknown
until their producers supply explicit telemetry. Raw stage bits, cooldowns and
the 20 ms client clock are not substitutes for those fields.

Inventory/equipment/ability entries retain today's telemetry keys as immutable
mappings, including their per-entry reasons. Event records retain their OWN
timestamps and process/scene identities, even on unavailable snapshot frames.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, Mapping, Optional, Tuple, TypeVar

CONTRACT_VERSION = 1
NANOSECONDS_PER_MILLISECOND = 1_000_000
T = TypeVar("T")


def freeze(value):
    """Copy JSON containers into recursively immutable, caller-independent storage."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite values are not part of the contract")
    if value is None or isinstance(value, (str, int, float, bool, Enum)):
        return value
    if is_dataclass(value) and value.__dataclass_params__.frozen:
        return replace(
            value,
            **{
                field.name: freeze(getattr(value, field.name))
                for field in fields(value)
                if field.init
            },
        )
    raise TypeError("Contract values must be JSON values or frozen contract dataclasses")


def to_jsonable(value):
    """Convert frozen contracts to JSON containers without using dataclasses.asdict."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class Observation(Generic[T]):
    """A value and its provenance, measured as of WorldState.now_ns.

    ``complete`` applies to collections only. Only ``complete is True`` allows
    an absent entry to be treated as absent. Unknown never means an empty list.
    """

    value: Optional[T] = None
    age_ms: Optional[float] = None
    source: Optional[str] = None
    reason: Optional[str] = None
    complete: Optional[bool] = None

    def __post_init__(self):
        if any(
            value is not None and not isinstance(value, str) for value in (self.source, self.reason)
        ):
            raise ValueError("Observation source and reason must be strings or unknown")
        if self.age_ms is not None and (
            isinstance(self.age_ms, bool) or not math.isfinite(self.age_ms) or self.age_ms < 0
        ):
            raise ValueError("Observation age must be finite and nonnegative, or unknown")
        if self.complete is not None and not isinstance(self.complete, bool):
            raise ValueError("Collection completeness must be boolean or unknown")
        if self.value is None and self.complete is not None:
            raise ValueError("An unknown collection cannot be complete or partial")
        object.__setattr__(self, "value", freeze(self.value))
        if self.value is None and self.reason is None:
            object.__setattr__(self, "reason", "not_observed")

    @property
    def known(self):
        return self.value is not None

    def is_fresh(self, maximum_age_ms):
        """Unknown values/ages never pass a consumer's freshness threshold."""
        if not math.isfinite(maximum_age_ms) or maximum_age_ms < 0:
            raise ValueError("Maximum age must be finite and nonnegative")
        return self.known and self.age_ms is not None and self.age_ms <= maximum_age_ms


@dataclass(frozen=True)
class SessionIdentity:
    """Snapshot identity; a missing component cannot authorize live input."""

    pid: Optional[int]
    process_creation_time: Optional[int]
    scene_epoch: Optional[int]

    @property
    def complete(self):
        return all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (self.pid, self.process_creation_time, self.scene_epoch)
        )


@dataclass(frozen=True)
class Tile:
    """World tile origin; instanced arena positions need observed anchors (P1)."""

    x: int
    y: int
    plane: int

    def __post_init__(self):
        if any(_integer(value) is None for value in (self.x, self.y, self.plane)) or self.plane > 3:
            raise ValueError("Tile requires nonnegative integer coordinates and plane 0..3")


@dataclass(frozen=True)
class Facing:
    """P1 supplies both angle and unit; unscaled client units are not radians."""

    angle: float
    unit: str

    def __post_init__(self):
        if not _number(self.angle) or self.unit not in ("degrees", "radians", "client_units"):
            raise ValueError("Facing requires a finite angle and an explicit supported unit")


@dataclass(frozen=True)
class EntityRef:
    """Target identity, scoped by the enclosing snapshot/intent session (P1)."""

    kind: str
    instance_id: Optional[int] = None
    runtime_id: Optional[int] = None
    config_id: Optional[int] = None
    player_index: Optional[int] = None

    def __post_init__(self):
        if self.kind not in ("npc", "player", "object", "interface", "none"):
            raise ValueError("Unsupported entity kind; explicit none means no target")
        if any(
            value is not None and _integer(value) is None
            for value in (self.instance_id, self.runtime_id, self.config_id, self.player_index)
        ):
            raise ValueError("Entity IDs must be nonnegative integers or unknown")


@dataclass(frozen=True)
class ResourceState:
    current: Observation[float]
    maximum: Observation[float]


@dataclass(frozen=True)
class PlayerState:
    health: ResourceState
    prayer: ResourceState
    adrenaline_percent: Observation[float]
    active_prayer: Observation[str]
    gcd: Observation[Mapping[str, Any]]
    tile: Observation[Tile]
    facing: Observation[Facing]  # P1
    target: Observation[EntityRef]  # P1
    effects: Observation[Tuple[Mapping[str, Any], ...]]  # P3, completeness matters


@dataclass(frozen=True)
class NPCState:
    instance_id: Observation[int]
    runtime_id: Observation[int]
    config_id: Observation[int]
    name: Observation[str]
    health: ResourceState
    animation: Observation[Mapping[str, Any]]
    tile: Observation[Tile]  # P1
    facing: Observation[Facing]  # P1
    target: Observation[EntityRef]  # P1


@dataclass(frozen=True)
class TelosState:
    phase: Observation[int]  # P2, never decoded from stage_raw here
    enrage: Observation[float]  # P2
    streak: Observation[int]  # P2
    meter: Observation[float]  # P2, producer states its scale in source metadata
    freedom: Observation[bool]  # P2: active state, not an ability cooldown
    stage_raw: Observation[int]  # Existing research field, NOT a phase


@dataclass(frozen=True)
class UIState:
    open: Observation[Tuple[Mapping[str, Any], ...]]  # P5


@dataclass(frozen=True)
class EventBatch:
    """Queue records keep original provenance; missing loss count is unknown."""

    records: Observation[Tuple[Mapping[str, Any], ...]]
    lost: Observation[int]


@dataclass(frozen=True)
class EventStreams:
    npc_actions: EventBatch
    npc_hits: EventBatch
    player_hits: EventBatch
    inventory_changes: EventBatch
    manual_actions: EventBatch


@dataclass(frozen=True)
class WorldState:
    """One sample, no inferred readiness, attacker identity or persistent cache.

    Scalar metadata describes this sample rather than a game measurement.
    ``observed_ns`` is native sample time; ``now_ns`` adds recorded snapshot age
    when available. Missing age remains unknown even if now_ns equals sample time.
    Use ``session.complete`` and each Observation.is_fresh before live decisions.
    Collection access is e.g. ``world.npcs.value``; None differs from ().
    """

    contract_version: int
    session: SessionIdentity
    sample_sequence: Optional[int]
    timestamp_ms: Optional[float]
    observed_ns: Optional[int]
    now_ns: Optional[int]
    status: str
    player: PlayerState
    npcs: Observation[Tuple[NPCState, ...]]
    inventory: Observation[Tuple[Mapping[str, Any], ...]]
    equipment: Observation[Mapping[str, Any]]
    abilities: Observation[Tuple[Mapping[str, Any], ...]]
    telos: TelosState
    client_logic_tick: Observation[int]  # Existing 20 ms clock, NOT a game tick
    game_tick: Observation[Mapping[str, Any]]  # P2: index/phase plus uncertainty_ms
    hazards: Observation[Tuple[Mapping[str, Any], ...]]  # P4
    ui: UIState
    action_bar: Observation[Tuple[Mapping[str, Any], ...]]  # P5
    events: EventStreams
    raw: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "raw", freeze(self.raw))

    @classmethod
    def from_telemetry(cls, record, *, now_ns=None):
        """Adapt schema-1 NativeTelemetry or unversioned capture_prayer JSONL.

        Future fields use ``{value, age_ms, source, reason, complete}`` envelopes.
        This adapter trusts explicit mapped fields, never derives them from raw
        variables. Callers can set now_ns in the SAME monotonic clock domain.
        No DLL, process, wall clock, keyboard or image access is performed.
        """
        return _build_world(record, now_ns)


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _integer(value, minimum=0):
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= minimum
        else None
    )


def _status(value):
    return {0: "no_sample", 1: "available", 2: "partial", 3: "unavailable"}.get(
        value, value if isinstance(value, str) else "unavailable"
    )


class _Adapter:
    def __init__(self, age_ms, source, available, age_adjustment_ms=0):
        self.age_ms, self.source, self.available = age_ms, source, available
        self.age_adjustment_ms = age_adjustment_ms

    def observe(self, value, *, owner=None, complete=None, reason=None, historical=False):
        if not self.available and not historical:
            return Observation(source=self.source, reason="frame_unavailable")
        envelope = _mapping(value)
        if "value" in envelope:
            age = envelope.get("age_ms", self.age_ms)
            if "age_ms" in envelope and _number(age):
                age += self.age_adjustment_ms
            return Observation(
                envelope["value"],
                age,
                envelope.get("source", self.source),
                envelope.get("reason", reason),
                envelope.get("complete", complete) if envelope["value"] is not None else None,
            )
        return Observation(
            value,
            self.age_ms if not historical else None,
            self.source,
            reason or ("unmapped:" + owner if owner and value is None else None),
            complete if value is not None else None,
        )

    def collection(self, section, key, *, owner=None, historical=False):
        section = _mapping(section)
        status = _status(section.get("status"))
        value = section.get(key)
        if "status" in section and status not in ("available", "partial"):
            value = None
        if value is not None and not isinstance(value, (list, tuple, Mapping)):
            raise ValueError("Collection field must be an array or observation envelope")
        return self.observe(
            value,
            owner=owner,
            complete=True if status == "available" else False if status == "partial" else None,
            reason=section.get("reason"),
            historical=historical,
        )

    def resource(self, section, *, current=None):
        section = _mapping(section)
        resource_adapter = _Adapter(
            self.age_ms, section.get("source", self.source), self.available, self.age_adjustment_ms
        )
        return ResourceState(
            resource_adapter.observe(section.get("current", current), reason=section.get("reason")),
            resource_adapter.observe(section.get("max"), reason=section.get("reason")),
        )

    def tile(self, value):
        observation = self.observe(value, owner="P1")
        value = observation.value
        if value is not None:
            coordinates = (
                tuple(value.get(key) for key in ("x", "y", "plane"))
                if isinstance(value, Mapping)
                else value
            )
            if len(coordinates) != 3 or any(_integer(item) is None for item in coordinates):
                raise ValueError("Tile must contain three nonnegative integer coordinates")
            observation = Observation(
                Tile(*coordinates), observation.age_ms, observation.source, observation.reason
            )
        return observation

    def typed(self, value, cls, owner):
        observation = self.observe(value, owner=owner)
        return (
            Observation(
                cls(**observation.value), observation.age_ms, observation.source, observation.reason
            )
            if observation.known
            else observation
        )

    def batch(self, section, key, lost_key="events_lost"):
        section = _mapping(section)
        return EventBatch(
            self.observe(section.get(key), historical=True),
            self.observe(_integer(section.get(lost_key)), historical=True),
        )


def _build_world(record, now_ns):
    if not isinstance(record, Mapping):
        raise ValueError("Telemetry row must be an object")
    legacy = "schema_version" not in record and "observed_ns" in record and "sequence" in record
    if not legacy and record.get("schema_version") != 1:
        raise ValueError("Expected telemetry schema_version 1 or capture_prayer record")
    native, combat = _mapping(record.get("native")), _mapping(record.get("combat"))
    observed_ns = _integer(record.get("observed_ns"))
    if observed_ns is None and _number(record.get("monotonic_ms")) and record["monotonic_ms"] >= 0:
        observed_ns = round(record["monotonic_ms"] * NANOSECONDS_PER_MILLISECOND)
    age_ms = native.get("snapshot_age_ms")
    if not _number(age_ms) or age_ms < 0:
        age_ms = None
    captured_age_ms = age_ms
    if now_ns is not None:
        if _integer(now_ns) is None or (observed_ns is not None and now_ns < observed_ns):
            raise ValueError("now_ns must not precede native sample time")
        age_ms = (
            (now_ns - observed_ns) / NANOSECONDS_PER_MILLISECOND
            if observed_ns is not None
            else None
        )
    elif observed_ns is not None:
        now_ns = observed_ns + round((age_ms or 0) * NANOSECONDS_PER_MILLISECOND)
    status = _status(record.get("status"))
    adapter = _Adapter(
        age_ms,
        record.get("source") or "native_memory",
        status in ("available", "partial"),
        max(0, (age_ms or 0) - (captured_age_ms or 0)),
    )
    player = _mapping(record.get("player"))  # Additive producer fields, P1/P3.
    player_state = PlayerState(
        adapter.resource(record.get("health"), current=record.get("hp")),
        adapter.resource(record.get("prayer")),
        adapter.observe(record.get("adrenaline_percent")),
        adapter.observe(combat.get("active_prayer"), reason=combat.get("reason")),
        adapter.observe(record.get("gcd")),
        adapter.tile(player.get("tile", record.get("position"))),
        adapter.typed(player.get("facing"), Facing, "P1"),
        adapter.typed(player.get("target"), EntityRef, "P1"),
        adapter.collection(player.get("effects"), "entries", owner="P3"),
    )
    npc_section = record.get("npcs")
    if legacy and npc_section is None and isinstance(record.get("animations"), list):
        npc_section = {"status": "partial", "entities": record["animations"]}
    npc_observation = adapter.collection(npc_section, "entities")
    npcs = None
    if npc_observation.known:
        npc_adapter = _Adapter(
            npc_observation.age_ms,
            npc_observation.source,
            adapter.available,
            adapter.age_adjustment_ms,
        )
        npcs = tuple(
            NPCState(
                npc_adapter.observe(entity.get("instance_id")),
                npc_adapter.observe(entity.get("runtime_id")),
                npc_adapter.observe(entity.get("config_id")),
                npc_adapter.observe(entity.get("name")),
                npc_adapter.resource(entity.get("health")),
                npc_adapter.observe(entity.get("animation", entity if legacy else None)),
                npc_adapter.tile(entity.get("tile")),
                npc_adapter.typed(entity.get("facing"), Facing, "P1"),
                npc_adapter.typed(entity.get("target"), EntityRef, "P1"),
            )
            for entity in npc_observation.value
        )
    telos = _mapping(record.get("telos"))
    telos_state = TelosState(
        *(
            adapter.observe(telos.get(name), owner="P2")
            for name in ("phase", "enrage", "streak", "meter", "freedom")
        ),
        adapter.observe(telos.get("stage_raw")),
    )
    events = EventStreams(
        adapter.batch(record, "npc_action_events", "npc_action_events_lost"),
        adapter.batch(record.get("hit_splats"), "newly_observed"),
        (
            adapter.batch(record, "player_hit_events", "player_hit_events_lost")
            if "player_hit_events" in record
            else adapter.batch(record.get("player_hit_splats"), "newly_observed")
        ),
        adapter.batch(record.get("inventory_changes"), "newly_observed"),
        adapter.batch(record, "manual_prayer_keys", "manual_actions_lost"),
    )
    return WorldState(
        CONTRACT_VERSION,
        SessionIdentity(
            _integer(record.get("pid"), 1),
            _integer(record.get("process_creation_time"), 1),
            _integer(combat.get("scene_epoch"), 1),
        ),
        _integer(native.get("sample_sequence", record.get("sequence"))),
        record.get(
            "timestamp_ms",
            (
                record.get("timestamp_ns", 0) / NANOSECONDS_PER_MILLISECOND
                if record.get("timestamp_ns") is not None
                else None
            ),
        ),
        observed_ns,
        now_ns,
        status,
        player_state,
        Observation(
            npcs,
            npc_observation.age_ms,
            npc_observation.source,
            npc_observation.reason,
            npc_observation.complete,
        ),
        adapter.collection(record.get("inventory"), "slots"),
        adapter.observe(
            record.get("equipment")
            if _status(_mapping(record.get("equipment")).get("status")) in ("available", "partial")
            else None
        ),
        adapter.collection(record.get("abilities"), "entries"),
        telos_state,
        adapter.observe(
            _mapping(record.get("freshness")).get(
                "client_logic_tick", record.get("client_logic_tick")
            )
        ),
        adapter.observe(record.get("game_tick"), owner="P2"),
        adapter.collection(record.get("hazards"), "entries", owner="P4"),
        UIState(adapter.collection(record.get("ui"), "open", owner="P5")),
        adapter.collection(record.get("action_bar"), "entries", owner="P5"),
        events,
        freeze(record),
    )
