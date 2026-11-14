/* P2 read-only encounter staging. */
#ifndef RS3_INTERNAL_ENCOUNTER_H
#define RS3_INTERNAL_ENCOUNTER_H

#include "context.h"
#include "rs3/encounter.h"

/** Sample raw candidates coherently; leave uncalibrated semantic fields unknown. */
void encounter_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t world_state);
/** Clear public observations and private tick adjacency state together. */
void encounter_clear(rs3_snapshot *snapshot);
/** Bind observations to the parent frame's committed scene/process identity. */
void encounter_commit(rs3_context *context, rs3_snapshot *snapshot);

#endif
