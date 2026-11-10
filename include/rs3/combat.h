#ifndef RS3_COMBAT_H
#define RS3_COMBAT_H
#include "prayer.h"
#ifdef __cplusplus
extern "C" {
#endif

/** Additive snapshot extension; existing ABI-v1 structures are unchanged.
 * A missing sparse variable is UNKNOWN, never an assumed zero. Bit positions
 * in known_mask/active_mask are RS3_PRAYER_BIT(enum rs3_prayer_id).
 */
typedef struct rs3_combat_state {
    uint64_t scene_epoch; /**< Local epoch; pair with PID/process creation time. */
    uint32_t status, reason, known_mask, active_mask;
    int32_t normal_raw, curses_raw, normal_necro_raw, curses_necro_raw;
    uint32_t raw_valid_mask; /**< Bits 0..3 correspond to raw fields. */
    int32_t scene_state; /**< Read client state; 30 is in-game on these profiles. */
} rs3_combat_state;

typedef struct rs3_npc_animation {
    uint64_t instance_id;
    uint32_t runtime_id;
    int32_t config_id;
    uint32_t valid, reason; /**< Both IDs read consistently; no attack classification. */
    int32_t action_sequence, movement_sequence; /**< -1 is a read idle/absent slot. */
} rs3_npc_animation;

enum rs3_npc_action_flags { RS3_NPC_ACTION_HAS_APPLICATION_STAMP=1 };

/** An observed application of an NPC action animation. Sequence IDs describe
 * animation, with no attack style, target, or prayer deadline implied.
 */
typedef struct rs3_npc_action_event {
    uint64_t sequence, observed_ns, sample_sequence, scene_epoch;
    uint64_t process_creation_time, instance_id;
    uint32_t pid, runtime_id;
    int32_t config_id, previous_action_sequence, action_sequence; /**< IDs from adjacent valid samples. */
    uint32_t logic_tick, application_stamp, flags;
} rs3_npc_action_event;

/** Local player's sampled hitmark ring, not a complete incoming attack schedule.
 * Amounts/types can represent healing, zeroes or text. No attacker is identified.
 */
typedef struct rs3_player_hit_info {
    uint32_t status, reason, player_index, hit_count;
    uint64_t instance_id;
} rs3_player_hit_info;
typedef struct rs3_player_hit_event {
    uint64_t sequence, observed_ns, sample_sequence, scene_epoch;
    uint64_t process_creation_time, instance_id;
    uint32_t pid, player_index;
    rs3_hit hit;
} rs3_player_hit_event;

RS3_API uint32_t rs3_combat_sizeof(uint32_t structure); /**< 1=state, 2=animation, 3=action event, 4=player hit info, 5=player hit event. */
RS3_API int32_t rs3_snapshot_player_hit_info(const rs3_snapshot *, rs3_player_hit_info *, uint32_t out_size);
RS3_API int32_t rs3_snapshot_player_hits(const rs3_snapshot *, rs3_hit *, uint32_t capacity, uint32_t *required);
/** Independent 4096-record cursor queue. Existing active records after attachment
 * or a sampling gap establish a baseline, not freshly timed hit events.
 */
RS3_API int32_t rs3_player_hit_events_since(rs3_context *, uint64_t after, rs3_player_hit_event *,
    uint32_t capacity, uint32_t *written, uint64_t *next, uint64_t *lost);
RS3_API int32_t rs3_snapshot_combat(const rs3_snapshot *, rs3_combat_state *, uint32_t out_size);
RS3_API int32_t rs3_snapshot_npc_animations(const rs3_snapshot *, rs3_npc_animation *,
    uint32_t capacity, uint32_t *required);
/** Read a bounded event queue with a caller-owned cursor initially set to 0.
 * Pass next as after on the next call. lost counts overwritten queue entries.
 * observed_ns uses rs3_monotonic_ns. Match process and scene before consuming.
 * A fresh instance or a gap in sampling establishes a new baseline.
 */
RS3_API int32_t rs3_npc_action_events_since(rs3_context *, uint64_t after,
    rs3_npc_action_event *out, uint32_t capacity, uint32_t *written, uint64_t *next, uint64_t *lost);
/** Returns UNKNOWN for conflicting/insufficiently known overheads. */
RS3_API uint32_t rs3_combat_active_prayer(const rs3_combat_state *);

enum rs3_key_modifiers { RS3_KEY_CTRL=1, RS3_KEY_SHIFT=2, RS3_KEY_ALT=4 };
/** Toggle the requested overhead only when it is observed inactive, another
 * overhead is known, prayer points are positive and the game is foreground
 * with a snapshot at most 150ms old.
 * Key mapping is caller calibration, not decoded from the action bar. Existing
 * user-held modifier/key states cause rejection. OK means input accepted only.
 * Prayer unlocks and server-side acknowledgement remain caller responsibilities.
 */
RS3_API int32_t rs3_set_prayer(const rs3_snapshot *, uint32_t prayer,
    uint32_t virtual_key, uint32_t modifiers);
#ifdef __cplusplus
}
#endif
#endif
