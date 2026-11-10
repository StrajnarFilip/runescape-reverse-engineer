/* Ability definitions, live cooldown timers and equipment requirement checks. */
#ifndef RS3_ABILITIES_H
#define RS3_ABILITIES_H

#include <stdint.h>

#include "context.h"

/**
 * Reads every client variable referenced by a catalog timer pair.
 *
 * The bucket array and the pages that contain variable nodes are read in
 * bulk; the selected nodes are re-read in a second pass so torn or reused
 * entries stay unknown.
 */
int abilities_read_timers(rs3_context *context, uint64_t client_address);

/**
 * Builds the ability records of the snapshot from the catalog, the timers read
 * by abilities_read_timers() (when timers_read is nonzero) and the sampled gear.
 */
void abilities_stage(rs3_context *context, rs3_snapshot *snapshot, int timers_read);

#endif /* RS3_ABILITIES_H */
