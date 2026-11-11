/*
 * Local player hit marks, read from the verified local player actor.
 *
 * Only the original executable has a verified actor layout. Hit marks never
 * identify their attacker, and an amount is not assumed to be damage.
 */
#ifndef RS3_PLAYER_HITS_H
#define RS3_PLAYER_HITS_H

#include <stdint.h>

#include "context.h"

/**
 * Stages the local player's hit marks into the snapshot. Call after
 * combat_stage(): the decode needs the scene state. This optional decode never
 * replaces the frame's failure state.
 */
void player_hits_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state);

/**
 * Assigns the actor instance and publishes newly active hit marks. Call after
 * combat_commit() so the scene epoch is set. Only adjacent samples of the same
 * actor, scene and process can produce events; anything else is a baseline.
 */
void player_hits_commit(rs3_context *context, rs3_snapshot *snapshot);

#endif /* RS3_PLAYER_HITS_H */
