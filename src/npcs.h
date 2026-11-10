/*
 * Loaded NPC registry: identities, health, hit mark rings and hit events.
 *
 * Decoding stages records into the working frame; npc_commit_frame() assigns
 * identities and publishes events only after the frame passed every
 * consistency check.
 */
#ifndef RS3_NPCS_H
#define RS3_NPCS_H

#include <stdint.h>

#include "context.h"

/** Bytes of one hit mark ring record. */
#define HIT_RECORD_BYTES 24u

/** Result of one pass over the NPC registry. */
typedef enum npc_list_result
{
    NPC_LIST_DECODED, /**< The list was read consistently. */
    NPC_LIST_CHANGED, /**< The list changed during the read; a retry may succeed. */
    NPC_LIST_FAILED   /**< The registry is unavailable or invalid. */
} npc_list_result;

/** Result of decoding one hit mark record. */
typedef enum hit_decode_result
{
    HIT_INVALID = -1, /**< The record is out of range. */
    HIT_ABSENT = 0,   /**< The slot is empty or expired. */
    HIT_DECODED = 1   /**< hit was filled. */
} hit_decode_result;

/** Forgets every NPC identity and the registry they belong to. */
void npc_reset_identities(rs3_context *context);

/** Decodes one 24-byte hit record relative to the current logic tick. */
hit_decode_result npc_decode_hit(const unsigned char *record, uint32_t logic_tick, uint32_t slot,
                                 rs3_hit *hit);

/**
 * Decodes the loaded NPC list into the snapshot.
 *
 * runtime_ids receives the listed IDs (RS3_MAX_NPCS capacity), *listed_count
 * their number and *manager_address the registry manager. An NPC that fails to
 * decode is skipped and counted; it does not fail the list.
 */
npc_list_result npc_decode_list(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state,
                                uint32_t *runtime_ids, uint32_t *listed_count,
                                uint64_t *manager_address);

/** Assigns instance IDs, retires despawned identities and publishes new hit events. */
void npc_commit_frame(rs3_context *context, rs3_snapshot *snapshot, const uint32_t *runtime_ids,
                      uint32_t listed_count, uint64_t manager_address, uint64_t world_state);

#endif /* RS3_NPCS_H */
