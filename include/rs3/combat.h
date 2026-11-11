/**
 * @file combat.h
 * @brief Overhead prayer flags, NPC animations and NPC action events (additive ABI v1).
 *
 * Existing ABI-v1 structures are unchanged. A missing sparse variable is
 * UNKNOWN, never an assumed zero. Animation sequence IDs are raw values: no
 * attack style, target or prayer deadline is implied.
 */
#ifndef RS3_COMBAT_H
#define RS3_COMBAT_H

#include "prayer.h"

#ifdef __cplusplus
extern "C"
{
#endif

/** Overhead prayer state of the local player. */
typedef struct rs3_combat_state
{
    uint64_t scene_epoch;     /**< Local scene epoch; pair it with PID and creation time. */
    uint32_t status;          /**< enum rs3_status. */
    uint32_t reason;          /**< enum rs3_reason. */
    uint32_t known_mask;      /**< RS3_PRAYER_BIT() of each overhead whose state is known. */
    uint32_t active_mask;     /**< RS3_PRAYER_BIT() of each overhead observed active. */
    int32_t normal_raw;       /**< VARP 3272: standard prayer flags. */
    int32_t curses_raw;       /**< VARP 3275: curse flags. */
    int32_t normal_necro_raw; /**< VARP 3273: standard necromancy prayer flags. */
    int32_t curses_necro_raw; /**< VARP 5859: curse necromancy flags. */
    uint32_t raw_valid_mask;  /**< Bits 0..3 validate the four raw fields in order. */
    int32_t scene_state;      /**< Client state; 30 is in game on these profiles. */
} rs3_combat_state;

/** Raw animation sequences of one NPC, parallel to the snapshot NPC list. */
typedef struct rs3_npc_animation
{
    uint64_t instance_id;      /**< Matches rs3_npc.instance_id. */
    uint32_t runtime_id;       /**< Matches rs3_npc.runtime_id. */
    int32_t config_id;         /**< Matches rs3_npc.config_id. */
    uint32_t valid;            /**< 1 when both sequences were read consistently. */
    uint32_t reason;           /**< enum rs3_reason when valid is 0. */
    int32_t action_sequence;   /**< Action sequence ID; -1 is an idle slot. */
    int32_t movement_sequence; /**< Movement sequence ID; -1 is an idle slot. */
} rs3_npc_animation;

/** Bits in rs3_npc_action_event.flags. */
enum rs3_npc_action_flags
{
    RS3_NPC_ACTION_HAS_APPLICATION_STAMP = 1 /**< application_stamp is valid. */
};

/** Structure identifiers accepted by rs3_combat_sizeof(). */
enum rs3_combat_structure
{
    RS3_COMBAT_SIZE_STATE = 1,
    RS3_COMBAT_SIZE_ANIMATION = 2,
    RS3_COMBAT_SIZE_ACTION_EVENT = 3,
    RS3_COMBAT_SIZE_PLAYER_HIT_INFO = 4,
    RS3_COMBAT_SIZE_PLAYER_HIT_EVENT = 5
};

/**
 * An observed application of an NPC action animation, detected between two
 * adjacent valid samples of the same NPC instance and scene.
 */
typedef struct rs3_npc_action_event
{
    uint64_t sequence;                /**< One-based queue sequence number. */
    uint64_t observed_ns;             /**< rs3_monotonic_ns() at observation. */
    uint64_t sample_sequence;         /**< rs3_info.sequence of the observing sample. */
    uint64_t scene_epoch;             /**< rs3_combat_state.scene_epoch. */
    uint64_t process_creation_time;   /**< Windows FILETIME of the observed process. */
    uint64_t instance_id;             /**< NPC instance. */
    uint32_t pid;                     /**< Observed process ID. */
    uint32_t runtime_id;              /**< NPC runtime ID. */
    int32_t config_id;                /**< NPC type ID, or -1. */
    int32_t previous_action_sequence; /**< Sequence in the previous sample. */
    int32_t action_sequence;          /**< Sequence in this sample. */
    uint32_t logic_tick;              /**< Client logic clock of this sample. */
    uint32_t application_stamp;       /**< Client application stamp, when flagged. */
    uint32_t flags;                   /**< enum rs3_npc_action_flags. */
} rs3_npc_action_event;

/**
 * The local player's sampled hit mark ring. This is not a complete incoming
 * attack schedule: amounts and types can represent healing, zeroes or text,
 * and no attacker is identified. Available on the original executable only.
 */
typedef struct rs3_player_hit_info
{
    uint32_t status;       /**< enum rs3_status. */
    uint32_t reason;       /**< enum rs3_reason when status is not available. */
    uint32_t player_index; /**< Index of the local player in the player table. */
    uint32_t hit_count;    /**< Records returned by rs3_snapshot_player_hits(). */
    uint64_t instance_id;  /**< Local identity of the player actor in this scene. */
} rs3_player_hit_info;

/** First observation of an active local-player hit mark between adjacent samples. */
typedef struct rs3_player_hit_event
{
    uint64_t sequence;              /**< One-based queue sequence number. */
    uint64_t observed_ns;           /**< rs3_monotonic_ns() at observation. */
    uint64_t sample_sequence;       /**< rs3_info.sequence of the observing sample. */
    uint64_t scene_epoch;           /**< rs3_combat_state.scene_epoch. */
    uint64_t process_creation_time; /**< Windows FILETIME of the observed process. */
    uint64_t instance_id;           /**< rs3_player_hit_info.instance_id. */
    uint32_t pid;                   /**< Observed process ID. */
    uint32_t player_index;          /**< rs3_player_hit_info.player_index. */
    rs3_hit hit;                    /**< The observed hit record. */
} rs3_player_hit_event;

/** Returns the size of the structure identified by enum rs3_combat_structure, or 0. */
RS3_API uint32_t rs3_combat_sizeof(uint32_t structure);

RS3_API int32_t rs3_snapshot_player_hit_info(const rs3_snapshot *snapshot, rs3_player_hit_info *out,
                                             uint32_t out_size);

RS3_API int32_t rs3_snapshot_player_hits(const rs3_snapshot *snapshot, rs3_hit *out,
                                         uint32_t capacity, uint32_t *required);

/**
 * Reads local-player hit events after the caller's cursor, initially 0.
 *
 * The queue retains 4096 records. Records already active at attachment or
 * after a sampling gap establish a baseline, not freshly timed hit events.
 */
RS3_API int32_t rs3_player_hit_events_since(rs3_context *context, uint64_t after,
                                            rs3_player_hit_event *out, uint32_t capacity,
                                            uint32_t *written, uint64_t *next, uint64_t *lost);

RS3_API int32_t rs3_snapshot_combat(const rs3_snapshot *snapshot, rs3_combat_state *out,
                                    uint32_t out_size);

RS3_API int32_t rs3_snapshot_npc_animations(const rs3_snapshot *snapshot, rs3_npc_animation *out,
                                            uint32_t capacity, uint32_t *required);

/**
 * Reads NPC action events after the caller's cursor, initially 0.
 *
 * Pass next as after on the following call. lost counts overwritten queue
 * entries. observed_ns uses rs3_monotonic_ns(). Match the process and scene
 * before consuming an event. A fresh instance or a sampling gap establishes a
 * new baseline instead of an event.
 */
RS3_API int32_t rs3_npc_action_events_since(rs3_context *context, uint64_t after,
                                            rs3_npc_action_event *out, uint32_t capacity,
                                            uint32_t *written, uint64_t *next, uint64_t *lost);

/**
 * Returns the single active overhead as an enum rs3_prayer_id.
 * Conflicting or insufficiently known overheads return RS3_PRAYER_UNKNOWN.
 */
RS3_API uint32_t rs3_combat_active_prayer(const rs3_combat_state *state);

/** Modifier bits accepted by rs3_set_prayer(). */
enum rs3_key_modifiers
{
    RS3_KEY_CTRL = 1,
    RS3_KEY_SHIFT = 2,
    RS3_KEY_ALT = 4
};

/**
 * Toggles an overhead prayer with the caller's key binding.
 *
 * The request is sent only when the prayer is observed inactive, another
 * overhead state is known, prayer points are positive, and the game is in the
 * foreground with a snapshot at most 150 ms old. The key mapping is caller
 * calibration, not decoded from the action bar. User-held modifiers or the key
 * itself cause rejection. RS3_OK means the input was accepted only; prayer
 * unlocks and server-side acknowledgement remain the caller's responsibility.
 */
RS3_API int32_t rs3_set_prayer(const rs3_snapshot *snapshot, uint32_t prayer, uint32_t virtual_key,
                               uint32_t modifiers);

#ifdef __cplusplus
}
#endif

#endif /* RS3_COMBAT_H */
