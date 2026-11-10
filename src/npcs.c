/* Loaded NPC registry: identities, health, hit mark rings and hit events. */
#include "npcs.h"

#include "decode_utils.h"
#include "event_ring.h"
#include "memory.h"
#include "npc_animation.h"

/* NPC manager layout. */
#define NPC_MANAGER_TABLE_OFFSET 16u /**< Bucket pointer array, followed by the bucket count. */
#define MAX_NPC_BUCKETS          1000000u

/* Bucket node layout: runtime ID, actor pointer and next pointer. */
#define NPC_NODE_BYTES             32u
#define NPC_NODE_RUNTIME_ID_OFFSET 0u
#define NPC_NODE_ACTOR_OFFSET      16u
#define NPC_NODE_NEXT_OFFSET       24u
#define MAX_NPC_CHAIN_LENGTH       1024u

/* Actor fields outside the profile. */
#define NPC_ACTOR_RUNTIME_ID_OFFSET 0x88u
#define NPC_CONFIG_ID_OFFSET        8u
#define MAX_NPC_CONFIG_ID           1000000
#define MAX_NPC_HEALTH              0xffffff

/* Client string object: 24 bytes. The final byte is a tag. */
#define CLIENT_STRING_BYTES           24u
#define CLIENT_STRING_INLINE_CAPACITY 23u
#define CLIENT_STRING_HEAP_FLAG       0x80u
#define CLIENT_STRING_DATA_OFFSET     0u
#define CLIENT_STRING_LENGTH_OFFSET   8u
#define MAX_NPC_NAME_BYTES            512u

/* Hit mark ring header and record layout. */
#define HIT_RING_HEADER_BYTES              40u
#define HIT_RING_CAPACITY_OFFSET           4u
#define HIT_RING_COUNT_OFFSET              24u
#define HIT_RING_DATA_OFFSET               32u
#define HIT_RECORD_TYPE_OFFSET             0u
#define HIT_RECORD_AMOUNT_OFFSET           4u
#define HIT_RECORD_END_TICK_OFFSET         8u
#define HIT_RECORD_SECONDARY_TYPE_OFFSET   12u
#define HIT_RECORD_SECONDARY_AMOUNT_OFFSET 16u
#define HIT_RECORD_DURATION_OFFSET         20u
#define MAX_HIT_TYPE_ID                    1000000
#define MAX_HIT_DURATION_TICKS             100000

/** Bitmap with one bit per 16-bit runtime ID. */
typedef struct runtime_id_set
{
    unsigned char bits[NPC_RUNTIME_ID_COUNT / 8];
} runtime_id_set;

static int runtime_id_set_contains(const runtime_id_set *set, uint32_t runtime_id)
{
    return (set->bits[runtime_id / 8] & (1u << (runtime_id % 8))) != 0;
}

static void runtime_id_set_add(runtime_id_set *set, uint32_t runtime_id)
{
    set->bits[runtime_id / 8] |= (unsigned char)(1u << (runtime_id % 8));
}

/** Bucket array of the NPC hash table, copied into the context's bucket buffer. */
typedef struct npc_table
{
    uint64_t bucket_count;
    uint64_t sentinel;
} npc_table;

void npc_reset_identities(rs3_context *context)
{
    npc_tracker *tracker = &context->npcs;
    memset(tracker->identities, 0, sizeof(npc_identity) * RS3_MAX_NPCS);
    memset(tracker->identity_slot_by_runtime_id, 0, sizeof tracker->identity_slot_by_runtime_id);
    tracker->registry_manager = 0;
    tracker->registry_world_state = 0;
}

/** Copies the NPC name from the client string object inside the actor bulk copy. */
static int decode_npc_name(rs3_context *context, uint64_t actor_address,
                           const unsigned char *actor_bytes, rs3_npc *npc)
{
    /*
     * The high bit of the tag selects a heap-backed string. An inline string
     * stores the unused byte count in the tag instead.
     */
    const unsigned char *string_object = actor_bytes + LAYOUT_NPC_NAME_OFFSET;
    uint32_t tag = string_object[CLIENT_STRING_BYTES - 1];
    int heap_backed = (tag & CLIENT_STRING_HEAP_FLAG) != 0;
    uint64_t length = heap_backed ? decode_u64(string_object + CLIENT_STRING_LENGTH_OFFSET)
                                  : (uint64_t)(uint32_t)(CLIENT_STRING_INLINE_CAPACITY - tag);
    uint64_t data_address = decode_u64(string_object + CLIENT_STRING_DATA_OFFSET);
    if (length > MAX_NPC_NAME_BYTES)
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid NPC name length");
    }
    if (length == 0)
    {
        return 1;
    }

    size_t name_length = (size_t)length;
    uint64_t offset_in_actor = data_address - actor_address;
    int data_inside_bulk_copy = data_address >= actor_address &&
                                offset_in_actor <= NPC_ACTOR_BYTES &&
                                name_length <= NPC_ACTOR_BYTES - offset_in_actor;
    if (!heap_backed)
    {
        memcpy(npc->name, string_object, name_length);
    }
    else if (data_inside_bulk_copy)
    {
        memcpy(npc->name, actor_bytes + (size_t)offset_in_actor, name_length);
    }
    else if (!memory_read(context, data_address, npc->name, name_length))
    {
        return 0;
    }

    for (size_t index = 0; index < name_length; index++)
    {
        if ((unsigned char)npc->name[index] < 32)
        {
            return context_fail(context, RS3_READ_FAILED, "Invalid NPC name");
        }
    }
    if (!is_valid_utf8(npc->name, (uint32_t)name_length))
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid NPC name encoding");
    }
    npc->name[name_length] = '\0';
    npc->name_length = (uint32_t)name_length;
    npc->flags |= RS3_NPC_NAME;
    return 1;
}

hit_decode_result npc_decode_hit(const unsigned char *record, uint32_t logic_tick, uint32_t slot,
                                 rs3_hit *hit)
{
    int32_t type_id = decode_i32(record + HIT_RECORD_TYPE_OFFSET);
    int32_t amount = decode_i32(record + HIT_RECORD_AMOUNT_OFFSET);
    int32_t duration_ticks = decode_i32(record + HIT_RECORD_DURATION_OFFSET);
    uint32_t end_tick = decode_u32(record + HIT_RECORD_END_TICK_OFFSET);

    /* Skip empty or expired slots. Future hits can still be present in the ring. */
    if (type_id < 0 || duration_ticks <= 0 || tick_difference(end_tick, logic_tick) <= 0)
    {
        return HIT_ABSENT;
    }
    if (type_id > MAX_HIT_TYPE_ID || amount < 0 || duration_ticks > MAX_HIT_DURATION_TICKS)
    {
        return HIT_INVALID;
    }

    memset(hit, 0, sizeof *hit);
    hit->slot = slot;
    hit->type_id = type_id;
    hit->amount = amount;
    hit->secondary_type_id = decode_i32(record + HIT_RECORD_SECONDARY_TYPE_OFFSET);
    hit->secondary_amount = decode_i32(record + HIT_RECORD_SECONDARY_AMOUNT_OFFSET);
    hit->end_tick = end_tick;
    hit->duration_ticks = (uint32_t)duration_ticks;
    hit->start_tick = end_tick - (uint32_t)duration_ticks;
    hit->state =
        tick_difference(logic_tick, hit->start_tick) >= 0 ? RS3_HIT_ACTIVE : RS3_HIT_SCHEDULED;
    return HIT_DECODED;
}

/** Appends the NPC's current hit records to the snapshot hit array. */
static int decode_npc_hits(rs3_context *context, uint64_t ring_address, rs3_snapshot *snapshot,
                           rs3_npc *npc)
{
    if (ring_address == 0)
    {
        return 1;
    }
    unsigned char header[HIT_RING_HEADER_BYTES];
    unsigned char records[MAX_HITS_PER_NPC * HIT_RECORD_BYTES];
    unsigned char records_again[MAX_HITS_PER_NPC * HIT_RECORD_BYTES];
    unsigned char count_and_data_again[16];
    if (!memory_read(context, ring_address, header, sizeof header))
    {
        return 0;
    }
    uint64_t record_count = decode_u64(header + HIT_RING_COUNT_OFFSET);
    uint64_t record_data = decode_u64(header + HIT_RING_DATA_OFFSET);
    uint32_t ring_capacity = decode_u32(header + HIT_RING_CAPACITY_OFFSET);
    if (record_count > ring_capacity || ring_capacity > MAX_HITS_PER_NPC ||
        (record_count != 0 && record_data == 0))
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid NPC hitmark ring");
    }

    /* Read the records twice and the header tail again: a torn ring is never a hit. */
    size_t record_bytes = (size_t)record_count * HIT_RECORD_BYTES;
    if (record_count != 0 && (!memory_read(context, record_data, records, record_bytes) ||
                              !memory_read(context, record_data, records_again, record_bytes)))
    {
        return 0;
    }
    if ((record_count != 0 && memcmp(records, records_again, record_bytes) != 0) ||
        !memory_read(context, ring_address + HIT_RING_COUNT_OFFSET, count_and_data_again,
                     sizeof count_and_data_again) ||
        memcmp(count_and_data_again, header + HIT_RING_COUNT_OFFSET, sizeof count_and_data_again) !=
            0)
    {
        return context_fail(context, RS3_READ_FAILED, "NPC hitmark ring changed during read");
    }

    for (uint32_t slot = 0; slot < record_count; slot++)
    {
        rs3_hit hit;
        hit_decode_result result = npc_decode_hit(records + slot * HIT_RECORD_BYTES,
                                                  snapshot->info.logic_tick, slot, &hit);
        if (result == HIT_INVALID)
        {
            return context_fail(context, RS3_READ_FAILED, "Invalid hitmark record");
        }
        if (result == HIT_DECODED)
        {
            snapshot->hits[snapshot->info.hit_count++] = hit;
            npc->hit_count++;
        }
    }
    return 1;
}

/** Walks the bucket chain of runtime_id and copies its node into node_bytes. */
static int find_npc_node(rs3_context *context, const npc_table *table, uint32_t runtime_id,
                         uint64_t *node_address, unsigned char node_bytes[NPC_NODE_BYTES])
{
    uint64_t node = context->bucket_buffer[runtime_id % table->bucket_count];
    uint64_t visited[MAX_NPC_CHAIN_LENGTH];
    unsigned visited_count = 0;
    while (node != 0 && node != table->sentinel)
    {
        if (visited_count == MAX_NPC_CHAIN_LENGTH)
        {
            return context_fail(context, RS3_READ_FAILED, "NPC bucket chain exceeds its bound");
        }
        for (unsigned index = 0; index < visited_count; index++)
        {
            if (visited[index] == node)
            {
                return context_fail(context, RS3_READ_FAILED, "NPC bucket chain cycle");
            }
        }
        visited[visited_count++] = node;
        if (!memory_read(context, node, node_bytes, NPC_NODE_BYTES))
        {
            return 0;
        }
        if (decode_u32(node_bytes + NPC_NODE_RUNTIME_ID_OFFSET) == runtime_id)
        {
            *node_address = node;
            return 1;
        }
        node = decode_u64(node_bytes + NPC_NODE_NEXT_OFFSET);
    }
    return context_fail(context, RS3_READ_FAILED, "NPC left the registry");
}

/** Decodes the health pair and its validity. */
static void decode_npc_health(const unsigned char *actor_bytes, rs3_npc *npc)
{
    npc->health_current = decode_i32(actor_bytes + LAYOUT_NPC_HEALTH_CURRENT_OFFSET);
    npc->health_max = decode_i32(actor_bytes + LAYOUT_NPC_HEALTH_MAX_OFFSET);
    npc->health_reason = RS3_NOT_TRANSMITTED;
    if (npc->health_max == 0)
    {
        return;
    }
    int pair_is_valid = npc->health_max > 0 && npc->health_max <= MAX_NPC_HEALTH &&
                        npc->health_current >= 0 && npc->health_current <= npc->health_max;
    if (pair_is_valid)
    {
        npc->flags |= RS3_NPC_HEALTH;
        npc->health_reason = RS3_REASON_NONE;
    }
    else
    {
        npc->health_reason = RS3_INVALID_STATS;
    }
}

/**
 * Decodes one NPC from a single bulk actor read, then verifies its identity
 * and hit ring. A failure rolls back this NPC's hit slice without touching
 * other NPCs.
 */
static int decode_one_npc(rs3_context *context, rs3_snapshot *snapshot, const npc_table *table,
                          uint32_t runtime_id)
{
    const client_profile *profile = context->target.profile;
    uint64_t expected_vtable = context->target.module_base + profile->npc_entity_vtable_rva;
    unsigned char node_bytes[NPC_NODE_BYTES];
    unsigned char actor_bytes[NPC_ACTOR_BYTES];
    uint64_t node_address = 0;
    uint32_t npc_index = snapshot->info.npc_count;

    rs3_npc npc = {0};
    npc.config_id = -1;
    npc.runtime_id = runtime_id;
    npc.hit_offset = snapshot->info.hit_count;

    if (!find_npc_node(context, table, runtime_id, &node_address, node_bytes))
    {
        return 0;
    }
    uint64_t actor_address = decode_u64(node_bytes + NPC_NODE_ACTOR_OFFSET);
    if (!memory_read(context, actor_address, actor_bytes, sizeof actor_bytes))
    {
        return 0;
    }
    uint32_t actor_runtime_id = decode_u32(actor_bytes + NPC_ACTOR_RUNTIME_ID_OFFSET) & 0xffffu;
    if (decode_u64(actor_bytes) != expected_vtable || actor_runtime_id != runtime_id)
    {
        return context_fail(context, RS3_READ_FAILED, "NPC identity mismatch");
    }

    uint64_t config_address = decode_u64(actor_bytes + LAYOUT_NPC_CONFIG_OVERRIDE_OFFSET);
    if (config_address == 0)
    {
        config_address = decode_u64(actor_bytes + LAYOUT_NPC_CONFIG_OFFSET);
    }
    if (config_address != 0)
    {
        if (!memory_read(context, config_address + NPC_CONFIG_ID_OFFSET, &npc.config_id,
                         sizeof npc.config_id))
        {
            return 0;
        }
        if (npc.config_id < 0 || npc.config_id > MAX_NPC_CONFIG_ID)
        {
            return context_fail(context, RS3_READ_FAILED, "Invalid NPC config ID");
        }
        npc.flags |= RS3_NPC_CONFIG;
    }
    if (!decode_npc_name(context, actor_address, actor_bytes, &npc))
    {
        return 0;
    }
    decode_npc_health(actor_bytes, &npc);

    uint64_t ring_address = decode_u64(actor_bytes + LAYOUT_NPC_HITMARKS_OFFSET);
    if (!decode_npc_hits(context, ring_address, snapshot, &npc))
    {
        goto rollback;
    }
    npc_animation_decode(context, snapshot, npc_index, &npc, actor_address, actor_bytes);

    /* The node, actor identity and ring pointer must be unchanged after the read. */
    unsigned char node_after[NPC_NODE_BYTES];
    uint16_t runtime_id_after;
    uint64_t vtable_after;
    uint64_t ring_after;
    if (!memory_read(context, node_address, node_after, sizeof node_after) ||
        !memory_read(context, actor_address + NPC_ACTOR_RUNTIME_ID_OFFSET, &runtime_id_after,
                     sizeof runtime_id_after) ||
        runtime_id_after != runtime_id ||
        !memory_read_pointer(context, actor_address, &vtable_after) ||
        vtable_after != decode_u64(actor_bytes) ||
        !memory_read_pointer(context, actor_address + LAYOUT_NPC_HITMARKS_OFFSET, &ring_after) ||
        ring_after != ring_address ||
        decode_u32(node_after + NPC_NODE_RUNTIME_ID_OFFSET) != runtime_id ||
        decode_u64(node_after + NPC_NODE_ACTOR_OFFSET) != actor_address)
    {
        goto rollback;
    }

    context->npcs.staged_actor_addresses[npc_index] = actor_address;
    snapshot->npcs[npc_index] = npc;
    snapshot->info.npc_count++;
    return 1;

rollback:
    snapshot->info.hit_count = npc.hit_offset;
    return context_fail(context, RS3_READ_FAILED, "NPC changed during read");
}

/** Reads the listed runtime IDs and rejects out-of-range or duplicate entries. */
static int read_runtime_ids(rs3_context *context, uint64_t manager_address, uint32_t *runtime_ids,
                            uint32_t *listed_count)
{
    if (!memory_read(context, manager_address + LAYOUT_NPC_COUNT_OFFSET, listed_count,
                     sizeof *listed_count) ||
        *listed_count > RS3_MAX_NPCS)
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid NPC count");
    }
    if (*listed_count != 0 && !memory_read(context, manager_address + LAYOUT_NPC_INDICES_OFFSET,
                                           runtime_ids, *listed_count * sizeof *runtime_ids))
    {
        return 0;
    }

    runtime_id_set seen = {0};
    for (uint32_t index = 0; index < *listed_count; index++)
    {
        uint32_t runtime_id = runtime_ids[index];
        if (runtime_id >= NPC_RUNTIME_ID_COUNT || runtime_id_set_contains(&seen, runtime_id))
        {
            return context_fail(context, RS3_READ_FAILED, "Invalid NPC index list");
        }
        runtime_id_set_add(&seen, runtime_id);
    }
    return 1;
}

/** Copies the bucket array of the NPC hash table into the context bucket buffer. */
static int read_npc_table(rs3_context *context, uint64_t manager_address, npc_table *table)
{
    unsigned char table_header[16];
    if (!memory_read(context, manager_address + NPC_MANAGER_TABLE_OFFSET, table_header,
                     sizeof table_header))
    {
        return 0;
    }
    uint64_t bucket_array = decode_u64(table_header);
    table->bucket_count = decode_u64(table_header + 8);
    if (bucket_array == 0 || table->bucket_count < 1 || table->bucket_count > MAX_NPC_BUCKETS)
    {
        return context_fail(context, RS3_READ_FAILED, "NPC table unavailable");
    }
    if (!context_reserve_buckets(context, table->bucket_count))
    {
        return 0;
    }
    size_t bucket_bytes = (size_t)table->bucket_count * sizeof(uint64_t);
    return memory_read(context, bucket_array, context->bucket_buffer, bucket_bytes) &&
           memory_read(context, bucket_array + bucket_bytes, &table->sentinel,
                       sizeof table->sentinel);
}

/** One pass over the registry; returns 0 with the failure recorded on the context. */
static int decode_list_once(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state,
                            uint32_t *runtime_ids, uint32_t *listed_count,
                            uint64_t *manager_address)
{
    snapshot->info.npc_count = 0;
    snapshot->info.hit_count = 0;
    snapshot->info.skipped_npcs = 0;

    uint64_t manager;
    if (!memory_read_pointer(context, world_state + LAYOUT_NPC_MANAGER_OFFSET, &manager))
    {
        return 0;
    }
    if (manager == 0)
    {
        return context_fail(context, RS3_READ_FAILED, "NPC manager unavailable");
    }
    uint64_t manager_vtable;
    if (!memory_read(context, manager, &manager_vtable, sizeof manager_vtable))
    {
        return 0;
    }
    if (manager_vtable !=
        context->target.module_base + context->target.profile->npc_manager_vtable_rva)
    {
        return context_fail(context, RS3_READ_FAILED, "NPC manager profile mismatch");
    }

    uint32_t count;
    npc_table table;
    if (!read_runtime_ids(context, manager, runtime_ids, &count) ||
        !read_npc_table(context, manager, &table))
    {
        return 0;
    }

    /* The listed IDs define what to visit; the hash buckets resolve them to actors. */
    snapshot->info.loaded_npcs = count;
    for (uint32_t index = 0; index < count; index++)
    {
        if (!decode_one_npc(context, snapshot, &table, runtime_ids[index]))
        {
            snapshot->info.skipped_npcs++;
        }
    }

    uint32_t count_after;
    uint32_t runtime_ids_after[RS3_MAX_NPCS];
    uint64_t manager_after;
    if (!memory_read(context, manager + LAYOUT_NPC_COUNT_OFFSET, &count_after,
                     sizeof count_after) ||
        count_after != count ||
        (count != 0 &&
         (!memory_read(context, manager + LAYOUT_NPC_INDICES_OFFSET, runtime_ids_after,
                       count * sizeof runtime_ids_after[0]) ||
          memcmp(runtime_ids, runtime_ids_after, count * sizeof runtime_ids_after[0]) != 0)) ||
        !memory_read_pointer(context, world_state + LAYOUT_NPC_MANAGER_OFFSET, &manager_after) ||
        manager_after != manager)
    {
        context_fail(context, RS3_READ_FAILED, "NPC list changed during read");
        return -1;
    }
    *listed_count = count;
    *manager_address = manager;
    return 1;
}

npc_list_result npc_decode_list(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state,
                                uint32_t *runtime_ids, uint32_t *listed_count,
                                uint64_t *manager_address)
{
    /* Retry a list that changed during the read once. */
    for (unsigned attempt = 0; attempt < 2; attempt++)
    {
        int result = decode_list_once(context, snapshot, world_state, runtime_ids, listed_count,
                                      manager_address);
        if (result > 0)
        {
            return NPC_LIST_DECODED;
        }
        if (result == 0)
        {
            return NPC_LIST_FAILED;
        }
    }
    return NPC_LIST_CHANGED;
}

/** Returns the identity of runtime_id, creating a fresh one for a new actor. */
static npc_identity *track_identity(rs3_context *context, uint32_t runtime_id,
                                    uint64_t actor_address)
{
    npc_tracker *tracker = &context->npcs;
    uint16_t slot = tracker->identity_slot_by_runtime_id[runtime_id];
    npc_identity *identity = NULL;
    /* Slots are one-based so zero can mean "no tracked instance". */
    if (slot != 0)
    {
        identity = &tracker->identities[slot - 1];
    }
    else
    {
        for (unsigned index = 0; index < RS3_MAX_NPCS; index++)
        {
            if (!tracker->identities[index].in_use)
            {
                identity = &tracker->identities[index];
                tracker->identity_slot_by_runtime_id[runtime_id] = (uint16_t)(index + 1);
                break;
            }
        }
    }
    if (identity == NULL)
    {
        return NULL;
    }
    if (!identity->in_use || identity->actor_address != actor_address)
    {
        memset(identity, 0, sizeof *identity);
        identity->in_use = 1;
        identity->actor_address = actor_address;
        identity->runtime_id = runtime_id;
        identity->instance_id = ++tracker->next_instance_id;
    }
    return identity;
}

/** Retires identities whose runtime ID is no longer listed by the registry. */
static void retire_unlisted_identities(rs3_context *context, const uint32_t *runtime_ids,
                                       uint32_t listed_count)
{
    /* A skipped actor read alone does not mean that the NPC despawned. */
    runtime_id_set listed = {0};
    for (uint32_t index = 0; index < listed_count; index++)
    {
        runtime_id_set_add(&listed, runtime_ids[index]);
    }
    npc_tracker *tracker = &context->npcs;
    for (unsigned index = 0; index < RS3_MAX_NPCS; index++)
    {
        npc_identity *identity = &tracker->identities[index];
        if (identity->in_use && !runtime_id_set_contains(&listed, identity->runtime_id))
        {
            tracker->identity_slot_by_runtime_id[identity->runtime_id] = 0;
            identity->in_use = 0;
        }
    }
}

/** Appends one hit observation to the hit event queue. */
static void publish_hit_event(rs3_context *context, const rs3_snapshot *snapshot,
                              const rs3_npc *npc, const rs3_hit *hit)
{
    uint64_t sequence = ++context->hit_event_sequence;
    rs3_event *event = &context->hit_events[event_ring_index(sequence, HIT_EVENT_CAPACITY)];
    memset(event, 0, sizeof *event);
    event->sequence = sequence;
    event->observed_at_ns = snapshot->info.timestamp_ns;
    event->process_creation_time = context->target.process_creation_time;
    event->pid = context->target.pid;
    event->instance_id = npc->instance_id;
    event->runtime_id = npc->runtime_id;
    event->config_id = npc->config_id;
    event->name_length = npc->name_length;
    event->hit = *hit;
    memcpy(event->name, npc->name, npc->name_length + 1);
}

/** Publishes active hits whose ring slot changed since the last report. */
static void publish_new_hits(rs3_context *context, const rs3_snapshot *snapshot, const rs3_npc *npc,
                             npc_identity *identity)
{
    for (uint32_t index = 0; index < npc->hit_count; index++)
    {
        const rs3_hit *hit = &snapshot->hits[npc->hit_offset + index];
        if (hit->state != RS3_HIT_ACTIVE)
        {
            continue;
        }
        /* Suppress a hit while this instance's ring slot still holds the same record. */
        uint64_t slot_bit = 1ull << hit->slot;
        if ((identity->reported_hit_slots & slot_bit) &&
            memcmp(&identity->last_reported_hits[hit->slot], hit, sizeof *hit) == 0)
        {
            continue;
        }
        identity->last_reported_hits[hit->slot] = *hit;
        identity->reported_hit_slots |= slot_bit;
        publish_hit_event(context, snapshot, npc, hit);
    }
}

void npc_commit_frame(rs3_context *context, rs3_snapshot *snapshot, const uint32_t *runtime_ids,
                      uint32_t listed_count, uint64_t manager_address, uint64_t world_state)
{
    npc_tracker *tracker = &context->npcs;
    if (tracker->registry_manager != manager_address ||
        tracker->registry_world_state != world_state)
    {
        npc_reset_identities(context);
        tracker->registry_manager = manager_address;
        tracker->registry_world_state = world_state;
    }
    retire_unlisted_identities(context, runtime_ids, listed_count);

    AcquireSRWLockExclusive(&context->event_queue_lock);
    for (uint32_t index = 0; index < snapshot->info.npc_count; index++)
    {
        rs3_npc *npc = &snapshot->npcs[index];
        npc_identity *identity =
            track_identity(context, npc->runtime_id, tracker->staged_actor_addresses[index]);
        if (identity == NULL)
        {
            continue;
        }
        npc->instance_id = identity->instance_id;
        snapshot->npc_animations[index].instance_id = npc->instance_id;
        npc_animation_commit_action(context, snapshot, identity, index);
        publish_new_hits(context, snapshot, npc, identity);
    }
    snapshot->info.event_sequence = context->hit_event_sequence;
    ReleaseSRWLockExclusive(&context->event_queue_lock);
}
