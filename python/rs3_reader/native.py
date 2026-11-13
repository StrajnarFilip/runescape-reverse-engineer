"""ctypes binding of the public rs3_native C ABI.

This module only mirrors the C structures in ``include/rs3/*.h`` and calls the
exported functions; it decodes no remote memory itself. ctypes CDLL calls
release the GIL. The native worker owns process discovery, fingerprinting,
sampling and event deduplication.

Each :class:`Reader` owns its snapshot and event cursors and must be closed.
Confine a Reader to one Python thread.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from enum import IntEnum, IntFlag
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_PROFILE_PATH = REPOSITORY_ROOT / "profiles" / "profile_951_3.json"
LIBRARY_ENVIRONMENT_VARIABLE = "RS3_NATIVE_LIBRARY"
LIBRARY_FILE_NAME = "rs3_native.dll"
ABI_VERSION = 1

# Capacities of the public C ABI.
VARIABLE_COUNT = 10
MAX_NPCS = 1024
MAX_HAZARDS = 128
MAX_HITS = 65536
NAME_CAPACITY = 520
MAX_ITEMS = 47
MAX_ABILITIES = 2048
BACKPACK_SLOT_COUNT = 28
EQUIPMENT_SLOT_COUNT = 19
HIT_EVENT_QUEUE_CAPACITY = 4096
ITEM_EVENT_QUEUE_CAPACITY = 1024
NPC_ACTION_EVENT_QUEUE_CAPACITY = 4096
PLAYER_HIT_EVENT_QUEUE_CAPACITY = 4096
# A hitmark ring holds at most this many records.
MAX_PLAYER_HITS = 64

# Batch sizes used when draining the native event queues.
HIT_EVENT_BATCH = 256
ITEM_EVENT_BATCH = 128
NPC_ACTION_EVENT_BATCH = 256
PLAYER_HIT_EVENT_BATCH = 256

DEFAULT_RETRY_MS = 1000
MAX_WORKER_HZ = 1000
MAX_PID = 0xFFFFFFFF

# Field names of rs3_player.raw_variables, in enum rs3_variable order.
VARIABLE_NAMES = (
    "health_current",
    "health_max",
    "prayer_packed",
    "adrenaline",
    "gcd_start",
    "gcd_end",
    "gcd_duration",
    "gcd_client_start",
    "gcd_client_end",
    "telos_packed",
)

# Prayer points in tenths occupy bits 0..14 of the packed prayer variable.
PRAYER_POINTS_MASK = 0x7FFF
# The client logic clock advances every 20 ms.
LOGIC_TICK_MS = 20


class Result(IntEnum):
    """enum rs3_result."""

    OK = 0
    ARGUMENT = -1
    MEMORY = -2
    ABI = -3
    BUFFER = -4
    STATE = -5
    OS = -6
    TARGET = -7


class Status(IntEnum):
    """enum rs3_status."""

    NO_SAMPLE = 0
    AVAILABLE = 1
    PARTIAL = 2
    UNAVAILABLE = 3


class Reason(IntEnum):
    """enum rs3_reason."""

    NONE = 0
    NOT_SET = 1
    READ_FAILED = 2
    UNSUPPORTED_CLIENT = 3
    NO_CLIENT = 4
    MULTIPLE_CLIENTS = 5
    INCONSISTENT_CLOCK = 6
    NOT_TRANSMITTED = 7
    INVALID_STATS = 8
    CATALOG_UNAVAILABLE = 9
    REQUIREMENT_BLOCKED = 10
    BONE_SHIELD_UNCHECKED = 11
    UNMAPPED_TIMER = 12


class Variable(IntEnum):
    """enum rs3_variable: indices into Player.raw_variables."""

    HEALTH = 0
    MAX_HEALTH = 1
    PRAYER_PACKED = 2
    ADRENALINE = 3
    GCD_START = 4
    GCD_END = 5
    GCD_DURATION = 6
    GCD_CLIENT_START = 7
    GCD_CLIENT_END = 8
    TELOS_PACKED = 9


class NPCFlags(IntFlag):
    """enum rs3_npc_flags."""

    NAME = 1
    CONFIG = 2
    HEALTH = 4


class HitState(IntEnum):
    """enum rs3_hit_state."""

    ACTIVE = 1
    SCHEDULED = 2


class Structure(IntEnum):
    """enum rs3_structure: identifiers accepted by rs3_sizeof()."""

    OPTIONS = 1
    INFO = 2
    PLAYER = 3
    NPC = 4
    HIT = 5
    EVENT = 6
    INVENTORY_INFO = 7
    ITEM = 8
    ABILITY = 9
    ITEM_EVENT = 10
    POSITION = 11
    MINIMAP = 12


class CombatStructure(IntEnum):
    """enum rs3_combat_structure: identifiers accepted by rs3_combat_sizeof()."""

    STATE = 1
    ANIMATION = 2
    ACTION_EVENT = 3
    PLAYER_HIT_INFO = 4
    PLAYER_HIT_EVENT = 5


class Container(IntEnum):
    """enum rs3_container."""

    ALL = 0
    BACKPACK = 93
    EQUIPMENT = 94


class CombatStyle(IntEnum):
    """enum rs3_combat_style."""

    NONE = 0
    MELEE = 1
    RANGED = 2
    MAGIC = 3
    NECROMANCY = 4


class ItemFlags(IntFlag):
    """enum rs3_item_flags."""

    METADATA = 1
    AMBIGUOUS_IDENTITY = 2


class EquipmentFlags(IntFlag):
    """enum rs3_equipment_flags."""

    WEAPON = 1
    TWO_HANDED = 2
    SHIELD = 4
    DEFENDER = 8


class EffectFlags(IntFlag):
    """enum rs3_effect_flags."""

    HEALS = 1
    RESTORES_PRAYER = 2
    RESTORES_STATS = 4


class Requirements(IntFlag):
    """enum rs3_requirements."""

    DUAL_WIELD = 1
    TWO_HANDED = 2
    SHIELD = 4
    WEAPON = 8


class EquipmentMatch(IntEnum):
    """enum rs3_equipment_match."""

    UNKNOWN = 0
    ALLOWED = 1
    BLOCKED = 2


class AbilityFlags(IntFlag):
    """enum rs3_ability_flags."""

    TIMER_VALID = 1
    IN_BOOK = 2
    BASE_COOLDOWN_KNOWN = 4


class InventoryFlags(IntFlag):
    """enum rs3_inventory_flags."""

    BACKPACK_READ = 1
    EQUIPMENT_READ = 2
    TIMERS_READ = 4
    CATALOG_LOADED = 8
    BONE_SHIELD_KNOWN = 16


class ItemChange(IntEnum):
    """enum rs3_item_change."""

    ADDED = 1
    REMOVED = 2
    MOVED = 3
    CHANGED = 4
    QUANTITY_CHANGED = 5
    DOSES_CHANGED = 6


class NPCActionFlags(IntFlag):
    """enum rs3_npc_action_flags."""

    HAS_APPLICATION_STAMP = 1


# ---------------------------------------------------------------------------
# Structures. Field order and types mirror include/rs3/*.h exactly; ctypes
# applies the same default alignment as the C compiler.
# ---------------------------------------------------------------------------


class Options(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("retry_ms", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class Info(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("sequence", ctypes.c_uint64),
        ("timestamp_ns", ctypes.c_uint64),
        ("monotonic_ns", ctypes.c_uint64),
        ("sample_duration_ns", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("event_sequence", ctypes.c_uint64),
        ("read_calls", ctypes.c_uint64),
        ("read_bytes", ctypes.c_uint64),
        ("logic_tick", ctypes.c_uint32),
        ("loaded_npcs", ctypes.c_uint32),
        ("npc_count", ctypes.c_uint32),
        ("skipped_npcs", ctypes.c_uint32),
        ("hit_count", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("executable_sha256", ctypes.c_char * 72),
        ("client_version", ctypes.c_char * 16),
        ("message", ctypes.c_char * 192),
    ]


class Player(ctypes.Structure):
    _fields_ = [
        ("valid_variables", ctypes.c_uint64),
        ("raw_variables", ctypes.c_int64 * VARIABLE_COUNT),
        ("hp_changed_ns", ctypes.c_uint64),
        ("prayer_changed_ns", ctypes.c_uint64),
        ("adrenaline_changed_ns", ctypes.c_uint64),
        ("clock_stalled_ns", ctypes.c_uint64),
        ("prayer_base_level", ctypes.c_int32),
        ("gcd_remaining_ms", ctypes.c_int32),
        ("gcd_duration_ms", ctypes.c_int32),
        ("gcd_reason", ctypes.c_uint32),
    ]

    def variable_is_valid(self, variable: int) -> bool:
        return bool(self.valid_variables & (1 << variable))

    def variable(self, variable: int):
        """Return the raw variable value, or None when it is not valid."""
        return self.raw_variables[variable] if self.variable_is_valid(variable) else None


class Position(ctypes.Structure):
    _fields_ = [
        ("valid", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("plane", ctypes.c_int32),
    ]


class CombatState(ctypes.Structure):
    _fields_ = [
        ("scene_epoch", ctypes.c_uint64),
        ("status", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("known_mask", ctypes.c_uint32),
        ("active_mask", ctypes.c_uint32),
        ("normal_raw", ctypes.c_int32),
        ("curses_raw", ctypes.c_int32),
        ("normal_necro_raw", ctypes.c_int32),
        ("curses_necro_raw", ctypes.c_int32),
        ("raw_valid_mask", ctypes.c_uint32),
        ("scene_state", ctypes.c_int32),
    ]


class NPCAnimation(ctypes.Structure):
    _fields_ = [
        ("instance_id", ctypes.c_uint64),
        ("runtime_id", ctypes.c_uint32),
        ("config_id", ctypes.c_int32),
        ("valid", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("action_sequence", ctypes.c_int32),
        ("movement_sequence", ctypes.c_int32),
    ]


class NPCActionEvent(ctypes.Structure):
    _fields_ = [
        ("sequence", ctypes.c_uint64),
        ("observed_ns", ctypes.c_uint64),
        ("sample_sequence", ctypes.c_uint64),
        ("scene_epoch", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("instance_id", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("runtime_id", ctypes.c_uint32),
        ("config_id", ctypes.c_int32),
        ("previous_action_sequence", ctypes.c_int32),
        ("action_sequence", ctypes.c_int32),
        ("logic_tick", ctypes.c_uint32),
        ("application_stamp", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class NPC(ctypes.Structure):
    _fields_ = [
        ("instance_id", ctypes.c_uint64),
        ("runtime_id", ctypes.c_uint32),
        ("config_id", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("health_reason", ctypes.c_uint32),
        ("health_current", ctypes.c_int32),
        ("health_max", ctypes.c_int32),
        ("hit_offset", ctypes.c_uint32),
        ("hit_count", ctypes.c_uint32),
        ("name_length", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("name", ctypes.c_char * NAME_CAPACITY),
    ]


class Hit(ctypes.Structure):
    _fields_ = [
        ("slot", ctypes.c_uint32),
        ("type_id", ctypes.c_int32),
        ("amount", ctypes.c_int32),
        ("secondary_type_id", ctypes.c_int32),
        ("secondary_amount", ctypes.c_int32),
        ("start_tick", ctypes.c_uint32),
        ("end_tick", ctypes.c_uint32),
        ("duration_ticks", ctypes.c_uint32),
        ("state", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class PlayerHitInfo(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("player_index", ctypes.c_uint32),
        ("hit_count", ctypes.c_uint32),
        ("instance_id", ctypes.c_uint64),
    ]


class PlayerHitEvent(ctypes.Structure):
    _fields_ = [
        ("sequence", ctypes.c_uint64),
        ("observed_ns", ctypes.c_uint64),
        ("sample_sequence", ctypes.c_uint64),
        ("scene_epoch", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("instance_id", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("player_index", ctypes.c_uint32),
        ("hit", Hit),
    ]


class Event(ctypes.Structure):
    _fields_ = [
        ("sequence", ctypes.c_uint64),
        ("observed_at_ns", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("instance_id", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("runtime_id", ctypes.c_uint32),
        ("config_id", ctypes.c_int32),
        ("name_length", ctypes.c_uint32),
        ("hit", Hit),
        ("name", ctypes.c_char * NAME_CAPACITY),
    ]


class InventoryInfo(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("backpack_count", ctypes.c_uint32),
        ("equipment_count", ctypes.c_uint32),
        ("catalog_status", ctypes.c_uint32),
        ("ability_count", ctypes.c_uint32),
        ("bone_shield_tier", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("change_sequence", ctypes.c_uint64),
        ("message", ctypes.c_char * 128),
    ]


class Item(ctypes.Structure):
    _fields_ = [
        ("token", ctypes.c_uint64),
        ("container_id", ctypes.c_uint32),
        ("slot", ctypes.c_uint32),
        ("item_id", ctypes.c_int32),
        ("quantity", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("family_id", ctypes.c_uint32),
        ("doses", ctypes.c_int32),
        ("next_dose_item_id", ctypes.c_int32),
        ("equip_slot", ctypes.c_int32),
        ("combat_style", ctypes.c_uint32),
        ("equipment_flags", ctypes.c_uint32),
        ("effect_flags", ctypes.c_uint32),
        ("base_heal", ctypes.c_uint32),
        ("name", ctypes.c_char * 128),
    ]


class Ability(ctypes.Structure):
    _fields_ = [
        ("struct_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("start_var", ctypes.c_int32),
        ("end_var", ctypes.c_int32),
        ("start_tick", ctypes.c_uint32),
        ("end_tick", ctypes.c_uint32),
        ("remaining_ms", ctypes.c_int32),
        ("duration_ms", ctypes.c_int32),
        ("base_cooldown_ms", ctypes.c_uint32),
        ("requirements", ctypes.c_uint32),
        ("combat_style", ctypes.c_uint32),
        ("equipment_match", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("timer_source", ctypes.c_uint32),
        ("level_requirement", ctypes.c_uint32),
        ("adrenaline_requirement_raw", ctypes.c_uint32),
        ("name", ctypes.c_char * 128),
    ]


class ItemEvent(ctypes.Structure):
    _fields_ = [
        ("sequence", ctypes.c_uint64),
        ("observed_at_ns", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("kind", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("before", Item),
        ("after", Item),
    ]


class ActorSpatial(ctypes.Structure):
    """Caller-owned rs3_actor_spatial; validity bits are independent."""

    _fields_ = [
        ("instance_id", ctypes.c_uint64),
        ("observed_ns", ctypes.c_uint64),
        ("runtime_id", ctypes.c_uint32),
        ("valid_mask", ctypes.c_uint32),
        ("tile", Position),
        ("footprint_size", ctypes.c_uint32),
        ("tile_reason", ctypes.c_uint32),
        ("fine_x", ctypes.c_float),
        ("fine_height", ctypes.c_float),
        ("fine_z", ctypes.c_float),
        ("facing_degrees", ctypes.c_float),
        ("facing_reason", ctypes.c_uint32),
        ("target_reason", ctypes.c_uint32),
        ("target_runtime_id", ctypes.c_int32),
        ("target_kind", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    ]


class SpatialFlags(IntFlag):
    TILE = 1
    FINE_POSITION = 2
    FACING = 4
    TARGET = 8
    SAME_WORLD = 16


class Camera(ctypes.Structure):
    """rs3_camera; a complete physical projection requires all three validity bits."""

    _fields_ = [
        ("observed_ns", ctypes.c_uint64),
        ("scene_epoch", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("valid_mask", ctypes.c_uint32),
        ("plane", ctypes.c_int32),
        ("reason", ctypes.c_uint32),
        ("world_to_clip", ctypes.c_float * 16),
        ("viewport_x", ctypes.c_int32),
        ("viewport_y", ctypes.c_int32),
        ("viewport_width", ctypes.c_int32),
        ("viewport_height", ctypes.c_int32),
        ("client_screen_x", ctypes.c_int32),
        ("client_screen_y", ctypes.c_int32),
        ("process_creation_time", ctypes.c_uint64),
        ("reserved", ctypes.c_uint32 * 2),
    ]


class SpatialInfo(ctypes.Structure):
    _fields_ = [
        ("sample_sequence", ctypes.c_uint64),
        ("observed_ns", ctypes.c_uint64),
        ("scene_epoch", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("npc_count", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("player", ActorSpatial),
        ("camera", Camera),
    ]


class ScreenPoint(ctypes.Structure):
    _fields_ = [
        ("valid", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("clip_w", ctypes.c_float),
        ("reserved", ctypes.c_uint32),
    ]


SIZED_SPATIAL_STRUCTURES = {1: ActorSpatial, 2: Camera, 3: SpatialInfo, 4: ScreenPoint}


class HazardFlags(IntFlag):
    """Validity bits from rs3/hazards.h; actor identity is separate from mechanics."""

    IDENTITY = 1
    TILE = 2
    FOOTPRINT = 4
    CLIENT_START = 8
    CLIENT_END = 16
    MECHANICS_VALIDATED = 32


class Hazard(ctypes.Structure):
    """Caller-owned rs3_hazard. Consult flags before using numeric fields."""

    _fields_ = [
        ("instance_id", ctypes.c_uint64),
        ("observed_ns", ctypes.c_uint64),
        ("runtime_id", ctypes.c_uint32),
        ("config_id", ctypes.c_int32),
        ("kind", ctypes.c_uint32),
        ("variant", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("tile", Position),
        ("footprint_size", ctypes.c_uint32),
        ("tile_reason", ctypes.c_uint32),
        ("start_client_tick", ctypes.c_uint32),
        ("end_client_tick", ctypes.c_uint32),
        ("timing_reason", ctypes.c_uint32),
        ("role_reason", ctypes.c_uint32),
        ("role", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class HazardInfo(ctypes.Structure):
    """Coverage/provenance of the snapshot's bounded P4 array."""

    _fields_ = [
        ("observed_ns", ctypes.c_uint64),
        ("sample_sequence", ctypes.c_uint64),
        ("scene_epoch", ctypes.c_uint64),
        ("process_creation_time", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("observed_channels", ctypes.c_uint32),
        ("complete_channels", ctypes.c_uint32),
        ("unmapped_channels", ctypes.c_uint32),
        ("candidate_count", ctypes.c_uint32),
        ("dropped_count", ctypes.c_uint32),
        ("unlocated_count", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    ]


SIZED_HAZARD_STRUCTURES = {1: HazardInfo, 2: Hazard}

# Structures checked against rs3_sizeof() and rs3_combat_sizeof().
SIZED_STRUCTURES = {
    Structure.OPTIONS: Options,
    Structure.INFO: Info,
    Structure.PLAYER: Player,
    Structure.NPC: NPC,
    Structure.HIT: Hit,
    Structure.EVENT: Event,
    Structure.INVENTORY_INFO: InventoryInfo,
    Structure.ITEM: Item,
    Structure.ABILITY: Ability,
    Structure.ITEM_EVENT: ItemEvent,
    Structure.POSITION: Position,
}
SIZED_COMBAT_STRUCTURES = {
    CombatStructure.STATE: CombatState,
    CombatStructure.ANIMATION: NPCAnimation,
    CombatStructure.ACTION_EVENT: NPCActionEvent,
    CombatStructure.PLAYER_HIT_INFO: PlayerHitInfo,
    CombatStructure.PLAYER_HIT_EVENT: PlayerHitEvent,
}


def decode_text(value: bytes) -> str:
    """Decode a NUL-terminated UTF-8 field from the C ABI."""
    return value.decode("utf-8")


def library_path(path=None) -> Path:
    """Return the DLL path: an explicit path, RS3_NATIVE_LIBRARY, or the CMake build output."""
    override = path or os.environ.get(LIBRARY_ENVIRONMENT_VARIABLE)
    if override:
        return Path(override).resolve()
    binary_directory = REPOSITORY_ROOT / "build" / "bin"
    visual_studio_build = binary_directory / "Release" / LIBRARY_FILE_NAME
    if visual_studio_build.is_file():
        return visual_studio_build
    return binary_directory / LIBRARY_FILE_NAME


def _function_prototypes():
    """Return {export suffix: (result type, argument types)} for every rs3_* export."""
    handle = ctypes.c_void_p
    u32, i32, u64 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint64
    cursor_outputs = [ctypes.POINTER(u32), ctypes.POINTER(u64), ctypes.POINTER(u64)]
    return {
        "abi_version": (u32, []),
        "sizeof": (u32, [u32]),
        "spatial_sizeof": (u32, [u32]),
        "snapshot_spatial": (i32, [handle, ctypes.POINTER(SpatialInfo), u32]),
        "snapshot_npc_spatial": (
            i32,
            [handle, ctypes.POINTER(ActorSpatial), u32, ctypes.POINTER(u32)],
        ),
        "project_tile": (
            i32,
            [
                ctypes.POINTER(Camera),
                ctypes.POINTER(Position),
                ctypes.c_float,
                ctypes.POINTER(ScreenPoint),
                u32,
            ],
        ),
        "project_actor": (
            i32,
            [
                ctypes.POINTER(Camera),
                ctypes.POINTER(ActorSpatial),
                ctypes.c_float,
                ctypes.POINTER(ScreenPoint),
                u32,
            ],
        ),
        "monotonic_ns": (u64, []),
        "profile_sha256": (ctypes.c_char_p, []),
        "result_string": (ctypes.c_char_p, [i32]),
        "create": (i32, [ctypes.POINTER(Options), ctypes.POINTER(handle)]),
        "destroy": (None, [handle]),
        "snapshot_create": (i32, [ctypes.POINTER(handle)]),
        "snapshot_destroy": (None, [handle]),
        "sample": (i32, [handle, handle]),
        "start": (i32, [handle, u32]),
        "stop": (i32, [handle]),
        "copy_latest": (i32, [handle, handle]),
        "snapshot_info": (i32, [handle, ctypes.POINTER(Info), u32]),
        "hazards_sizeof": (u32, [u32]),
        "snapshot_hazard_info": (i32, [handle, ctypes.POINTER(HazardInfo), u32]),
        "snapshot_hazards": (i32, [handle, ctypes.POINTER(Hazard), u32, ctypes.POINTER(u32)]),
        "snapshot_client_version": (ctypes.c_char_p, [handle]),
        "snapshot_player": (i32, [handle, ctypes.POINTER(Player), u32]),
        "snapshot_position": (i32, [handle, ctypes.POINTER(Position), u32]),
        "combat_sizeof": (u32, [u32]),
        "snapshot_combat": (i32, [handle, ctypes.POINTER(CombatState), u32]),
        "snapshot_npc_animations": (
            i32,
            [handle, ctypes.POINTER(NPCAnimation), u32, ctypes.POINTER(u32)],
        ),
        "npc_action_events_since": (
            i32,
            [handle, u64, ctypes.POINTER(NPCActionEvent), u32, *cursor_outputs],
        ),
        "snapshot_player_hit_info": (i32, [handle, ctypes.POINTER(PlayerHitInfo), u32]),
        "snapshot_player_hits": (i32, [handle, ctypes.POINTER(Hit), u32, ctypes.POINTER(u32)]),
        "player_hit_events_since": (
            i32,
            [handle, u64, ctypes.POINTER(PlayerHitEvent), u32, *cursor_outputs],
        ),
        "combat_active_prayer": (u32, [ctypes.POINTER(CombatState)]),
        "set_prayer": (i32, [handle, u32, u32, u32]),
        "snapshot_npcs": (i32, [handle, ctypes.POINTER(NPC), u32, ctypes.POINTER(u32)]),
        "snapshot_hits": (i32, [handle, ctypes.POINTER(Hit), u32, ctypes.POINTER(u32)]),
        "events_since": (i32, [handle, u64, ctypes.POINTER(Event), u32, *cursor_outputs]),
        "snapshot_inventory_info": (i32, [handle, ctypes.POINTER(InventoryInfo), u32]),
        "snapshot_items": (i32, [handle, u32, ctypes.POINTER(Item), u32, ctypes.POINTER(u32)]),
        "snapshot_abilities": (i32, [handle, ctypes.POINTER(Ability), u32, ctypes.POINTER(u32)]),
        "item_events_since": (i32, [handle, u64, ctypes.POINTER(ItemEvent), u32, *cursor_outputs]),
        "load_catalog": (i32, [handle, ctypes.c_char_p]),
    }


def _verify_layouts(library):
    for structure_id, structure in SIZED_HAZARD_STRUCTURES.items():
        if library.rs3_hazards_sizeof(structure_id) != ctypes.sizeof(structure):
            raise RuntimeError("C/Python layout mismatch for " + structure.__name__)
    for structure_id, structure in SIZED_SPATIAL_STRUCTURES.items():
        if library.rs3_spatial_sizeof(structure_id) != ctypes.sizeof(structure):
            raise RuntimeError("C/Python spatial layout mismatch: " + structure.__name__)
    for structure_id, structure in SIZED_STRUCTURES.items():
        if library.rs3_sizeof(structure_id) != ctypes.sizeof(structure):
            raise RuntimeError("C/Python layout mismatch for " + structure.__name__)
    for structure_id, structure in SIZED_COMBAT_STRUCTURES.items():
        if library.rs3_combat_sizeof(structure_id) != ctypes.sizeof(structure):
            raise RuntimeError("C/Python layout mismatch for " + structure.__name__)


def load_library(path=None):
    """Load the DLL, declare every prototype and verify the ABI and the build profile."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("rs3_native requires a 64-bit Python interpreter")
    resolved_path = library_path(path)
    if not resolved_path.is_file():
        raise RuntimeError(
            f"Native library missing at {resolved_path}. "
            f"Build with CMake or set {LIBRARY_ENVIRONMENT_VARIABLE}."
        )
    library = ctypes.CDLL(str(resolved_path))
    # Declare each prototype before calling, so pointers and 64-bit values keep their width.
    for name, (result_type, argument_types) in _function_prototypes().items():
        try:
            function = getattr(library, "rs3_" + name)
        except AttributeError as error:
            message = f"Native library is missing rs3_{name}. Rebuild with CMake."
            raise RuntimeError(message) from error
        function.restype = result_type
        function.argtypes = argument_types
    if library.rs3_abi_version() != ABI_VERSION:
        raise RuntimeError("Unsupported rs3_native ABI version")
    _verify_layouts(library)
    # This digest identifies the profile used at build time. The DLL checks the
    # running client's executable fingerprint separately at attachment.
    expected_profile_digest = hashlib.sha256(PRIMARY_PROFILE_PATH.read_bytes()).hexdigest()
    if library.rs3_profile_sha256().decode() != expected_profile_digest:
        raise RuntimeError("C library profile differs from Python profile; rebuild rs3_native")
    return library


class Reader:
    """Typed native snapshots. ``worker_hz=0`` selects synchronous sampling.

    Snapshot arrays are views of reusable storage: copy values that must
    survive the next :meth:`sample`. Each event queue has an independent cursor
    and reports explicit overflow counts; drain once per consumer refresh.
    """

    def __init__(self, pid=None, worker_hz=100, library=None):
        if not 0 <= worker_hz <= MAX_WORKER_HZ:
            raise ValueError(f"worker_hz must be 0..{MAX_WORKER_HZ}")
        if pid is not None and not 0 < pid <= MAX_PID:
            raise ValueError("pid must be an unsigned nonzero 32-bit integer")
        self.library = load_library(library)
        self.context = ctypes.c_void_p()
        self.snapshot = ctypes.c_void_p()
        self.worker_hz = worker_hz
        # Reusable output buffers sized for the ABI limits.
        self.info = Info()
        self.hazard_info = HazardInfo()
        self.hazards = (Hazard * MAX_HAZARDS)()
        self.player = Player()
        self.position = Position()
        self.combat = CombatState()
        self.spatial = SpatialInfo()
        self.npc_spatial = (ActorSpatial * MAX_NPCS)()
        self.npcs = (NPC * MAX_NPCS)()
        self.npc_animations = (NPCAnimation * MAX_NPCS)()
        self.hits = (Hit * MAX_HITS)()
        self.player_hit_info = PlayerHitInfo()
        self.player_hits = (Hit * MAX_PLAYER_HITS)()
        self.inventory_info = InventoryInfo()
        self.items = (Item * MAX_ITEMS)()
        self.abilities = (Ability * MAX_ABILITIES)()
        # Event batches and their cursors.
        self.hit_events = (Event * HIT_EVENT_BATCH)()
        self.hit_event_cursor = 0
        self.item_events = (ItemEvent * ITEM_EVENT_BATCH)()
        self.item_event_cursor = 0
        self.npc_action_events = (NPCActionEvent * NPC_ACTION_EVENT_BATCH)()
        self.npc_action_event_cursor = 0
        self.player_hit_events = (PlayerHitEvent * PLAYER_HIT_EVENT_BATCH)()
        self.player_hit_event_cursor = 0
        try:
            options = Options(ctypes.sizeof(Options), pid or 0, DEFAULT_RETRY_MS, 0)
            self.check_result(
                self.library.rs3_create(ctypes.byref(options), ctypes.byref(self.context))
            )
            self.check_result(self.library.rs3_snapshot_create(ctypes.byref(self.snapshot)))
            # The first consumer gets a real observation rather than NO_SAMPLE.
            self.check_result(self.library.rs3_sample(self.context, self.snapshot))
            if worker_hz:
                self.check_result(self.library.rs3_start(self.context, worker_hz))
        except BaseException:
            self.close()
            raise

    def check_result(self, result):
        """Raise RuntimeError with the library's description of a failed result."""
        if result != Result.OK:
            raise RuntimeError(self.library.rs3_result_string(result).decode())

    def monotonic_ns(self) -> int:
        """Return the native monotonic clock used by Info.monotonic_ns."""
        return self.library.rs3_monotonic_ns()

    def client_version(self) -> str:
        """Return the full client version label of the current snapshot."""
        return decode_text(self.library.rs3_snapshot_client_version(self.snapshot))

    def active_prayer(self) -> int:
        """Return the enum rs3_prayer_id of the single active overhead."""
        return self.library.rs3_combat_active_prayer(ctypes.byref(self.combat))

    def sample(self):
        """Refresh every snapshot buffer. Returns (info, player, npcs, hits)."""
        if not self.context:
            raise RuntimeError("Reader is closed")
        acquire = self.library.rs3_copy_latest if self.worker_hz else self.library.rs3_sample
        self.check_result(acquire(self.context, self.snapshot))
        library, snapshot = self.library, self.snapshot

        def copy_record(accessor, record):
            self.check_result(accessor(snapshot, ctypes.byref(record), ctypes.sizeof(record)))

        # Only the prefixes reported by the snapshot counts are populated.
        count = ctypes.c_uint32()

        def copy_array(accessor, array, *leading_arguments):
            self.check_result(
                accessor(snapshot, *leading_arguments, array, len(array), ctypes.byref(count))
            )

        copy_record(library.rs3_snapshot_info, self.info)
        copy_record(library.rs3_snapshot_hazard_info, self.hazard_info)
        copy_array(library.rs3_snapshot_hazards, self.hazards)
        copy_record(library.rs3_snapshot_player, self.player)
        copy_record(library.rs3_snapshot_position, self.position)
        copy_record(library.rs3_snapshot_combat, self.combat)
        copy_record(library.rs3_snapshot_spatial, self.spatial)
        copy_array(library.rs3_snapshot_npc_spatial, self.npc_spatial)
        copy_array(library.rs3_snapshot_npcs, self.npcs)
        copy_array(library.rs3_snapshot_npc_animations, self.npc_animations)
        copy_array(library.rs3_snapshot_hits, self.hits)
        copy_record(library.rs3_snapshot_player_hit_info, self.player_hit_info)
        copy_array(library.rs3_snapshot_player_hits, self.player_hits)
        copy_record(library.rs3_snapshot_inventory_info, self.inventory_info)
        copy_array(library.rs3_snapshot_items, self.items, Container.ALL)
        copy_array(library.rs3_snapshot_abilities, self.abilities)
        return self.info, self.player, self.npcs, self.hits

    def _drain(self, read_events, cursor, batch, structure, max_batches):
        """Drain one event queue. Returns (owned events, lost count, next cursor)."""
        events, lost_total = [], 0
        # Bound the drain so a busy producer cannot starve the consumer.
        for _ in range(max_batches):
            written, next_cursor, lost = ctypes.c_uint32(), ctypes.c_uint64(), ctypes.c_uint64()
            self.check_result(
                read_events(
                    self.context,
                    cursor,
                    batch,
                    len(batch),
                    ctypes.byref(written),
                    ctypes.byref(next_cursor),
                    ctypes.byref(lost),
                )
            )
            cursor = next_cursor.value
            lost_total += lost.value
            # The batch is reused storage: copy every record before the next call.
            events.extend(
                structure.from_buffer_copy(batch[index]) for index in range(written.value)
            )
            if written.value < len(batch):
                break
        return events, lost_total, cursor

    def drain_events(self):
        """Return (owned hit events, number of overwritten events)."""
        events, lost, self.hit_event_cursor = self._drain(
            self.library.rs3_events_since,
            self.hit_event_cursor,
            self.hit_events,
            Event,
            HIT_EVENT_QUEUE_CAPACITY // HIT_EVENT_BATCH,
        )
        return events, lost

    def drain_item_events(self):
        """Return (owned item events, number of overwritten events)."""
        events, lost, self.item_event_cursor = self._drain(
            self.library.rs3_item_events_since,
            self.item_event_cursor,
            self.item_events,
            ItemEvent,
            ITEM_EVENT_QUEUE_CAPACITY // ITEM_EVENT_BATCH,
        )
        return events, lost

    def drain_npc_action_events(self):
        """Return (owned NPC animation events, number of overwritten events)."""
        events, lost, self.npc_action_event_cursor = self._drain(
            self.library.rs3_npc_action_events_since,
            self.npc_action_event_cursor,
            self.npc_action_events,
            NPCActionEvent,
            NPC_ACTION_EVENT_QUEUE_CAPACITY // NPC_ACTION_EVENT_BATCH,
        )
        return events, lost

    def drain_player_hit_events(self):
        """Return (owned local player hit events, number of overwritten events)."""
        events, lost, self.player_hit_event_cursor = self._drain(
            self.library.rs3_player_hit_events_since,
            self.player_hit_event_cursor,
            self.player_hit_events,
            PlayerHitEvent,
            PLAYER_HIT_EVENT_QUEUE_CAPACITY // PLAYER_HIT_EVENT_BATCH,
        )
        return events, lost

    def close(self):
        """Stop the worker and release the native context and snapshot. Idempotent."""
        if self.context:
            self.library.rs3_destroy(self.context)
            self.context = ctypes.c_void_p()
        if self.snapshot:
            self.library.rs3_snapshot_destroy(self.snapshot)
            self.snapshot = ctypes.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.close()
