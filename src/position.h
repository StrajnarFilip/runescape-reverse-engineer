/*
 * Local player actor and tile.
 *
 * The tile and the local-player hit marks resolve the actor through the same
 * position_find_local_actor(). Only the Compatibility Mode profile pins the
 * tile layout; docs/validation/profile-unification.json records the evidence.
 */
#ifndef RS3_POSITION_H
#define RS3_POSITION_H

#include <stdint.h>

#include "context.h"

/**
 * Reads the client scene object and returns nonzero while it reports the
 * in-game state. *scene_client receives the scene object address. On the
 * Compatibility Mode client it is the world state that the client context's
 * state pointer also holds.
 */
int position_player_in_scene(rs3_context *context, uint64_t *scene_client);

/**
 * Resolves the local player actor of a world state: the player context's
 * actor override when set, otherwise the player table slot of the context's
 * player index. The player context must name world_state as its owner. When
 * the profile pins a player actor vtable, the actor must carry it and store
 * the same player index as its runtime ID. Returns nonzero with *actor_address
 * and *player_index set. Failed reads set the context failure state.
 */
int position_find_local_actor(rs3_context *context, uint64_t world_state, uint64_t *actor_address,
                              uint32_t *player_index);

/**
 * Reads the player tile. Consecutive reads must agree on both the actor and
 * its tile; otherwise the position stays unchanged and 0 is returned.
 */
int position_decode(rs3_context *context, rs3_position *position);

#endif /* RS3_POSITION_H */
