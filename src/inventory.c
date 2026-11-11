/* Backpack and equipment slots, inferred item identities and item events. */
#include "inventory.h"

#include <stdio.h>
#include <stdlib.h>

#include "abilities.h"
#include "decode_utils.h"
#include "event_ring.h"
#include "memory.h"
#include "variables.h"

/* Inventory manager: a registry vector (begin, end, capacity) at this offset. */
#define INVENTORY_MANAGER_REGISTRY_OFFSET 8u
#define INVENTORY_REGISTRY_VECTOR_BYTES   24u

/*
 * Registry entry layout. The key encodes the container ID in the upper bits
 * and a flag in bit zero; the ordinary backpack and equipment entries have
 * that flag clear.
 */
#define REGISTRY_ENTRY_KEY_OFFSET            0u
#define REGISTRY_ENTRY_CONTAINER_ID_OFFSET   16u
#define REGISTRY_ENTRY_SLOTS_BEGIN_OFFSET    24u
#define REGISTRY_ENTRY_SLOTS_END_OFFSET      32u
#define REGISTRY_ENTRY_SLOTS_CAPACITY_OFFSET 40u
#define REGISTRY_KEY_BACKPACK                (RS3_CONTAINER_BACKPACK * 2u)
#define REGISTRY_KEY_EQUIPMENT               (RS3_CONTAINER_EQUIPMENT * 2u)

/* Slot record: item ID and quantity. */
#define SLOT_RECORD_BYTES    8u
#define SLOT_ITEM_ID_OFFSET  0u
#define SLOT_QUANTITY_OFFSET 4u
#define EMPTY_ITEM_ID        (-1)
#define MAX_ITEM_ID          1000000

#define BONE_SHIELD_TIER_MASK 3u
#define NO_MATCH              (-1)

inventory_state *inventory_create(void)
{
    return calloc(1, sizeof(inventory_state));
}

void inventory_destroy(inventory_state *inventory)
{
    if (inventory != NULL)
    {
        catalog_free(&inventory->catalog);
        free(inventory);
    }
}

void inventory_reset_tracking(inventory_state *inventory)
{
    if (inventory != NULL)
    {
        inventory->items.has_previous_items = 0;
        inventory->items.world_state = 0;
        inventory->items.manager_address = 0;
    }
}

/** Fills the catalog-derived fields of an occupied slot. */
static void describe_item(const item_catalog *catalog, rs3_item *item)
{
    item->doses = -1;
    item->next_dose_item_id = -1;
    item->equip_slot = -1;
    if (item->item_id < 0)
    {
        return;
    }
    catalog_item_definition definition;
    if (!catalog_find_item(catalog, item->item_id, &definition))
    {
        return;
    }
    item->flags |= RS3_ITEM_METADATA;
    item->family_id = definition.family_id;
    item->doses = definition.doses;
    item->next_dose_item_id = definition.next_dose_item_id;
    item->equip_slot = definition.equip_slot;
    item->combat_style = definition.combat_style;
    item->equipment_flags = definition.equipment_flags;
    item->effect_flags = definition.effect_flags;
    item->base_heal = definition.base_heal;
    catalog_copy_name(catalog, definition.name_offset, item->name);
}

/**
 * Reads one container's slot vector and appends every slot, including empty
 * ones, to the snapshot. Slots past the vector end are empty, as in the client.
 */
static int read_container(rs3_context *context, rs3_snapshot *snapshot, const unsigned char *entry,
                          uint32_t container_id, uint32_t *item_count)
{
    uint32_t slot_count =
        container_id == RS3_CONTAINER_BACKPACK ? BACKPACK_SLOT_COUNT : EQUIPMENT_SLOT_COUNT;
    uint64_t slots_begin = decode_u64(entry + REGISTRY_ENTRY_SLOTS_BEGIN_OFFSET);
    uint64_t slots_end = decode_u64(entry + REGISTRY_ENTRY_SLOTS_END_OFFSET);
    uint64_t slots_capacity = decode_u64(entry + REGISTRY_ENTRY_SLOTS_CAPACITY_OFFSET);
    int vector_is_valid =
        decode_u32(entry + REGISTRY_ENTRY_CONTAINER_ID_OFFSET) == container_id &&
        slots_end >= slots_begin && slots_capacity >= slots_end &&
        (slots_end - slots_begin) % SLOT_RECORD_BYTES == 0 &&
        slots_end - slots_begin <= (uint64_t)slot_count * SLOT_RECORD_BYTES &&
        (slots_begin != 0 ? is_valid_remote_pointer(slots_begin) : slots_capacity == 0);
    if (!vector_is_valid)
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid inventory slot vector");
    }

    size_t slot_bytes = (size_t)(slots_end - slots_begin);
    unsigned char slots[BACKPACK_SLOT_COUNT * SLOT_RECORD_BYTES];
    unsigned char slots_again[BACKPACK_SLOT_COUNT * SLOT_RECORD_BYTES];
    if (slot_bytes != 0 && (!memory_read(context, slots_begin, slots, slot_bytes) ||
                            !memory_read(context, slots_begin, slots_again, slot_bytes) ||
                            memcmp(slots, slots_again, slot_bytes) != 0))
    {
        return context_fail(context, RS3_READ_FAILED, "Inventory slots changed during read");
    }

    const item_catalog *catalog = &context->inventory->catalog;
    uint32_t stored_slots = (uint32_t)(slot_bytes / SLOT_RECORD_BYTES);
    for (uint32_t slot = 0; slot < slot_count; slot++)
    {
        rs3_item item = {0};
        item.container_id = container_id;
        item.slot = slot;
        item.item_id = EMPTY_ITEM_ID;
        if (slot < stored_slots)
        {
            const unsigned char *record = slots + slot * SLOT_RECORD_BYTES;
            item.item_id = decode_i32(record + SLOT_ITEM_ID_OFFSET);
            item.quantity = decode_u32(record + SLOT_QUANTITY_OFFSET);
        }
        if (item.item_id < EMPTY_ITEM_ID || item.item_id > MAX_ITEM_ID ||
            (item.item_id == EMPTY_ITEM_ID && item.quantity != 0))
        {
            return context_fail(context, RS3_READ_FAILED, "Invalid inventory item record");
        }
        describe_item(catalog, &item);
        snapshot->items[(*item_count)++] = item;
    }
    return 1;
}

/** Reads the backpack and equipment containers from the inventory registry. */
static int read_inventory_slots(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    inventory_state *inventory = context->inventory;
    uint64_t manager;
    if (!memory_read_pointer(context, world_state + LAYOUT_INVENTORY_MANAGER_OFFSET, &manager) ||
        manager == 0)
    {
        return context_fail(context, RS3_READ_FAILED, "Inventory manager unavailable");
    }

    unsigned char registry_vector[INVENTORY_REGISTRY_VECTOR_BYTES];
    if (!memory_read(context, manager + INVENTORY_MANAGER_REGISTRY_OFFSET, registry_vector,
                     sizeof registry_vector))
    {
        return 0;
    }
    uint64_t entries_begin = decode_u64(registry_vector);
    uint64_t entries_end = decode_u64(registry_vector + 8);
    uint64_t entries_capacity = decode_u64(registry_vector + 16);
    if (entries_end < entries_begin || entries_capacity < entries_end ||
        (entries_end - entries_begin) % INVENTORY_REGISTRY_ENTRY_BYTES != 0 ||
        entries_end - entries_begin > sizeof inventory->registry_entries ||
        (entries_begin != 0 && !is_valid_remote_pointer(entries_begin)))
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid inventory registry vector");
    }

    size_t registry_bytes = (size_t)(entries_end - entries_begin);
    if (registry_bytes != 0 &&
        !memory_read(context, entries_begin, inventory->registry_entries, registry_bytes))
    {
        return 0;
    }

    uint32_t found_containers = 0;
    uint32_t item_count = 0;
    uint32_t previous_key = 0;
    for (size_t offset = 0; offset < registry_bytes; offset += INVENTORY_REGISTRY_ENTRY_BYTES)
    {
        const unsigned char *entry = inventory->registry_entries + offset;
        uint32_t key = decode_u32(entry + REGISTRY_ENTRY_KEY_OFFSET);
        if (offset != 0 && key <= previous_key)
        {
            return context_fail(context, RS3_READ_FAILED, "Unsorted inventory registry");
        }
        previous_key = key;
        if (key != REGISTRY_KEY_BACKPACK && key != REGISTRY_KEY_EQUIPMENT)
        {
            continue;
        }
        uint32_t container_id = key / 2;
        if (!read_container(context, snapshot, entry, container_id, &item_count))
        {
            return 0;
        }
        found_containers |= container_id == RS3_CONTAINER_BACKPACK ? RS3_INVENTORY_BACKPACK_READ
                                                                   : RS3_INVENTORY_EQUIPMENT_READ;
    }

    /* The registry and its manager must still match after reading both slot vectors. */
    unsigned char registry_vector_after[INVENTORY_REGISTRY_VECTOR_BYTES];
    if (!memory_read(context, manager + INVENTORY_MANAGER_REGISTRY_OFFSET, registry_vector_after,
                     sizeof registry_vector_after) ||
        memcmp(registry_vector, registry_vector_after, sizeof registry_vector) != 0 ||
        (registry_bytes != 0 &&
         (!memory_read(context, entries_begin, inventory->registry_entries_check, registry_bytes) ||
          memcmp(inventory->registry_entries, inventory->registry_entries_check, registry_bytes) !=
              0)))
    {
        return context_fail(context, RS3_READ_FAILED, "Inventory registry changed during read");
    }
    uint64_t manager_after;
    if (!memory_read_pointer(context, world_state + LAYOUT_INVENTORY_MANAGER_OFFSET,
                             &manager_after) ||
        manager_after != manager)
    {
        return context_fail(context, RS3_READ_FAILED, "Inventory manager changed during read");
    }
    uint32_t both_containers = RS3_INVENTORY_BACKPACK_READ | RS3_INVENTORY_EQUIPMENT_READ;
    if (found_containers != both_containers)
    {
        return context_fail(context, RS3_READ_FAILED,
                            "Backpack or equipment container not transmitted");
    }

    snapshot->inventory_info.flags |= found_containers;
    snapshot->inventory_info.backpack_count = BACKPACK_SLOT_COUNT;
    snapshot->inventory_info.equipment_count = EQUIPMENT_SLOT_COUNT;
    inventory->items.staged_manager_address = manager;
    return 1;
}

/** Reads the Bone Shield tier from its player variable, when present. */
static void read_bone_shield_tier(rs3_context *context, rs3_inventory_info *info,
                                  uint64_t player_address)
{
    variable_table table;
    int64_t bone_shield_value;
    int present;
    if (variable_table_open(context, player_address + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET,
                            &table) &&
        variable_table_find(context, &table, LAYOUT_INVENTORY_BONE_SHIELD_VARP, &bone_shield_value,
                            &present) &&
        present)
    {
        info->bone_shield_tier = ((uint32_t)bone_shield_value >> LAYOUT_INVENTORY_BONE_SHIELD_LSB) &
                                 BONE_SHIELD_TIER_MASK;
        info->flags |= RS3_INVENTORY_BONE_SHIELD_KNOWN;
    }
}

void inventory_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t client_address, uint64_t world_state)
{
    const item_catalog *catalog = &context->inventory->catalog;
    rs3_inventory_info *info = &snapshot->inventory_info;
    info->catalog_status = catalog->status;
    info->status = RS3_AVAILABLE;
    if (catalog->status != RS3_AVAILABLE)
    {
        info->reason = RS3_CATALOG_UNAVAILABLE;
        snprintf(info->message, sizeof info->message, "%s", catalog->error_message);
    }
    else
    {
        info->flags |= RS3_INVENTORY_CATALOG_LOADED;
    }

    if (!read_inventory_slots(context, snapshot, world_state))
    {
        info->status = RS3_UNAVAILABLE;
        info->reason = RS3_READ_FAILED;
        info->backpack_count = 0;
        info->equipment_count = 0;
        info->flags &= ~(uint32_t)(RS3_INVENTORY_BACKPACK_READ | RS3_INVENTORY_EQUIPMENT_READ);
        snprintf(info->message, sizeof info->message, "%s", context->failure_message);
    }
    read_bone_shield_tier(context, info, player_address);

    int timers_read = catalog->ability_count != 0 && abilities_read_timers(context, client_address);
    if (timers_read)
    {
        info->flags |= RS3_INVENTORY_TIMERS_READ;
    }
    abilities_stage(context, snapshot, timers_read);
}

int inventory_same_item(const rs3_item *before, const rs3_item *after, int allow_dose_change)
{
    if (before->item_id < 0 || after->item_id < 0 || before->quantity != after->quantity)
    {
        return 0;
    }
    if (before->item_id == after->item_id)
    {
        return 1;
    }
    return allow_dose_change && before->family_id == after->family_id && before->doses > 0 &&
           after->doses == before->doses - 1 && before->next_dose_item_id == after->item_id;
}

void inventory_publish_event(rs3_context *context, const rs3_snapshot *snapshot, uint32_t kind,
                             const rs3_item *before, const rs3_item *after)
{
    item_tracker *tracker = &context->inventory->items;
    uint64_t sequence = ++tracker->event_sequence;
    rs3_item_event *event = &tracker->events[event_ring_index(sequence, ITEM_EVENT_CAPACITY)];
    memset(event, 0, sizeof *event);
    event->before.item_id = EMPTY_ITEM_ID;
    event->after.item_id = EMPTY_ITEM_ID;
    event->before.doses = -1;
    event->after.doses = -1;
    event->sequence = sequence;
    event->observed_at_ns = snapshot->info.timestamp_ns;
    event->process_creation_time = context->target.process_creation_time;
    event->pid = context->target.pid;
    event->kind = kind;
    if (before != NULL)
    {
        event->before = *before;
    }
    if (after != NULL)
    {
        event->after = *after;
    }
    event->flags = (before != NULL ? before->flags : 0) | (after != NULL ? after->flags : 0);
}

/**
 * Matches every current slot to a previous slot.
 *
 * Prefer a stable exact slot, then uniquely matching IDs across both
 * containers, then an explicitly defined next-dose variant. matched[i]
 * receives the previous index of current slot i, or NO_MATCH; used[j] marks a
 * previous slot that was matched so it cannot be matched or removed twice.
 */
static void match_items(rs3_snapshot *snapshot, const rs3_item *previous_items,
                        int matched[RS3_MAX_ITEMS], int used[RS3_MAX_ITEMS])
{
    rs3_item *current_items = snapshot->items;
    for (unsigned current = 0; current < RS3_MAX_ITEMS; current++)
    {
        if (inventory_same_item(&current_items[current], &previous_items[current], 0))
        {
            matched[current] = (int)current;
            used[current] = 1;
        }
    }

    for (int allow_dose_change = 0; allow_dose_change <= 1; allow_dose_change++)
    {
        for (unsigned current = 0; current < RS3_MAX_ITEMS; current++)
        {
            if (matched[current] != NO_MATCH || current_items[current].item_id < 0)
            {
                continue;
            }
            unsigned candidate_count = 0;
            unsigned candidate = 0;
            for (unsigned previous = 0; previous < RS3_MAX_ITEMS; previous++)
            {
                if (!used[previous] &&
                    inventory_same_item(&previous_items[previous], &current_items[current],
                                        allow_dose_change))
                {
                    candidate_count++;
                    candidate = previous;
                }
            }
            unsigned target_count = 0;
            if (candidate_count == 1)
            {
                for (unsigned other = 0; other < RS3_MAX_ITEMS; other++)
                {
                    if (matched[other] == NO_MATCH &&
                        inventory_same_item(&previous_items[candidate], &current_items[other],
                                            allow_dose_change))
                    {
                        target_count++;
                    }
                }
            }
            /* Preserve the token only when the candidate is unique in both directions. */
            if (candidate_count == 1 && target_count == 1)
            {
                matched[current] = (int)candidate;
                used[candidate] = 1;
            }
            else if (candidate_count != 0 || target_count != 0)
            {
                current_items[current].flags |= RS3_ITEM_AMBIGUOUS_IDENTITY;
            }
        }
    }

    /* Give a same-slot quantity change a chance after resolving moves and doses. */
    for (unsigned current = 0; current < RS3_MAX_ITEMS; current++)
    {
        if (matched[current] == NO_MATCH && !used[current] && current_items[current].item_id >= 0 &&
            current_items[current].item_id == previous_items[current].item_id)
        {
            matched[current] = (int)current;
            used[current] = 1;
        }
    }
}

/** Classifies how a matched or unmatched current slot changed and publishes it. */
static void publish_slot_change(rs3_context *context, const rs3_snapshot *snapshot,
                                const rs3_item *previous_items, const rs3_item *current_item,
                                unsigned slot_index, const rs3_item *matched_previous,
                                int used[RS3_MAX_ITEMS])
{
    const rs3_item *same_slot_previous = &previous_items[slot_index];
    if (matched_previous == NULL && !used[slot_index] && same_slot_previous->item_id >= 0)
    {
        inventory_publish_event(context, snapshot, RS3_ITEM_CHANGED, same_slot_previous,
                                current_item);
        used[slot_index] = 1;
    }
    else if (matched_previous == NULL)
    {
        inventory_publish_event(context, snapshot, RS3_ITEM_ADDED, NULL, current_item);
    }
    else if (matched_previous->item_id != current_item->item_id)
    {
        inventory_publish_event(context, snapshot, RS3_ITEM_DOSES_CHANGED, matched_previous,
                                current_item);
    }
    else if (matched_previous->quantity != current_item->quantity)
    {
        inventory_publish_event(context, snapshot, RS3_ITEM_QUANTITY_CHANGED, matched_previous,
                                current_item);
    }
    else if (matched_previous->container_id != current_item->container_id ||
             matched_previous->slot != current_item->slot)
    {
        inventory_publish_event(context, snapshot, RS3_ITEM_MOVED, matched_previous, current_item);
    }
}

void inventory_commit(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    uint32_t both_containers = RS3_INVENTORY_BACKPACK_READ | RS3_INVENTORY_EQUIPMENT_READ;
    if ((snapshot->inventory_info.flags & both_containers) != both_containers)
    {
        return;
    }
    item_tracker *tracker = &context->inventory->items;
    if (tracker->world_state != world_state ||
        tracker->manager_address != tracker->staged_manager_address)
    {
        inventory_reset_tracking(context->inventory);
        tracker->world_state = world_state;
        tracker->manager_address = tracker->staged_manager_address;
    }

    int matched[RS3_MAX_ITEMS];
    int used[RS3_MAX_ITEMS] = {0};
    for (unsigned index = 0; index < RS3_MAX_ITEMS; index++)
    {
        matched[index] = NO_MATCH;
    }
    if (tracker->has_previous_items)
    {
        match_items(snapshot, tracker->previous_items, matched, used);
    }

    AcquireSRWLockExclusive(&context->event_queue_lock);
    for (unsigned index = 0; index < RS3_MAX_ITEMS; index++)
    {
        rs3_item *current_item = &snapshot->items[index];
        const rs3_item *matched_previous =
            matched[index] != NO_MATCH ? &tracker->previous_items[matched[index]] : NULL;
        if (current_item->item_id < 0)
        {
            continue;
        }
        current_item->token =
            matched_previous != NULL ? matched_previous->token : ++tracker->last_token;
        /* The first observation is a baseline, not a bag full of "new" items. */
        if (tracker->has_previous_items)
        {
            publish_slot_change(context, snapshot, tracker->previous_items, current_item, index,
                                matched_previous, used);
        }
    }
    if (tracker->has_previous_items)
    {
        for (unsigned index = 0; index < RS3_MAX_ITEMS; index++)
        {
            if (!used[index] && tracker->previous_items[index].item_id >= 0)
            {
                inventory_publish_event(context, snapshot, RS3_ITEM_REMOVED,
                                        &tracker->previous_items[index], NULL);
            }
        }
    }
    memcpy(tracker->previous_items, snapshot->items, sizeof tracker->previous_items);
    tracker->has_previous_items = 1;
    snapshot->inventory_info.change_sequence = tracker->event_sequence;
    ReleaseSRWLockExclusive(&context->event_queue_lock);
}
