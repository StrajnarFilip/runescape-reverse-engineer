/* Native P4 observations; the P1 actor reader is the sole position decoder. */
#ifndef RS3_HAZARDS_INTERNAL_H
#define RS3_HAZARDS_INTERNAL_H

#include "context.h"

/**
 * Build candidates from the committed NPC identities and P1 poses. Call after
 * parent-context checks and spatial_commit. This performs no remote reads and
 * keeps no cross-frame cache. A failed NPC channel invalidates all candidates.
 */
void hazards_commit(rs3_context *context, rs3_snapshot *snapshot, int npcs_decoded);

/** Append freshly observed Telos scene-location tiles on the pinned profile. */
void hazards_read_telos_beams(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state);

#endif /* RS3_HAZARDS_INTERNAL_H */
