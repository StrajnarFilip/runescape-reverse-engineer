/* Native interface and action-bar acquisition. */
#ifndef RS3_INTERFACE_H
#define RS3_INTERFACE_H

#include "context.h"

/** Stage the five displayed rows, invalidating changing variable domains. */
void interface_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t client_address);
/** Clear both domains on failed frame/process/scene acquisition. */
void interface_clear(rs3_snapshot *snapshot);

/** Clear raw pointer-candidate data after any unavailable frame or process. */
void interface_pointer_clear(rs3_pointer_candidate *out);
/** Stage a guarded raw menu candidate; it remains unvalidated, never a click hit-test. */
void interface_pointer_stage(rs3_context *context, uint64_t world_state,
                             rs3_pointer_candidate *out);

#endif
