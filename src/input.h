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

#endif /* RS3_INPUT_H */
