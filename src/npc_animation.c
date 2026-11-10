/* Raw NPC animation sequences and NPC action events. */
#include "npc_animation.h"

#include "clock.h"
#include "decode_utils.h"
#include "event_ring.h"
#include "memory.h"

/** Sequence ID used by the client for an idle animation slot. */
#define IDLE_SEQUENCE   (-1)
#define MAX_SEQUENCE_ID 1000000

static int sequence_is_plausible(int32_t sequence)
{
    return sequence >= IDLE_SEQUENCE && sequence <= MAX_SEQUENCE_ID;
}

void npc_animation_decode(rs3_context *context, rs3_snapshot *snapshot, uint32_t npc_index,
                          const rs3_npc *npc, uint64_t actor_address,
                          const unsigned char *actor_bytes)
{
    const client_profile *profile = context->target.profile;
    rs3_npc_animation *animation = &snapshot->npc_animations[npc_index];
    memset(animation, 0, sizeof *animation);
    animation->runtime_id = npc->runtime_id;
    animation->config_id = npc->config_id;
    animation->reason = RS3_UNSUPPORTED_CLIENT;
    if (profile->npc_animation_vtable_rva == 0)
    {
        return;
    }
    animation->reason = RS3_READ_FAILED;

    uint64_t expected_component = context->target.module_base + profile->npc_animation_vtable_rva;
    uint64_t bulk_component = decode_u64(actor_bytes + LAYOUT_NPC_ANIMATION_COMPONENT_OFFSET);
    int32_t bulk_sequences[2] = {decode_i32(actor_bytes + LAYOUT_NPC_ACTION_SEQUENCE_OFFSET),
                                 decode_i32(actor_bytes + LAYOUT_NPC_MOVEMENT_SEQUENCE_OFFSET)};
    uint32_t stamp_offset = profile->npc_action_stamp_offset;
    uint32_t application_stamp = 0;
    if (stamp_offset > NPC_ACTOR_BYTES - sizeof application_stamp ||
        bulk_component != expected_component || !sequence_is_plausible(bulk_sequences[0]) ||
        !sequence_is_plausible(bulk_sequences[1]))
    {
        return;
    }

    /* Re-read the live fields: the bulk copy must still describe the actor. */
    int32_t live_sequences[2];
    uint64_t live_component;
    if (!memory_read(context, actor_address + LAYOUT_NPC_ACTION_SEQUENCE_OFFSET, &live_sequences[0],
                     sizeof live_sequences[0]) ||
        !memory_read(context, actor_address + LAYOUT_NPC_MOVEMENT_SEQUENCE_OFFSET,
                     &live_sequences[1], sizeof live_sequences[1]))
    {
        return;
    }
    if (stamp_offset != 0 && (!memory_read(context, actor_address + stamp_offset,
                                           &application_stamp, sizeof application_stamp) ||
                              application_stamp != decode_u32(actor_bytes + stamp_offset)))
    {
        return;
    }
    if (!memory_read_pointer(context, actor_address + LAYOUT_NPC_ANIMATION_COMPONENT_OFFSET,
                             &live_component) ||
        live_component != bulk_component ||
        memcmp(bulk_sequences, live_sequences, sizeof bulk_sequences) != 0)
    {
        return;
    }

    animation->valid = 1;
    animation->reason = RS3_REASON_NONE;
    animation->action_sequence = bulk_sequences[0];
    animation->movement_sequence = bulk_sequences[1];
    context->npcs.staged_action_stamps[npc_index] = application_stamp;
}

void npc_animation_commit_action(rs3_context *context, const rs3_snapshot *snapshot,
                                 npc_identity *identity, uint32_t npc_index)
{
    const rs3_npc_animation *animation = &snapshot->npc_animations[npc_index];
    uint64_t scene_epoch = snapshot->combat.scene_epoch;
    if (scene_epoch == 0 || !animation->valid)
    {
        identity->action_scene_epoch = 0;
        return;
    }

    int has_stamp = context->target.profile->npc_action_stamp_offset != 0;
    int32_t action_sequence = animation->action_sequence;
    uint32_t application_stamp = context->npcs.staged_action_stamps[npc_index];
    /*
     * The client writes the stamp after applying the sequence. On profiles
     * with a stamp, use it as the edge so one application cannot emit twice.
     */
    int action_applied = has_stamp ? application_stamp != identity->action_stamp
                                   : action_sequence != identity->action_sequence;
    int adjacent_sample = identity->action_scene_epoch == scene_epoch &&
                          identity->action_sample_sequence + 1 == snapshot->info.sequence;

    if (adjacent_sample && action_sequence >= 0 && action_applied)
    {
        uint64_t sequence = ++context->npc_action_event_sequence;
        rs3_npc_action_event *event =
            &context->npc_action_events[event_ring_index(sequence, NPC_ACTION_EVENT_CAPACITY)];
        *event = (rs3_npc_action_event){
            .sequence = sequence,
            .observed_ns = clock_monotonic_ns(),
            .sample_sequence = snapshot->info.sequence,
            .scene_epoch = scene_epoch,
            .process_creation_time = context->target.process_creation_time,
            .instance_id = identity->instance_id,
            .pid = context->target.pid,
            .runtime_id = animation->runtime_id,
            .config_id = animation->config_id,
            .previous_action_sequence = identity->action_sequence,
            .action_sequence = action_sequence,
            .logic_tick = snapshot->info.logic_tick,
            .application_stamp = application_stamp,
            .flags = has_stamp ? RS3_NPC_ACTION_HAS_APPLICATION_STAMP : 0,
        };
    }
    identity->action_scene_epoch = scene_epoch;
    identity->action_sample_sequence = snapshot->info.sequence;
    identity->action_sequence = action_sequence;
    identity->action_stamp = application_stamp;
}
