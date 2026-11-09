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

RS3_API uint32_t rs3_combat_sizeof(uint32_t structure); /**< 1=state, 2=animation. */
RS3_API int32_t rs3_snapshot_combat(const rs3_snapshot *, rs3_combat_state *, uint32_t out_size);
RS3_API int32_t rs3_snapshot_npc_animations(const rs3_snapshot *, rs3_npc_animation *,
    uint32_t capacity, uint32_t *required);
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
