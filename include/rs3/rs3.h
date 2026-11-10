/**
 * @file rs3.h
 * @brief Read-only RS3 sampling and opt-in foreground input ABI, version 1.
 *
 * Platform: Windows x64 with the C calling convention. Every string is UTF-8
 * and NUL-terminated. Every public structure uses the platform's default
 * 8-byte alignment; never apply a packed layout. Foreign-language bindings
 * should compare their layouts against rs3_sizeof() before reading buffers.
 *
 * Ownership: rs3_context and rs3_snapshot are opaque, library-owned objects.
 * Release them only with rs3_destroy() and rs3_snapshot_destroy(). Every array
 * output buffer belongs to the caller. Borrowed strings returned by the library
 * are immutable and remain valid while the DLL is loaded.
 */
#ifndef RS3_NATIVE_H
#define RS3_NATIVE_H

#include <stdint.h>

#if defined(_WIN32) && defined(RS3_BUILD)
#define RS3_API __declspec(dllexport)
#elif defined(_WIN32) && !defined(RS3_STATIC)
#define RS3_API __declspec(dllimport)
#else
#define RS3_API
#endif

#ifdef __cplusplus
extern "C"
{
#endif

/** ABI version reported by rs3_abi_version() and rs3_info.abi_version. */
#define RS3_ABI_VERSION 1u
/** Number of entries in rs3_player.raw_variables. */
#define RS3_VARIABLE_COUNT 10u
/** Maximum number of NPC records in one snapshot. */
#define RS3_MAX_NPCS 1024u
/** Maximum number of hit records in one snapshot. */
#define RS3_MAX_HITS 65536u
/** Size of the NPC name buffers, including the terminating NUL. */
#define RS3_NAME_CAPACITY 520u
/** 28 backpack slots plus 19 equipment slots, including empty cells. */
#define RS3_MAX_ITEMS 47u
/** Maximum number of ability definitions in one snapshot. */
#define RS3_MAX_ABILITIES 2048u

    /** Sampling context: owns the target process handle and the native worker. */
    typedef struct rs3_context rs3_context;
    /** Immutable copy of one sampled frame. */
    typedef struct rs3_snapshot rs3_snapshot;

    /** Result codes returned by API functions. Describe them with rs3_result_string(). */
    enum rs3_result
    {
        RS3_OK = 0,          /**< The operation succeeded. Check rs3_info.status for game data. */
        RS3_E_ARGUMENT = -1, /**< An argument is NULL, out of range or inconsistent. */
        RS3_E_MEMORY = -2,   /**< A local allocation failed. */
        RS3_E_ABI = -3,      /**< An output structure is smaller than this library's layout. */
        RS3_E_BUFFER = -4,   /**< An output array is too small; the required count is reported. */
        RS3_E_STATE = -5,    /**< The call is not valid in the current lifecycle or game state. */
        RS3_E_OS = -6,       /**< A Windows operation failed. */
        RS3_E_TARGET = -7    /**< The input target is missing, hidden or not under the pointer. */
    };

    /** Availability of a sampled frame or sub-record. */
    enum rs3_status
    {
        RS3_NO_SAMPLE = 0,  /**< No sample has been published yet. */
        RS3_AVAILABLE = 1,  /**< Every field covered by this status was read consistently. */
        RS3_PARTIAL = 2,    /**< Some fields are available; inspect the per-field validity. */
        RS3_UNAVAILABLE = 3 /**< No game data is available; see the reason and message. */
    };

    /** Why a value or frame is unavailable. */
    enum rs3_reason
    {
        RS3_REASON_NONE = 0,          /**< The value is available. */
        RS3_NOT_SET = 1,              /**< The game has not stored this value. */
        RS3_READ_FAILED = 2,          /**< A remote read failed or changed during the read. */
        RS3_UNSUPPORTED_CLIENT = 3,   /**< The executable or loaded code is not a pinned profile. */
        RS3_NO_CLIENT = 4,            /**< No RS3 client process was found. */
        RS3_MULTIPLE_CLIENTS = 5,     /**< Several clients exist and none could be preferred. */
        RS3_INCONSISTENT_CLOCK = 6,   /**< A timer interval contradicts the client logic clock. */
        RS3_NOT_TRANSMITTED = 7,      /**< The server has not sent this NPC statistic. */
        RS3_INVALID_STATS = 8,        /**< The NPC statistic pair is out of range. */
        RS3_CATALOG_UNAVAILABLE = 9,  /**< The item and ability catalog is missing or invalid. */
        RS3_REQUIREMENT_BLOCKED = 10, /**< Reserved for requirement checks. */
        RS3_BONE_SHIELD_UNCHECKED = 11, /**< Reserved for Bone Shield exception checks. */
        RS3_UNMAPPED_TIMER = 12         /**< The ability has no mapped client timer pair. */
    };

    /** Indices into rs3_player.raw_variables and bits of rs3_player.valid_variables. */
    enum rs3_variable
    {
        RS3_HEALTH = 0,       /**< VARP 13537: current life points. */
        RS3_MAX_HEALTH,       /**< VARP 13538: maximum life points. */
        RS3_PRAYER_PACKED,    /**< VARP 3274: prayer points in tenths, bits 0..14. */
        RS3_ADRENALINE,       /**< VARP 679: adrenaline in tenths of a percent. */
        RS3_GCD_START,        /**< VARP 4501: server global cooldown start. */
        RS3_GCD_END,          /**< VARP 4502: server global cooldown end. */
        RS3_GCD_DURATION,     /**< VARP 9847: server global cooldown duration. */
        RS3_GCD_CLIENT_START, /**< VARC 2091: client global cooldown start tick. */
        RS3_GCD_CLIENT_END,   /**< VARC 2092: client global cooldown end tick. */
        RS3_TELOS_PACKED      /**< VARP 6363: Telos stage in bits 21..23, unverified. */
    };

    /** Validity bits in rs3_npc.flags. */
    enum rs3_npc_flags
    {
        RS3_NPC_NAME = 1,   /**< name and name_length are valid. */
        RS3_NPC_CONFIG = 2, /**< config_id is valid. */
        RS3_NPC_HEALTH = 4  /**< health_current and health_max are valid. */
    };

    /** Values of rs3_hit.state. */
    enum rs3_hit_state
    {
        RS3_HIT_ACTIVE = 1,   /**< The hit mark is currently displayed. */
        RS3_HIT_SCHEDULED = 2 /**< The hit mark starts at a future client tick. */
    };

    /** Structure identifiers accepted by rs3_sizeof(). */
    enum rs3_structure
    {
        RS3_SIZE_OPTIONS = 1,
        RS3_SIZE_INFO,
        RS3_SIZE_PLAYER,
        RS3_SIZE_NPC,
        RS3_SIZE_HIT,
        RS3_SIZE_EVENT,
        RS3_SIZE_INVENTORY_INFO,
        RS3_SIZE_ITEM,
        RS3_SIZE_ABILITY,
        RS3_SIZE_ITEM_EVENT,
        RS3_SIZE_POSITION,
        RS3_SIZE_MINIMAP
    };

    /** Container identifiers used by rs3_item.container_id and rs3_snapshot_items(). */
    enum rs3_container
    {
        RS3_CONTAINER_ALL = 0,       /**< Both containers, backpack first. */
        RS3_CONTAINER_BACKPACK = 93, /**< The 28-slot backpack. */
        RS3_CONTAINER_EQUIPMENT = 94 /**< The 19 worn-equipment slots. */
    };

    /** Combat styles used by items and abilities. */
    enum rs3_combat_style
    {
        RS3_STYLE_NONE = 0,
        RS3_STYLE_MELEE = 1,
        RS3_STYLE_RANGED = 2,
        RS3_STYLE_MAGIC = 3,
        RS3_STYLE_NECROMANCY = 4
    };

    /** Bits in rs3_item.flags. */
    enum rs3_item_flags
    {
        RS3_ITEM_METADATA = 1,          /**< The catalog describes this item. */
        RS3_ITEM_AMBIGUOUS_IDENTITY = 2 /**< The tracking token could not be matched uniquely. */
    };

    /** Bits in rs3_item.equipment_flags. */
    enum rs3_equipment_flags
    {
        RS3_WEAPON = 1,
        RS3_TWO_HANDED = 2,
        RS3_SHIELD = 4,
        RS3_DEFENDER = 8
    };

    /** Bits in rs3_item.effect_flags. */
    enum rs3_effect_flags
    {
        RS3_HEALS = 1,
        RS3_RESTORES_PRAYER = 2,
        RS3_RESTORES_STATS = 4
    };

    /** Bits in rs3_ability.requirements. */
    enum rs3_requirements
    {
        RS3_REQ_DUAL_WIELD = 1,
        RS3_REQ_TWO_HANDED = 2,
        RS3_REQ_SHIELD = 4,
        RS3_REQ_WEAPON = 8
    };

    /** Values of rs3_ability.equipment_match. */
    enum rs3_equipment_match
    {
        RS3_EQUIPMENT_UNKNOWN = 0, /**< The sampled gear cannot be evaluated. */
        RS3_EQUIPMENT_ALLOWED = 1, /**< The weapon requirements are met; not full castability. */
        RS3_EQUIPMENT_BLOCKED = 2  /**< The sampled gear violates a weapon requirement. */
    };

    /** Bits in rs3_ability.flags. */
    enum rs3_ability_flags
    {
        RS3_ABILITY_TIMER_VALID = 1,        /**< start/end ticks and countdown were observed. */
        RS3_ABILITY_IN_BOOK = 2,            /**< The definition is an ability-book entry. */
        RS3_ABILITY_BASE_COOLDOWN_KNOWN = 4 /**< base_cooldown_ms comes from the definition. */
    };

    /** Values of rs3_ability.timer_source. */
    enum rs3_timer_source
    {
        RS3_TIMER_UNMAPPED = 0,   /**< No client timer pair is known for the ability. */
        RS3_TIMER_CLIENT_PAIR = 1 /**< start_var and end_var are VarClient tick IDs. */
    };

    /** Bits in rs3_inventory_info.flags. */
    enum rs3_inventory_flags
    {
        RS3_INVENTORY_BACKPACK_READ = 1,     /**< Backpack slots were read consistently. */
        RS3_INVENTORY_EQUIPMENT_READ = 2,    /**< Equipment slots were read consistently. */
        RS3_INVENTORY_TIMERS_READ = 4,       /**< Ability timer variables were read. */
        RS3_INVENTORY_CATALOG_LOADED = 8,    /**< A valid catalog supplied metadata. */
        RS3_INVENTORY_BONE_SHIELD_KNOWN = 16 /**< bone_shield_tier was read. */
    };

    /** Values of rs3_item_event.kind. */
    enum rs3_item_change
    {
        RS3_ITEM_ADDED = 1,
        RS3_ITEM_REMOVED = 2,
        RS3_ITEM_MOVED = 3,
        RS3_ITEM_CHANGED = 4,
        RS3_ITEM_QUANTITY_CHANGED = 5,
        RS3_ITEM_DOSES_CHANGED = 6
    };

    /** Actions accepted by rs3_interact(). */
    enum rs3_entity_action
    {
        RS3_ENTITY_DEFAULT = 1,  /**< Left click the entity. */
        RS3_ENTITY_OPEN_MENU,    /**< Right click the entity to open its menu. */
        RS3_ENTITY_SELECT_OPTION /**< Left click a visible menu row. */
    };

    /** Options for rs3_create(). */
    typedef struct rs3_options
    {
        uint32_t struct_size; /**< Must equal sizeof(rs3_options). */
        uint32_t pid;         /**< Target process ID, or 0 to discover a client automatically. */
        uint32_t retry_ms;    /**< Reattachment interval in milliseconds; 0 selects 1000. */
        uint32_t reserved;    /**< Must be zero. */
    } rs3_options;

    /** Frame-level metadata and diagnostics. */
    typedef struct rs3_info
    {
        uint32_t abi_version;           /**< ABI version of the producing library. */
        uint32_t status;                /**< enum rs3_status. */
        uint32_t reason;                /**< enum rs3_reason when status is not available. */
        uint32_t pid;                   /**< Attached process ID, or 0. */
        uint64_t sequence;              /**< Per-context sample counter, starting at 1. */
        uint64_t timestamp_ns;          /**< Unix epoch nanoseconds at sample start. */
        uint64_t monotonic_ns;          /**< rs3_monotonic_ns() at sample start. */
        uint64_t sample_duration_ns;    /**< Time spent acquiring this sample. */
        uint64_t process_creation_time; /**< Windows FILETIME of the target process. */
        uint64_t event_sequence;        /**< Latest hit event sequence when published. */
        uint64_t read_calls;            /**< Remote reads attempted during the sample. */
        uint64_t read_bytes;            /**< Remote bytes requested during the sample. */
        uint32_t logic_tick;            /**< Client logic clock, 20 ms per tick. */
        uint32_t loaded_npcs;           /**< NPC IDs listed by the client registry. */
        uint32_t npc_count;             /**< NPC records in this snapshot. */
        uint32_t skipped_npcs;          /**< Listed NPCs that changed or failed during the read. */
        uint32_t hit_count;             /**< Hit records across all NPCs in this snapshot. */
        uint32_t reserved;
        char executable_sha256[72]; /**< Lowercase hexadecimal SHA-256 of rs2client.exe. */
        char client_version[16];    /**< Short label; see rs3_snapshot_client_version(). */
        char message[192];          /**< Human-readable failure detail, or empty. */
    } rs3_info;

    /** Local player values. */
    typedef struct rs3_player
    {
        uint64_t valid_variables;                  /**< Bit i validates raw_variables[i]. */
        int64_t raw_variables[RS3_VARIABLE_COUNT]; /**< Indexed by enum rs3_variable. */
        uint64_t hp_changed_ns;         /**< Monotonic time of the last observed change. */
        uint64_t prayer_changed_ns;     /**< Monotonic time of the last observed change. */
        uint64_t adrenaline_changed_ns; /**< Monotonic time of the last observed change. */
        uint64_t clock_stalled_ns;      /**< Time since the logic clock last advanced. */
        int32_t prayer_base_level;      /**< Base Prayer level; maximum prayer is 10x. */
        int32_t gcd_remaining_ms;       /**< Valid when gcd_reason is RS3_REASON_NONE. */
        int32_t gcd_duration_ms;        /**< Valid when gcd_reason is RS3_REASON_NONE. */
        uint32_t gcd_reason;            /**< enum rs3_reason. */
    } rs3_player;

    /** Local player tile. */
    typedef struct rs3_position
    {
        uint32_t valid; /**< 1 when all three coordinates are available, otherwise 0. */
        int32_t x;      /**< Tile coordinate towards the east. */
        int32_t y;      /**< Tile coordinate towards the north. */
        int32_t plane;  /**< Plane 0..3. */
    } rs3_position;

    /** Calibration of the currently displayed minimap, in physical screen pixels. */
    typedef struct rs3_minimap
    {
        rs3_position anchor;    /**< Player tile when the minimap was calibrated. */
        int32_t screen_x;       /**< Horizontal centre of the anchor tile on the minimap. */
        int32_t screen_y;       /**< Vertical centre of the anchor tile on the minimap. */
        double east_dx;         /**< Horizontal pixel displacement for one tile east. */
        double east_dy;         /**< Vertical pixel displacement for one tile east. */
        double north_dx;        /**< Horizontal pixel displacement for one tile north. */
        double north_dy;        /**< Vertical pixel displacement for one tile north. */
        uint32_t radius_pixels; /**< Safe clickable radius around the anchor. */
        uint32_t reserved;      /**< Must be zero. */
    } rs3_minimap;

    /** One NPC from the loaded scene. */
    typedef struct rs3_npc
    {
        uint64_t instance_id;   /**< Local identity; changes on despawn or actor replacement. */
        uint32_t runtime_id;    /**< Index in the client's loaded NPC list. */
        int32_t config_id;      /**< NPC type ID, or -1 when unknown. */
        uint32_t flags;         /**< enum rs3_npc_flags. */
        uint32_t health_reason; /**< enum rs3_reason for the health fields. */
        int32_t health_current; /**< Transmitted current life points. */
        int32_t health_max;     /**< Transmitted maximum life points; 0 means not sent. */
        uint32_t hit_offset;    /**< First record of this NPC in the snapshot hit array. */
        uint32_t hit_count;     /**< Number of hit records of this NPC. */
        uint32_t name_length;   /**< Name length in bytes, excluding the NUL. */
        uint32_t reserved;
        char name[RS3_NAME_CAPACITY];
    } rs3_npc;

    /** One record from an NPC's hit mark ring. Amounts are raw; not always damage. */
    typedef struct rs3_hit
    {
        uint32_t slot;             /**< Ring slot index. */
        int32_t type_id;           /**< Raw hit mark type. */
        int32_t amount;            /**< Raw amount. */
        int32_t secondary_type_id; /**< Raw secondary type; negative means absent. */
        int32_t secondary_amount;  /**< Raw secondary amount. */
        uint32_t start_tick;       /**< Client tick when the mark appears. */
        uint32_t end_tick;         /**< Client tick when the mark expires. */
        uint32_t duration_ticks;   /**< end_tick - start_tick. */
        uint32_t state;            /**< enum rs3_hit_state. */
        uint32_t reserved;
    } rs3_hit;

    /** First observation of an active hit mark, with its recipient. */
    typedef struct rs3_event
    {
        uint64_t sequence;              /**< One-based queue sequence number. */
        uint64_t observed_at_ns;        /**< Unix epoch nanoseconds of the observing sample. */
        uint64_t process_creation_time; /**< Windows FILETIME of the observed process. */
        uint64_t instance_id;           /**< Recipient NPC instance. */
        uint32_t pid;                   /**< Observed process ID. */
        uint32_t runtime_id;            /**< Recipient runtime ID. */
        int32_t config_id;              /**< Recipient type ID, or -1. */
        uint32_t name_length;           /**< Recipient name length in bytes. */
        rs3_hit hit;                    /**< The observed hit record. */
        char name[RS3_NAME_CAPACITY];   /**< Recipient name. */
    } rs3_event;

    /** Inventory, equipment and ability summary of a snapshot. */
    typedef struct rs3_inventory_info
    {
        uint32_t status;           /**< enum rs3_status of the containers. */
        uint32_t reason;           /**< enum rs3_reason. */
        uint32_t backpack_count;   /**< 28 when the backpack was read, otherwise 0. */
        uint32_t equipment_count;  /**< 19 when the equipment was read, otherwise 0. */
        uint32_t catalog_status;   /**< enum rs3_status of the metadata catalog. */
        uint32_t ability_count;    /**< Number of ability records. */
        uint32_t bone_shield_tier; /**< Valid with RS3_INVENTORY_BONE_SHIELD_KNOWN. */
        uint32_t flags;            /**< enum rs3_inventory_flags. */
        uint64_t change_sequence;  /**< Latest item event sequence when published. */
        char message[128];         /**< Human-readable failure detail, or empty. */
    } rs3_inventory_info;

    /** One backpack or equipment slot, including empty cells. */
    typedef struct rs3_item
    {
        uint64_t token;            /**< Local inferred identity; duplicates can be ambiguous. */
        uint32_t container_id;     /**< enum rs3_container. */
        uint32_t slot;             /**< Native zero-based slot index. */
        int32_t item_id;           /**< Item ID, or -1 for an empty slot. */
        uint32_t quantity;         /**< Stack size. */
        uint32_t flags;            /**< enum rs3_item_flags. */
        uint32_t family_id;        /**< Raw potion family ID; meaningful when doses >= 0. */
        int32_t doses;             /**< Remaining doses, or -1. */
        int32_t next_dose_item_id; /**< Item after one dose is consumed, or -1. */
        int32_t equip_slot;        /**< Equipment slot of the item, or -1. */
        uint32_t combat_style;     /**< enum rs3_combat_style. */
        uint32_t equipment_flags;  /**< enum rs3_equipment_flags. */
        uint32_t effect_flags;     /**< enum rs3_effect_flags. */
        uint32_t base_heal;        /**< Base flat heal in life points. */
        char name[128];
    } rs3_item;

    /** One ability definition with its live cooldown observation. */
    typedef struct rs3_ability
    {
        uint32_t struct_id;         /**< Definition struct ID. */
        uint32_t flags;             /**< enum rs3_ability_flags. */
        int32_t start_var;          /**< VarClient ID of the cooldown start, or -1. */
        int32_t end_var;            /**< VarClient ID of the cooldown end, or -1. */
        uint32_t start_tick;        /**< Observed start tick; see RS3_ABILITY_TIMER_VALID. */
        uint32_t end_tick;          /**< Observed end tick; see RS3_ABILITY_TIMER_VALID. */
        int32_t remaining_ms;       /**< Remaining cooldown; zero means expired. */
        int32_t duration_ms;        /**< Observed cooldown duration. */
        uint32_t base_cooldown_ms;  /**< Definition cooldown. */
        uint32_t requirements;      /**< enum rs3_requirements. */
        uint32_t combat_style;      /**< enum rs3_combat_style. */
        uint32_t equipment_match;   /**< enum rs3_equipment_match. */
        uint32_t reason;            /**< enum rs3_reason for the timer fields. */
        uint32_t timer_source;      /**< enum rs3_timer_source. */
        uint32_t level_requirement; /**< Definition level requirement. */
        uint32_t adrenaline_requirement_raw; /**< Raw definition adrenaline requirement. */
        char name[128];
    } rs3_ability;

    /** Observed inventory change between two consistent samples. */
    typedef struct rs3_item_event
    {
        uint64_t sequence;              /**< One-based queue sequence number. */
        uint64_t observed_at_ns;        /**< Unix epoch nanoseconds of the observing sample. */
        uint64_t process_creation_time; /**< Windows FILETIME of the observed process. */
        uint32_t pid;                   /**< Observed process ID. */
        uint32_t kind;                  /**< enum rs3_item_change. */
        uint32_t flags;                 /**< Union of before.flags and after.flags. */
        uint32_t reserved;
        rs3_item before; /**< Previous slot record; item_id -1 when absent. */
        rs3_item after;  /**< Current slot record; item_id -1 when absent. */
    } rs3_item_event;

    /* -------------------------------------------------------------------------
     * Library information
     * ---------------------------------------------------------------------- */

    /** Returns RS3_ABI_VERSION of the loaded library. */
    RS3_API uint32_t rs3_abi_version(void);

    /**
     * Returns the library's QPC-based monotonic time in nanoseconds.
     * Use it for snapshot age; a foreign runtime's monotonic clock may have a
     * different origin. This is not wall time.
     */
    RS3_API uint64_t rs3_monotonic_ns(void);

    /** Returns the size of the structure identified by enum rs3_structure, or 0. */
    RS3_API uint32_t rs3_sizeof(uint32_t structure);

    /** Returns the borrowed SHA-256 of the 951-3 JSON profile used at build time. */
    RS3_API const char *rs3_profile_sha256(void);

    /** Returns a borrowed, immutable description of an enum rs3_result value. */
    RS3_API const char *rs3_result_string(int32_t result);

    /* -------------------------------------------------------------------------
     * Context lifecycle and sampling
     * ---------------------------------------------------------------------- */

    /**
     * Creates a sampling context.
     *
     * pid 0 discovers a client automatically. With several clients, the
     * foreground game window is preferred, then the visible client with the lowest
     * PID. A missing client is reported in snapshots, not as a create error.
     * options may be NULL to select defaults.
     */
    RS3_API int32_t rs3_create(const rs3_options *options, rs3_context **out);

    /**
     * Stops and joins a running worker and closes the read-only process handle.
     * The caller must stop all other API calls on the context first. NULL is safe.
     */
    RS3_API void rs3_destroy(rs3_context *context);

    /** Allocates an empty snapshot. Use one snapshot per consumer. */
    RS3_API int32_t rs3_snapshot_create(rs3_snapshot **out);

    /** Releases a snapshot. NULL is safe. */
    RS3_API void rs3_snapshot_destroy(rs3_snapshot *snapshot);

    /**
     * Samples synchronously into out. Returns RS3_E_STATE while the worker runs.
     * RS3_OK means an observation was produced; inspect info.status for availability.
     */
    RS3_API int32_t rs3_sample(rs3_context *context, rs3_snapshot *out);

    /**
     * Starts the native worker at 1..1000 requested samples per second.
     * The worker uses a waitable timer; the requested rate is not guaranteed under load.
     */
    RS3_API int32_t rs3_start(rs3_context *context, uint32_t hz);

    /** Stops and joins the native worker. Idempotent. */
    RS3_API int32_t rs3_stop(rs3_context *context);

    /** Copies the latest published frame. Does not sample the game. */
    RS3_API int32_t rs3_copy_latest(rs3_context *context, rs3_snapshot *out);

    /* -------------------------------------------------------------------------
     * Snapshot accessors
     *
     * Fixed-size accessors return RS3_E_ABI when out_size is too small. Array
     * accessors report the required count; a too-small capacity returns
     * RS3_E_BUFFER and writes no records.
     * ---------------------------------------------------------------------- */

    RS3_API int32_t rs3_snapshot_info(const rs3_snapshot *snapshot, rs3_info *out,
                                      uint32_t out_size);

    /** Returns the borrowed full client version label, or an empty string. */
    RS3_API const char *rs3_snapshot_client_version(const rs3_snapshot *snapshot);

    RS3_API int32_t rs3_snapshot_player(const rs3_snapshot *snapshot, rs3_player *out,
                                        uint32_t out_size);

    /** Additive ABI-v1 accessor. A missing or unmapped tile sets valid to 0. */
    RS3_API int32_t rs3_snapshot_position(const rs3_snapshot *snapshot, rs3_position *out,
                                          uint32_t out_size);

    RS3_API int32_t rs3_snapshot_npcs(const rs3_snapshot *snapshot, rs3_npc *out, uint32_t capacity,
                                      uint32_t *required);

    RS3_API int32_t rs3_snapshot_hits(const rs3_snapshot *snapshot, rs3_hit *out, uint32_t capacity,
                                      uint32_t *required);

    /**
     * Reads hit observations after the caller's cursor.
     *
     * Start with after = 0 and pass the returned next value on the following call.
     * lost counts overwritten sequence numbers that the caller must treat as a gap.
     * The queue retains 4096 observations, including hits already active at
     * attachment. Polling cannot recover game records overwritten between samples.
     */
    RS3_API int32_t rs3_events_since(rs3_context *context, uint64_t after, rs3_event *out,
                                     uint32_t capacity, uint32_t *written, uint64_t *next,
                                     uint64_t *lost);

    /* -------------------------------------------------------------------------
     * Inventory and ability extension (additive ABI v1)
     *
     * Item slots include empty cells; logical indices are independent of UI
     * pixels. Unknown metadata, cooldowns and equipment compatibility stay explicit.
     * ---------------------------------------------------------------------- */

    RS3_API int32_t rs3_snapshot_inventory_info(const rs3_snapshot *snapshot,
                                                rs3_inventory_info *out, uint32_t out_size);

    /** container_id is an enum rs3_container value. */
    RS3_API int32_t rs3_snapshot_items(const rs3_snapshot *snapshot, uint32_t container_id,
                                       rs3_item *out, uint32_t capacity, uint32_t *required);

    RS3_API int32_t rs3_snapshot_abilities(const rs3_snapshot *snapshot, rs3_ability *out,
                                           uint32_t capacity, uint32_t *required);

    /** Reads item changes after the caller's cursor. The queue retains 1024 records. */
    RS3_API int32_t rs3_item_events_since(rs3_context *context, uint64_t after, rs3_item_event *out,
                                          uint32_t capacity, uint32_t *written, uint64_t *next,
                                          uint64_t *lost);

    /**
     * Loads a catalog from an explicit UTF-8 path while the worker is stopped.
     *
     * By default, rs3_create() tries rs3_catalog.bin beside the DLL. Catalogs are
     * hash-checked and pinned to the executable profile. A failed load keeps the
     * previous valid catalog. Missing metadata never invents item names.
     */
    RS3_API int32_t rs3_load_catalog(rs3_context *context, const char *path);

    /* -------------------------------------------------------------------------
     * Foreground input (opt-in)
     *
     * Every call requires a verified snapshot at most one second old. Keep the
     * snapshot unchanged until the call returns. The game must be the foreground
     * window. RS3_OK means Windows accepted the input; observe the result in game.
     * ---------------------------------------------------------------------- */

    /**
     * Clicks a visible tile on the calibrated minimap, on the player's plane.
     * The calibration must describe the current view and match the sampled tile.
     */
    RS3_API int32_t rs3_move_to_tile(const rs3_snapshot *snapshot, int32_t x, int32_t y,
                                     int32_t plane, const rs3_minimap *minimap);

    /**
     * Presses the caller's action-bar key for a sampled ability struct ID.
     * virtual_key is an unmodified Windows A-Z, 0-9 or F1-F12 virtual key.
     */
    RS3_API int32_t rs3_activate_ability(const rs3_snapshot *snapshot, uint32_t struct_id,
                                         uint32_t virtual_key);

    /**
     * Clicks a visible entity or menu row at a physical screen pixel.
     *
     * A nonzero instance_id must appear in the snapshot's NPC list; use zero for
     * scene objects. RS3_ENTITY_OPEN_MENU sends a right click; the other actions
     * send a left click. Resolve the visible option after the menu opens.
     */
    RS3_API int32_t rs3_interact(const rs3_snapshot *snapshot, uint64_t instance_id,
                                 uint32_t action, int32_t screen_x, int32_t screen_y);

#ifdef __cplusplus
}
#endif

#endif /* RS3_NATIVE_H */
