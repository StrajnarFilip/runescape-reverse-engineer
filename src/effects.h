/* Read-only collection of player-effect candidates. */
#ifndef RS3_EFFECTS_H
#define RS3_EFFECTS_H

#include "context.h"

/** Stage an independent observation; semantic mappings require live validation. */
void effects_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                   uint64_t client_address);
/** Clear all values and identities when the enclosing frame is unavailable. */
void effects_clear(rs3_snapshot *snapshot);

#endif
