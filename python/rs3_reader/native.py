"""ctypes binding for the public rs3_native C ABI (no remote-memory decoding).

The CDLL calls release Python's GIL. The native worker owns process discovery,
fingerprinting, sampling and event deduplication. Each Reader owns its snapshot
and event cursor and must be closed. Use a separate Reader per Python thread.
"""
import ctypes as C
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
U32, I32, U64, I64 = C.c_uint32, C.c_int32, C.c_uint64, C.c_int64
# Index order matches the generated C variable ID array and validity mask.
VARIABLES = ("health_current", "health_max", "prayer_packed", "adrenaline",
             "gcd_start", "gcd_end", "gcd_duration", "gcd_client_start",
             "gcd_client_end", "telos_packed")


def fields(kind, names):
    return [(name, kind) for name in names.split()]


# The ctypes layouts mirror include/rs3/rs3.h, including field order and alignment.
class Options(C.Structure):
    _fields_ = fields(U32, "struct_size pid retry_ms reserved")


class Info(C.Structure):
    _fields_ = (fields(U32, "abi_version status reason pid") +
                fields(U64, "sequence timestamp_ns monotonic_ns sample_duration_ns "
                       "process_creation_time event_sequence read_calls read_bytes") +
                fields(U32, "logic_tick loaded_npcs npc_count skipped_npcs hit_count reserved") +
                [("executable_sha256", C.c_char*72), ("client_version", C.c_char*16),
                 ("message", C.c_char*192)])


class Player(C.Structure):
    _fields_ = ([("valid_variables", U64), ("raw_variables", I64*10)] +
                fields(U64, "hp_changed_ns prayer_changed_ns adrenaline_changed_ns clock_stalled_ns") +
                fields(I32, "prayer_base_level gcd_remaining_ms gcd_duration_ms") +
                [("gcd_reason", U32)])


class Position(C.Structure):
    _fields_ = [("valid", U32)] + fields(I32, "x y plane")


class CombatState(C.Structure):
    _fields_ = ([("scene_epoch", U64)] + fields(U32, "status reason known_mask active_mask") +
                fields(I32, "normal_raw curses_raw normal_necro_raw curses_necro_raw") +
                [("raw_valid_mask", U32), ("scene_state", I32)])


class NPCAnimation(C.Structure):
    _fields_ = ([("instance_id", U64), ("runtime_id", U32), ("config_id", I32)] +
                fields(U32, "valid reason") + fields(I32, "action_sequence movement_sequence"))


class NPCActionEvent(C.Structure):
    _fields_ = (fields(U64, "sequence observed_ns sample_sequence scene_epoch process_creation_time instance_id") +
                fields(U32, "pid runtime_id") + fields(I32, "config_id previous_action_sequence action_sequence") +
                fields(U32, "logic_tick application_stamp flags"))


class NPC(C.Structure):
    _fields_ = ([("instance_id", U64), ("runtime_id", U32), ("config_id", I32)] +
                fields(U32, "flags health_reason") + fields(I32, "health_current health_max") +
                fields(U32, "hit_offset hit_count name_length reserved") + [("name", C.c_char*520)])


class Hit(C.Structure):
    _fields_ = ([("slot", U32)] + fields(I32, "type_id amount secondary_type_id secondary_amount") +
                fields(U32, "start_tick end_tick duration_ticks state reserved"))


class PlayerHitInfo(C.Structure):
    _fields_ = fields(U32, "status reason player_index hit_count") + [("instance_id", U64)]


class PlayerHitEvent(C.Structure):
    _fields_ = (fields(U64, "sequence observed_ns sample_sequence scene_epoch process_creation_time instance_id") +
                fields(U32, "pid player_index") + [("hit", Hit)])


class Event(C.Structure):
    _fields_ = (fields(U64, "sequence observed_at_ns process_creation_time instance_id") +
                fields(U32, "pid runtime_id") + [("config_id", I32), ("name_length", U32),
                ("hit", Hit), ("name", C.c_char*520)])


class InventoryInfo(C.Structure):
    _fields_ = fields(U32, "status reason backpack_count equipment_count catalog_status ability_count bone_shield_tier flags") + [("change_sequence", U64), ("message", C.c_char*128)]


class Item(C.Structure):
    _fields_ = ([("token", U64)] + fields(U32, "container_id slot") + [("item_id", I32)] +
                fields(U32, "quantity flags family_id") + fields(I32, "doses next_dose_item_id equip_slot") +
                fields(U32, "combat_style equipment_flags effect_flags base_heal") + [("name", C.c_char*128)])


class Ability(C.Structure):
    _fields_ = (fields(U32, "struct_id flags") + fields(I32, "start_var end_var") + fields(U32, "start_tick end_tick") +
                fields(I32, "remaining_ms duration_ms") +
                fields(U32, "base_cooldown_ms requirements combat_style equipment_match reason timer_source level_requirement adrenaline_requirement_raw") +
                [("name", C.c_char*128)])


class ItemEvent(C.Structure):
    _fields_ = (fields(U64, "sequence observed_at_ns process_creation_time") + fields(U32, "pid kind flags reserved") +
                [("before", Item), ("after", Item)])


def library_path(path=None):
    override = path or os.environ.get("RS3_NATIVE_LIBRARY")
    if override:
        return Path(override).resolve()
    binary_dir = ROOT / "build" / "bin"
    release = binary_dir / "Release" / "rs3_native.dll"
    if release.is_file():
        return release
    return binary_dir / "rs3_native.dll"


def load_library(path=None):
    if C.sizeof(C.c_void_p) != 8:
        raise RuntimeError("rs3_native requires a 64-bit Python interpreter")
    path = library_path(path)
    if not path.is_file():
        raise RuntimeError(f"Native library missing at {path}. Build with CMake or set RS3_NATIVE_LIBRARY.")
    lib = C.CDLL(str(path))
    pointer = C.c_void_p
    # Declare each prototype before calling the DLL so pointers and 64-bit
    # values cross the boundary with their full width.
    signatures = {
        "abi_version": (U32, []), "sizeof": (U32, [U32]), "monotonic_ns": (U64, []),
        "profile_sha256": (C.c_char_p, []), "result_string": (C.c_char_p, [I32]),
        "create": (I32, [C.POINTER(Options), C.POINTER(pointer)]),
        "destroy": (None, [pointer]), "snapshot_create": (I32, [C.POINTER(pointer)]),
        "snapshot_destroy": (None, [pointer]), "sample": (I32, [pointer, pointer]),
        "start": (I32, [pointer, U32]), "stop": (I32, [pointer]),
        "copy_latest": (I32, [pointer, pointer]),
        "snapshot_info": (I32, [pointer, C.POINTER(Info), U32]),
        "snapshot_client_version": (C.c_char_p, [pointer]),
        "snapshot_player": (I32, [pointer, C.POINTER(Player), U32]),
        "snapshot_position": (I32, [pointer, C.POINTER(Position), U32]),
        "combat_sizeof": (U32, [U32]),
        "snapshot_combat": (I32, [pointer, C.POINTER(CombatState), U32]),
        "snapshot_npc_animations": (I32, [pointer, C.POINTER(NPCAnimation), U32, C.POINTER(U32)]),
        "snapshot_player_hit_info": (I32, [pointer, C.POINTER(PlayerHitInfo), U32]),
        "snapshot_player_hits": (I32, [pointer, C.POINTER(Hit), U32, C.POINTER(U32)]),
        "player_hit_events_since": (I32, [pointer, U64, C.POINTER(PlayerHitEvent), U32,
                                          C.POINTER(U32), C.POINTER(U64), C.POINTER(U64)]),
        "npc_action_events_since": (I32, [pointer, U64, C.POINTER(NPCActionEvent), U32,
                                          C.POINTER(U32), C.POINTER(U64), C.POINTER(U64)]),
        "combat_active_prayer": (U32, [C.POINTER(CombatState)]),
        "set_prayer": (I32, [pointer, U32, U32, U32]),
        "snapshot_npcs": (I32, [pointer, C.POINTER(NPC), U32, C.POINTER(U32)]),
        "snapshot_hits": (I32, [pointer, C.POINTER(Hit), U32, C.POINTER(U32)]),
        "events_since": (I32, [pointer, U64, C.POINTER(Event), U32, C.POINTER(U32),
                                C.POINTER(U64), C.POINTER(U64)]),
        "snapshot_inventory_info": (I32, [pointer, C.POINTER(InventoryInfo), U32]),
        "snapshot_items": (I32, [pointer, U32, C.POINTER(Item), U32, C.POINTER(U32)]),
        "snapshot_abilities": (I32, [pointer, C.POINTER(Ability), U32, C.POINTER(U32)]),
        "item_events_since": (I32, [pointer, U64, C.POINTER(ItemEvent), U32, C.POINTER(U32), C.POINTER(U64), C.POINTER(U64)]),
        "load_catalog": (I32, [pointer, C.c_char_p]),
    }
    for name, (result, args) in signatures.items():
        try:
            function = getattr(lib, "rs3_"+name)
        except AttributeError as error:
            raise RuntimeError(f"Native library is missing rs3_{name}. Rebuild with CMake.") from error
        function.restype, function.argtypes = result, args
    if lib.rs3_abi_version() != 1:
        raise RuntimeError("Unsupported rs3_native ABI version")
    structures = (Options, Info, Player, NPC, Hit, Event, InventoryInfo, Item, Ability, ItemEvent, Position)
    for kind, structure in enumerate(structures, 1):
        if lib.rs3_sizeof(kind) != C.sizeof(structure):
            raise RuntimeError("C/Python layout mismatch for "+structure.__name__)
    for kind, structure in enumerate((CombatState, NPCAnimation, NPCActionEvent, PlayerHitInfo, PlayerHitEvent), 1):
        if lib.rs3_combat_sizeof(kind) != C.sizeof(structure):
            raise RuntimeError("C/Python layout mismatch for "+structure.__name__)
    # This digest identifies the profile used at build time. The DLL checks
    # the running client's executable fingerprint separately at attachment.
    profile = (ROOT / "profiles" / "profile_951_3.json").read_bytes()
    if lib.rs3_profile_sha256().decode() != hashlib.sha256(profile).hexdigest():
        raise RuntimeError("C library profile differs from Python profile; rebuild rs3_native")
    return lib


class Reader:
    """Typed C snapshots. worker_hz=0 selects synchronous sampling.

    Native events use an independent cursor and report explicit overflow counts.
    Drain once per consumer refresh; the queue holds 4096 observations.
    """
    def __init__(self, pid=None, worker_hz=100, library=None):
        if not 0 <= worker_hz <= 1000:
            raise ValueError("worker_hz must be 0..1000")
        if pid is not None and not 0 < pid <= 0xffffffff:
            raise ValueError("pid must be an unsigned nonzero 32-bit integer")
        self.lib = load_library(library)
        self.context, self.snapshot = C.c_void_p(), C.c_void_p()
        self.worker_hz, self.cursor = worker_hz, 0
        # Size the reusable snapshot arrays for the ABI limits. Events drain in batches.
        self.info, self.player = Info(), Player()
        self.position = Position()
        self.combat, self.animations = CombatState(), (NPCAnimation*1024)()
        self.npcs, self.hits = (NPC*1024)(), (Hit*65536)()
        self.events = (Event*256)()
        self.npc_action_events, self.npc_action_cursor = (NPCActionEvent*256)(), 0
        self.player_hit_info, self.player_hits = PlayerHitInfo(), (Hit*64)()
        self.player_hit_events, self.player_hit_cursor = (PlayerHitEvent*256)(), 0
        self.inventory_info, self.items, self.abilities = InventoryInfo(), (Item*47)(), (Ability*2048)()
        self.item_events, self.item_cursor = (ItemEvent*128)(), 0
        try:
            options = Options(C.sizeof(Options), pid or 0, 1000, 0)
            self.check(self.lib.rs3_create(C.byref(options), C.byref(self.context)))
            self.check(self.lib.rs3_snapshot_create(C.byref(self.snapshot)))
            # The first consumer gets a real observation rather than NO_SAMPLE.
            self.check(self.lib.rs3_sample(self.context, self.snapshot))
            if worker_hz:
                self.check(self.lib.rs3_start(self.context, worker_hz))
        except BaseException:
            self.close()
            raise

    def check(self, result):
        if result:
            raise RuntimeError(self.lib.rs3_result_string(result).decode())

    def sample(self):
        if not self.context:
            raise RuntimeError("Reader is closed")
        function = self.lib.rs3_copy_latest if self.worker_hz else self.lib.rs3_sample
        self.check(function(self.context, self.snapshot))
        self.check(self.lib.rs3_snapshot_info(self.snapshot, C.byref(self.info), C.sizeof(Info)))
        self.check(self.lib.rs3_snapshot_player(self.snapshot, C.byref(self.player), C.sizeof(Player)))
        self.check(self.lib.rs3_snapshot_position(self.snapshot, C.byref(self.position), C.sizeof(Position)))
        self.check(self.lib.rs3_snapshot_combat(self.snapshot, C.byref(self.combat), C.sizeof(CombatState)))
        self.check(self.lib.rs3_snapshot_player_hit_info(self.snapshot, C.byref(self.player_hit_info), C.sizeof(PlayerHitInfo)))
        # Only the prefixes reported in the snapshot counts are populated.
        count = U32()
        self.check(self.lib.rs3_snapshot_npcs(self.snapshot, self.npcs, len(self.npcs), C.byref(count)))
        self.check(self.lib.rs3_snapshot_npc_animations(self.snapshot, self.animations, len(self.animations), C.byref(count)))
        self.check(self.lib.rs3_snapshot_hits(self.snapshot, self.hits, len(self.hits), C.byref(count)))
        self.check(self.lib.rs3_snapshot_player_hits(self.snapshot, self.player_hits, len(self.player_hits), C.byref(count)))
        self.check(self.lib.rs3_snapshot_inventory_info(self.snapshot, C.byref(self.inventory_info), C.sizeof(InventoryInfo)))
        self.check(self.lib.rs3_snapshot_items(self.snapshot, 0, self.items, len(self.items), C.byref(count)))
        self.check(self.lib.rs3_snapshot_abilities(self.snapshot, self.abilities, len(self.abilities), C.byref(count)))
        return self.info, self.player, self.npcs, self.hits

    def drain_player_hit_events(self):
        result, lost_total = [], 0
        for _ in range(16):
            count, next_cursor, lost = U32(), U64(), U64()
            self.check(self.lib.rs3_player_hit_events_since(self.context, self.player_hit_cursor,
                self.player_hit_events, len(self.player_hit_events), C.byref(count), C.byref(next_cursor), C.byref(lost)))
            self.player_hit_cursor = next_cursor.value
            lost_total += lost.value
            result.extend(PlayerHitEvent.from_buffer_copy(self.player_hit_events[i]) for i in range(count.value))
            if count.value < len(self.player_hit_events):
                break
        return result, lost_total

    def drain_events(self):
        result, lost_total = [], 0
        # Bound the drain to the queue size so a busy producer cannot starve UI.
        for _ in range(16):
            count, next_cursor, lost = U32(), U64(), U64()
            self.check(self.lib.rs3_events_since(self.context, self.cursor, self.events, len(self.events),
                       C.byref(count), C.byref(next_cursor), C.byref(lost)))
            self.cursor = next_cursor.value
            lost_total += lost.value
            # ctypes array views point into a reused buffer; copy before next call.
            result.extend(Event.from_buffer_copy(self.events[i]) for i in range(count.value))
            if count.value < len(self.events):
                break
        return result, lost_total

    def drain_item_events(self):
        result, lost_total = [], 0
        # Eight batches of 128 cover the entire retained item queue.
        for _ in range(8):
            count, next_cursor, lost = U32(), U64(), U64()
            self.check(self.lib.rs3_item_events_since(self.context, self.item_cursor, self.item_events,
                       len(self.item_events), C.byref(count), C.byref(next_cursor), C.byref(lost)))
            self.item_cursor = next_cursor.value
            lost_total += lost.value
            result.extend(ItemEvent.from_buffer_copy(self.item_events[i]) for i in range(count.value))
            if count.value<len(self.item_events):
                break
        return result, lost_total

    def drain_npc_action_events(self):
        """Return owned NPC animation events and the number overwritten in the queue."""
        result, lost_total = [], 0
        for _ in range(16):
            count, next_cursor, lost = U32(), U64(), U64()
            self.check(self.lib.rs3_npc_action_events_since(
                self.context, self.npc_action_cursor, self.npc_action_events, len(self.npc_action_events),
                C.byref(count), C.byref(next_cursor), C.byref(lost)))
            self.npc_action_cursor = next_cursor.value
            lost_total += lost.value
            result.extend(NPCActionEvent.from_buffer_copy(self.npc_action_events[i]) for i in range(count.value))
            if count.value < len(self.npc_action_events):
                break
        return result, lost_total

    def close(self):
        if self.context:
            self.lib.rs3_destroy(self.context)
            self.context = C.c_void_p()
        if self.snapshot:
            self.lib.rs3_snapshot_destroy(self.snapshot)
            self.snapshot = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
