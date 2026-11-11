/* Local player statistics, variables, global cooldown and change tracking. */
#ifndef RS3_PLAYER_H
#define RS3_PLAYER_H

#include <stdint.h>

#include "context.h"

/** Prayer points in tenths occupy bits 0..14 of RS3_PRAYER_PACKED. */
#define PLAYER_PRAYER_POINTS_MASK 0x7fff

/**
 * Verifies the client context and reads the base Prayer level.
 * client_address is the captured client domain context.
 */
int player_decode_statistics(rs3_context *context, uint64_t client_address, rs3_player *player);

/** Reads every enum rs3_variable value from the player and client variable tables. */
int player_read_variables(rs3_context *context, uint64_t player_address, uint64_t client_address,
                          rs3_player *player);

/** Rejects implausible life points and adrenaline values. */
int player_check_plausibility(rs3_context *context, const rs3_player *player);

/** Derives the global cooldown countdown from the client timer pair and the logic clock. */
void player_decode_global_cooldown(rs3_player *player, uint32_t logic_tick);

/**
 * Updates the clock-stall time and the last-change times of health, prayer and
 * adrenaline. Call only after the frame passed every consistency check.
 */
void player_track_changes(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state);

#endif /* RS3_PLAYER_H */
