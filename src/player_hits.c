/* Local player hit marks, read from the verified local player actor. */
#include "player_hits.h"

#include <string.h>

#include "clock.h"
#include "decode_utils.h"
#include "event_ring.h"
#include "memory.h"
#include "npcs.h"

_Static_assert(sizeof(rs3_player_hit_info) == 24, "ABI player hit info layout");
_Static_assert(sizeof(rs3_player_hit_event) == 96, "ABI player hit event layout");

#define MAX_PLAYER_INDEX 65535
#define MAX_PLAYER_SLOTS 65536u

/**
 * Resolves the local player actor from the world state: the context override
 * when set, otherwise the player table slot of the context's player index.
 * The actor must have the profile's virtual table and carry the same index.
 */
static int find_local_actor(rs3_context *context, uint64_t world_state, uint64_t *actor_address,
                            uint32_t *player_index)
{
    uint64_t player_context;
    uint64_t context_owner;
    int32_t index;
    if (!memory_read_pointer(context, world_state + LAYOUT_LOCAL_ACTOR_CONTEXT_OFFSET,
                             &player_context) ||
        player_context == 0 || !memory_read_pointer(context, player_context, &context_owner) ||
        context_owner != world_state ||
        !memory_read(context, player_context + LAYOUT_LOCAL_ACTOR_INDEX_OFFSET, &index,
                     sizeof index) ||
        index < 0 || index > MAX_PLAYER_INDEX ||
        !memory_read_pointer(context, player_context + LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET,
                             actor_address))
    {
        return 0;
    }

    if (*actor_address == 0)
    {
        uint64_t player_table;
        uint64_t slot_range[2]; /* [begin, end) of the slot pointer array. */
        uint64_t player_slot;
        if (!memory_read_pointer(context, world_state + LAYOUT_LOCAL_ACTOR_TABLE_OFFSET,
                                 &player_table) ||
            player_table == 0 ||
            !memory_read(context, player_table + LAYOUT_LOCAL_ACTOR_RANGE_OFFSET, slot_range,
                         sizeof slot_range) ||
            !is_valid_remote_pointer(slot_range[0]) || slot_range[1] < slot_range[0])
        {
            return 0;
        }
        uint64_t range_bytes = slot_range[1] - slot_range[0];
        uint64_t slot_count = range_bytes / sizeof player_slot;
        if (range_bytes % sizeof player_slot != 0 || slot_count > MAX_PLAYER_SLOTS ||
            (uint64_t)index >= slot_count ||
            !memory_read_pointer(context, slot_range[0] + (uint64_t)index * sizeof player_slot,
                                 &player_slot) ||
            player_slot == 0 ||
            !memory_read_pointer(context, player_slot + LAYOUT_LOCAL_ACTOR_SLOT_ENTITY_OFFSET,
                                 actor_address))
        {
            return 0;
        }
    }

    uint64_t actor_vtable;
    uint16_t actor_index;
    uint64_t expected_vtable =
        context->target.module_base + context->target.profile->player_actor_vtable_rva;
    if (*actor_address == 0 || !memory_read_pointer(context, *actor_address, &actor_vtable) ||
        actor_vtable != expected_vtable ||
        !memory_read(context, *actor_address + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET, &actor_index,
                     sizeof actor_index) ||
        actor_index != (uint32_t)index)
    {
        return 0;
    }
    *player_index = (uint32_t)index;
    return 1;
}

/** Reads the actor's ring and verifies that the actor and ring are unchanged afterwards. */
static int read_player_ring(rs3_context *context, uint64_t world_state, uint32_t logic_tick,
                            uint64_t *actor_address, uint32_t *player_index,
                            rs3_hit hits[MAX_HITS_PER_RING], uint32_t *hit_count)
{
    uint64_t ring_address;
    uint64_t actor_after;
    uint32_t player_index_after;
    uint64_t ring_after;
    return find_local_actor(context, world_state, actor_address, player_index) &&
           memory_read_pointer(context, *actor_address + LAYOUT_LOCAL_ACTOR_HITMARKS_OFFSET,
                               &ring_address) &&
           npc_read_hit_ring(context, ring_address, logic_tick, hits, hit_count) &&
           find_local_actor(context, world_state, &actor_after, &player_index_after) &&
           actor_after == *actor_address && player_index_after == *player_index &&
           memory_read_pointer(context, *actor_address + LAYOUT_LOCAL_ACTOR_HITMARKS_OFFSET,
                               &ring_after) &&
           ring_after == ring_address;
}

void player_hits_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    rs3_player_hit_info *info = &snapshot->player_hit_info;
    memset(info, 0, sizeof *info);
    snapshot->player_actor_address = 0;
    info->status = RS3_UNAVAILABLE;
    info->reason = RS3_NOT_SET;
    if (context->target.profile->player_actor_vtable_rva == 0)
    {
        info->reason = RS3_UNSUPPORTED_CLIENT;
        return;
    }
    if (snapshot->combat.scene_state != (int32_t)LAYOUT_IN_GAME_STATE)
    {
        return;
    }

    /* Preserve the frame's failure state: this optional decode must not replace it. */
    uint32_t saved_reason = context->failure_reason;
    char saved_message[FAILURE_MESSAGE_CAPACITY];
    memcpy(saved_message, context->failure_message, sizeof saved_message);

    uint64_t actor_address;
    uint32_t player_index;
    rs3_hit hits[MAX_HITS_PER_RING];
    uint32_t hit_count = 0;
    if (read_player_ring(context, world_state, snapshot->info.logic_tick, &actor_address,
                         &player_index, hits, &hit_count))
    {
        info->status = RS3_AVAILABLE;
        info->reason = RS3_REASON_NONE;
        info->player_index = player_index;
        info->hit_count = hit_count;
        snapshot->player_actor_address = actor_address;
        memcpy(snapshot->player_hits, hits, hit_count * sizeof hits[0]);
    }
    else
    {
        info->reason = RS3_READ_FAILED;
    }

    context->failure_reason = saved_reason;
    memcpy(context->failure_message, saved_message, sizeof saved_message);
}

/** Starts a new instance when the actor, scene, process or player index changed. */
static void track_player_instance(rs3_context *context, const rs3_snapshot *snapshot)
{
    player_hit_tracker *tracker = &context->player_hits;
    int same_instance = tracker->actor_address == snapshot->player_actor_address &&
                        tracker->scene_epoch == snapshot->combat.scene_epoch &&
                        tracker->process_creation_time == context->target.process_creation_time &&
                        tracker->player_index == snapshot->player_hit_info.player_index;
    if (same_instance)
    {
        return;
    }
    uint64_t next_instance_id = tracker->instance_id + 1;
    memset(tracker, 0, sizeof *tracker);
    tracker->instance_id = next_instance_id;
    tracker->actor_address = snapshot->player_actor_address;
    tracker->scene_epoch = snapshot->combat.scene_epoch;
    tracker->process_creation_time = context->target.process_creation_time;
    tracker->player_index = snapshot->player_hit_info.player_index;
}

static void publish_player_hit_event(rs3_context *context, const rs3_snapshot *snapshot,
                                     const rs3_hit *hit)
{
    const player_hit_tracker *tracker = &context->player_hits;
    uint64_t sequence = ++context->player_hit_event_sequence;
    context->player_hit_events[event_ring_index(sequence, PLAYER_HIT_EVENT_CAPACITY)] =
        (rs3_player_hit_event){
            .sequence = sequence,
            .observed_ns = clock_monotonic_ns(),
            .sample_sequence = snapshot->info.sequence,
            .scene_epoch = snapshot->combat.scene_epoch,
            .process_creation_time = context->target.process_creation_time,
            .instance_id = tracker->instance_id,
            .pid = context->target.pid,
            .player_index = tracker->player_index,
            .hit = *hit,
        };
}

void player_hits_commit(rs3_context *context, rs3_snapshot *snapshot)
{
    player_hit_tracker *tracker = &context->player_hits;
    if (snapshot->combat.scene_epoch == 0 || snapshot->player_hit_info.status != RS3_AVAILABLE)
    {
        tracker->sample_sequence = 0;
        if (snapshot->combat.scene_epoch == 0)
        {
            tracker->actor_address = 0;
        }
        return;
    }
    track_player_instance(context, snapshot);

    /* Records already active after attachment or a gap are a baseline, not new hits. */
    int adjacent_sample =
        tracker->sample_sequence != 0 && tracker->sample_sequence + 1 == snapshot->info.sequence;
    if (!adjacent_sample)
    {
        tracker->reported_hit_slots = 0;
    }
    snapshot->player_hit_info.instance_id = tracker->instance_id;

    AcquireSRWLockExclusive(&context->event_queue_lock);
    for (uint32_t index = 0; index < snapshot->player_hit_info.hit_count; index++)
    {
        const rs3_hit *hit = &snapshot->player_hits[index];
        if (hit->state != RS3_HIT_ACTIVE)
        {
            continue;
        }
        /* Suppress a hit while its ring slot still holds the same record. */
        uint64_t slot_bit = 1ull << hit->slot;
        if ((tracker->reported_hit_slots & slot_bit) &&
            memcmp(&tracker->last_reported_hits[hit->slot], hit, sizeof *hit) == 0)
        {
            continue;
        }
        tracker->reported_hit_slots |= slot_bit;
        tracker->last_reported_hits[hit->slot] = *hit;
        if (adjacent_sample)
        {
            publish_player_hit_event(context, snapshot, hit);
        }
    }
    ReleaseSRWLockExclusive(&context->event_queue_lock);
    tracker->sample_sequence = snapshot->info.sequence;
}
