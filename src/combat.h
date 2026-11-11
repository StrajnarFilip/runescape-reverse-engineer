/*
 * Overhead prayer flags from locally decoded VarBit definitions, not pixels,
 * and the scene epoch that scopes combat observations.
 */
#ifndef RS3_COMBAT_INTERNAL_H
#define RS3_COMBAT_INTERNAL_H

#include <stdint.h>

#include "context.h"

/**
 * Stages the combat state of the snapshot. Optional prayer reads never
 * discard the already verified player and NPC fields: the context failure
 * state is restored before returning.
 */
void combat_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                  uint64_t world_state);

/** Assigns the scene epoch after the frame passed every consistency check. */
void combat_commit(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state);

#endif /* RS3_COMBAT_INTERNAL_H */
