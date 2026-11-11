/* Native spatial staging; owned by P1. */
#ifndef RS3_INTERNAL_SPATIAL_H
#define RS3_INTERNAL_SPATIAL_H

#include "context.h"
#include "rs3/spatial.h"

/** Decode a double-read actor pose after checking its vtable and runtime ID. */
int spatial_decode_actor(rs3_context *context, uint64_t actor, uint32_t vtable_rva,
                         uint32_t runtime_id, rs3_actor_spatial *out);
/** Stage current poses/camera before the parent frame's final context validation. */
void spatial_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state);
/** Bind staged records to committed scene and NPC instance identities. */
void spatial_commit(rs3_context *context, rs3_snapshot *snapshot);

#endif
