/*
 * Raw NPC animation sequences and NPC action events.
 *
 * Sequence IDs describe animations only; no attack style, target or prayer
 * deadline is inferred here.
 */
#ifndef RS3_NPC_ANIMATION_H
#define RS3_NPC_ANIMATION_H

#include <stdint.h>

#include "context.h"

/** Bytes of an NPC actor copied in one bulk read; ends after the maximum health field. */
#define NPC_ACTOR_BYTES (LAYOUT_NPC_HEALTH_MAX_OFFSET + 4u)

/**
 * Decodes the animation of npc, which is being staged at frame index npc_index.
 *
 * actor_bytes is the bulk copy of the actor at actor_address. The sequences
 * are re-read from the live actor and must match the bulk copy; on success the
 * application stamp is staged for npc_animation_commit_action().
 */
void npc_animation_decode(rs3_context *context, rs3_snapshot *snapshot, uint32_t npc_index,
                          const rs3_npc *npc, uint64_t actor_address,
                          const unsigned char *actor_bytes);

/**
 * Emits an action event when the NPC applied a new action since the previous
 * adjacent sample of the same instance and scene. Called with the event queue
 * lock held, after frame and identity verification.
 */
void npc_animation_commit_action(rs3_context *context, const rs3_snapshot *snapshot,
                                 npc_identity *identity, uint32_t npc_index);

#endif /* RS3_NPC_ANIMATION_H */
