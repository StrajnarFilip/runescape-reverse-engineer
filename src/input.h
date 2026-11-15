/*
 * Explicit, opt-in foreground input. Sampling never calls this path.
 *
 * The helpers below are exposed for the deterministic fixtures; the public
 * entry points are declared in rs3/rs3.h and rs3/combat.h.
 */
#ifndef RS3_INPUT_H
#define RS3_INPUT_H

#include <windows.h>

#include <stdint.h>

#include "rs3/rs3.h"

/** Largest key chord built by input_build_key_chord(): three modifiers, key down and up. */
#define MAX_CHORD_INPUTS 8u
/** Two complete chords, submitted in one ordered foreground SendInput call. */
#define MAX_CHORD_BATCH_INPUTS  (2u * MAX_CHORD_INPUTS)
#define MAX_TARGET_BATCH_INPUTS (3u * MAX_CHORD_INPUTS)

/* Additive public types are registered by L in rs3/rs3.h. */
#ifndef RS3_KEY_BATCH_TYPES
#define RS3_KEY_BATCH_TYPES
#define RS3_MAX_KEY_ACTIONS 3u
enum rs3_key_action_kind
{
    RS3_KEY_ACTION_ABILITY = 1,
    RS3_KEY_ACTION_ITEM = 2,
    RS3_KEY_ACTION_TARGET_CYCLE = 3
};
typedef struct rs3_key_action
{
    uint32_t kind;
    uint32_t virtual_key;
    uint32_t modifiers;
    uint32_t ability_id;
    uint32_t slot;
    int32_t expected_item_id;
    int32_t expected_config_id;
    uint32_t reserved;
    uint64_t expected_token;
    uint64_t expected_instance_id;
} rs3_key_action;
#endif

/** Prepare 1..3 complete, validated chords without input or partial output. */
int32_t input_prepare_key_batch(const rs3_snapshot *snapshot, const rs3_key_action *actions,
                                uint32_t action_size, uint32_t action_count,
                                INPUT events[MAX_TARGET_BATCH_INPUTS], uint32_t *input_count);
/** Return the additive key action's C layout size for foreign-language callers. */
RS3_API uint32_t rs3_key_action_sizeof(void);
/** Submit one bounded ordered batch; target-cycle selection must be checked afterward. */
RS3_API int32_t rs3_activate_key_batch(const rs3_snapshot *snapshot, const rs3_key_action *actions,
                                       uint32_t action_size, uint32_t action_count);

/**
 * Projects a tile onto the calibrated minimap.
 * Returns RS3_E_ARGUMENT for an invalid calibration, RS3_E_STATE when the
 * calibration does not match the sampled tile, RS3_E_TARGET when the tile is
 * off the map or on another plane, and RS3_OK with *pixel filled otherwise.
 * NULL arguments and numerically singular fitted axes are invalid. All pixel
 * coordinates are physical; callers must revalidate native UI/view calibration.
 */
int32_t input_project_minimap_tile(const rs3_position *player_tile, const rs3_minimap *minimap,
                                   int32_t tile_x, int32_t tile_y, int32_t plane, POINT *pixel);

/**
 * Builds modifier downs, the key down and up, and modifier ups in reverse
 * order. modifiers is a combination of enum rs3_key_modifiers.
 */
int32_t input_build_key_chord(uint32_t virtual_key, uint32_t modifiers,
                              INPUT chord[MAX_CHORD_INPUTS], uint32_t *input_count);

/**
 * Returns the releases for keys and buttons pressed by the first inserted
 * events, newest first. Unrelated keys are never released.
 */
uint32_t input_release_inserted(const INPUT *events, uint32_t inserted_count,
                                INPUT releases[MAX_CHORD_INPUTS]);

/** Prepare a guarded sampled-ability chord without sending input; exposed for fixture tests. */
int32_t input_prepare_ability_chord(const rs3_snapshot *snapshot, uint32_t struct_id,
                                    uint32_t virtual_key, uint32_t modifiers,
                                    INPUT chord[MAX_CHORD_INPUTS], uint32_t *input_count);

/** Prepare an exact, unambiguous backpack-item chord without sending input. */
int32_t input_prepare_item_chord(const rs3_snapshot *snapshot, uint32_t slot,
                                 int32_t expected_item_id, uint64_t expected_token,
                                 uint32_t virtual_key, uint32_t modifiers,
                                 INPUT chord[MAX_CHORD_INPUTS], uint32_t *input_count);

/** Prepare both exact actions before exposing any ability-then-item batch events. */
int32_t input_prepare_ability_item_batch(const rs3_snapshot *snapshot, uint32_t struct_id,
                                         uint32_t ability_key, uint32_t ability_modifiers,
                                         uint32_t slot, int32_t expected_item_id,
                                         uint64_t expected_token, uint32_t item_key,
                                         uint32_t item_modifiers,
                                         INPUT events[MAX_CHORD_BATCH_INPUTS],
                                         uint32_t *input_count);

/** Submit an ordered ability-then-item batch; acceptance is not a server-tick guarantee. */
RS3_API int32_t rs3_activate_ability_then_item(const rs3_snapshot *snapshot, uint32_t struct_id,
                                               uint32_t ability_key, uint32_t ability_modifiers,
                                               uint32_t slot, int32_t expected_item_id,
                                               uint64_t expected_token, uint32_t item_key,
                                               uint32_t item_modifiers);

/* Additive public declarations are routed through L's shared ABI registration. */
/** Send a balanced sampled-ability chord; modifiers use rs3_key_modifiers. */
RS3_API int32_t rs3_activate_ability_chord(const rs3_snapshot *snapshot, uint32_t struct_id,
                                           uint32_t virtual_key, uint32_t modifiers);
/** Send a calibrated item's chord only while its exact slot, ID and nonzero token agree. */
RS3_API int32_t rs3_activate_item_chord(const rs3_snapshot *snapshot, uint32_t slot,
                                        int32_t expected_item_id, uint64_t expected_token,
                                        uint32_t virtual_key, uint32_t modifiers);

#endif /* RS3_INPUT_H */
