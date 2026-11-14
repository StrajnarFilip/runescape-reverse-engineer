/* Native interface and action-bar acquisition. */
#ifndef RS3_INTERFACE_H
#define RS3_INTERFACE_H

#include "context.h"

/** Stage the five displayed rows, invalidating changing variable domains. */
void interface_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t client_address);
/** Clear both domains on failed frame/process/scene acquisition. */
void interface_clear(rs3_snapshot *snapshot);

#endif
