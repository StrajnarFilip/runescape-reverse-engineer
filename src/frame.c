/* Decoding of one complete frame from the attached client. */
#include "frame.h"

#include <stdio.h>
#include <string.h>

#include "combat.h"
#include "inventory.h"
#include "memory.h"
#include "npcs.h"
#include "player.h"
#include "player_hits.h"
#include "position.h"
#include "spatial.h"
#include "variables.h"

/** Context addresses captured at the start of a frame and verified at its end. */
typedef struct frame_contexts
{
    uint64_t player_address;
    uint64_t client_address;
    uint64_t world_state;
    uint64_t scene_client; /**< 0 when the profile has no player tile. */
} frame_contexts;

/** Captures the player, client and world contexts and reads the player fields. */
static int decode_player(rs3_context *context, rs3_snapshot *snapshot, frame_contexts *contexts)
{
    rs3_player *player = &snapshot->player;
    if (context->target.profile->has_position &&
        !position_player_in_scene(context, &contexts->scene_client))
    {
        return context_fail(context, RS3_NOT_SET, "Player is outside the active game scene");
    }
    if (!domain_capture(context, DOMAIN_PLAYER, &contexts->player_address) ||
        !domain_capture(context, DOMAIN_CLIENT, &contexts->client_address) ||
        !player_decode_statistics(context, contexts->client_address, player) ||
        !player_read_variables(context, contexts->player_address, contexts->client_address,
                               player) ||
        !memory_read_pointer(context, contexts->client_address + LAYOUT_STATE_POINTER_OFFSET,
                             &contexts->world_state))
    {
        return 0;
    }
    if (contexts->world_state == 0)
    {
        return context_fail(context, RS3_READ_FAILED, "Client clock unavailable");
    }
    if (!memory_read(context, contexts->world_state + LAYOUT_LOGIC_CLOCK_OFFSET,
                     &snapshot->info.logic_tick, sizeof snapshot->info.logic_tick) ||
        !player_check_plausibility(context, player))
    {
        return 0;
    }
    player_decode_global_cooldown(player, snapshot->info.logic_tick);
    return 1;
}

/** Returns nonzero when every captured context still has the same address. */
static int contexts_unchanged(rs3_context *context, const frame_contexts *contexts)
{
    uint64_t address_after;
    if (!domain_capture(context, DOMAIN_PLAYER, &address_after) ||
        address_after != contexts->player_address ||
        !domain_capture(context, DOMAIN_CLIENT, &address_after) ||
        address_after != contexts->client_address ||
        !memory_read_pointer(context, contexts->client_address + LAYOUT_STATE_POINTER_OFFSET,
                             &address_after) ||
        address_after != contexts->world_state)
    {
        return 0;
    }
    if (contexts->scene_client != 0 && (!position_player_in_scene(context, &address_after) ||
                                        address_after != contexts->scene_client))
    {
        return 0;
    }
    return 1;
}

int frame_decode(rs3_context *context, rs3_snapshot *snapshot)
{
    frame_contexts contexts = {0};
    if (!decode_player(context, snapshot, &contexts))
    {
        return 0;
    }
    snapshot->info.status = RS3_AVAILABLE;

    /* NPC failures leave the player fields usable and only make the frame partial. */
    uint32_t runtime_ids[RS3_MAX_NPCS];
    uint32_t listed_count = 0;
    uint64_t npc_manager = 0;
    npc_list_result npc_result = npc_decode_list(context, snapshot, contexts.world_state,
                                                 runtime_ids, &listed_count, &npc_manager);
    int npcs_decoded = npc_result == NPC_LIST_DECODED;
    if (!npcs_decoded)
    {
        snapshot->info.npc_count = 0;
        snapshot->info.hit_count = 0;
        snapshot->info.loaded_npcs = 0;
        snapshot->info.status = RS3_PARTIAL;
        snapshot->info.reason = RS3_READ_FAILED;
        snprintf(snapshot->info.message, sizeof snapshot->info.message, "NPCs: %s",
                 context->failure_message);
    }
    else if (snapshot->info.skipped_npcs != 0)
    {
        snapshot->info.status = RS3_PARTIAL;
    }

    inventory_stage(context, snapshot, contexts.player_address, contexts.client_address,
                    contexts.world_state);
    combat_stage(context, snapshot, contexts.player_address, contexts.world_state);
    player_hits_stage(context, snapshot, contexts.world_state);
    spatial_stage(context, snapshot, contexts.world_state);

    if (!contexts_unchanged(context, &contexts))
    {
        return context_fail(context, RS3_READ_FAILED, "World context changed during read");
    }

    combat_commit(context, snapshot, contexts.world_state);
    player_hits_commit(context, snapshot);
    if (npcs_decoded)
    {
        npc_commit_frame(context, snapshot, runtime_ids, listed_count, npc_manager,
                         contexts.world_state);
    }
    inventory_commit(context, snapshot, contexts.world_state);
    spatial_commit(context, snapshot);
    player_track_changes(context, snapshot, contexts.world_state);
    return 1;
}
