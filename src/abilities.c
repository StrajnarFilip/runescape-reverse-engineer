/* Ability definitions, live cooldown timers and equipment requirement checks. */
#include "abilities.h"

#include "decode_utils.h"
#include "inventory.h"
#include "memory.h"
#include "variables.h"

/** Maximum chain length walked in one timer variable bucket. */
#define MAX_TIMER_CHAIN_LENGTH 128u
/** Longest plausible ability cooldown: 24 hours of 20 ms ticks. */
#define MAX_COOLDOWN_TICKS 4320000
#define PAGE_ADDRESS_MASK  (~(uint64_t)(TIMER_PAGE_BYTES - 1))
/** Requirements that need a weapon in the main hand. */
#define WEAPON_REQUIREMENTS ((uint32_t)(RS3_REQ_DUAL_WIELD | RS3_REQ_TWO_HANDED | RS3_REQ_WEAPON))

/** Copies one variable node, serving it from the page cache when possible. */
static int read_variable_node(rs3_context *context, uint64_t address,
                              unsigned char output[VARIABLE_NODE_BYTES])
{
    ability_timer_scratch *timers = &context->inventory->timers;
    uint64_t page = address & PAGE_ADDRESS_MASK;
    size_t offset_in_page = (size_t)(address - page);
    if (is_valid_remote_pointer(page) && offset_in_page <= TIMER_PAGE_BYTES - VARIABLE_NODE_BYTES)
    {
        unsigned cache_slot =
            (unsigned)(((page >> 12) ^ (page >> 20)) & (TIMER_PAGE_CACHE_SLOTS - 1));
        if (timers->page_bases[cache_slot] != page)
        {
            timers->page_bases[cache_slot] = 0;
            if (memory_read(context, page, timers->pages[cache_slot], TIMER_PAGE_BYTES))
            {
                timers->page_bases[cache_slot] = page;
            }
        }
        if (timers->page_bases[cache_slot] == page)
        {
            memcpy(output, timers->pages[cache_slot] + offset_in_page, VARIABLE_NODE_BYTES);
            return 1;
        }
    }
    /* A node that crosses a page boundary, an unreadable page or a sparse test fixture. */
    return memory_read(context, address, output, VARIABLE_NODE_BYTES);
}

/** Marks every variable referenced by a client timer pair as wanted. */
static void select_wanted_variables(rs3_context *context)
{
    const item_catalog *catalog = &context->inventory->catalog;
    ability_timer_scratch *timers = &context->inventory->timers;
    memset(timers->wanted, 0, sizeof timers->wanted);
    memset(timers->present, 0, sizeof timers->present);
    memset(timers->page_bases, 0, sizeof timers->page_bases);
    for (uint32_t index = 0; index < catalog->ability_count; index++)
    {
        catalog_ability_definition definition;
        catalog_get_ability(catalog, index, &definition);
        if (definition.timer_source == RS3_TIMER_CLIENT_PAIR)
        {
            timers->wanted[definition.start_var] = 1;
            timers->wanted[definition.end_var] = 1;
        }
    }
}

/** Walks one bucket chain and records the wanted variables it contains. */
static int collect_bucket(rs3_context *context, uint64_t first_node, uint64_t sentinel,
                          unsigned *selected_count)
{
    ability_timer_scratch *timers = &context->inventory->timers;
    uint64_t node = first_node;
    uint64_t visited[MAX_TIMER_CHAIN_LENGTH];
    unsigned visited_count = 0;
    while (node != 0 && node != sentinel)
    {
        if (visited_count == MAX_TIMER_CHAIN_LENGTH)
        {
            return context_fail(context, RS3_READ_FAILED, "Ability variable chain exceeds bound");
        }
        for (unsigned index = 0; index < visited_count; index++)
        {
            if (visited[index] == node)
            {
                return context_fail(context, RS3_READ_FAILED, "Ability variable chain cycle");
            }
        }
        visited[visited_count++] = node;

        unsigned char node_bytes[VARIABLE_NODE_BYTES];
        if (!read_variable_node(context, node, node_bytes))
        {
            return 0;
        }
        uint32_t variable_id = decode_u32(node_bytes + LAYOUT_VARIABLE_NODE_KEY_OFFSET);
        if (variable_id < VARIABLE_ID_COUNT && timers->wanted[variable_id])
        {
            int64_t value;
            if (!variable_node_value(node_bytes, &value))
            {
                return 0;
            }
            if (*selected_count == MAX_SELECTED_TIMER_VARIABLES || timers->present[variable_id])
            {
                return context_fail(context, RS3_READ_FAILED, "Invalid ability variable selection");
            }
            timers->values[variable_id] = value;
            timers->present[variable_id] = 1;
            timers->selected_addresses[*selected_count] = node;
            timers->selected_ids[*selected_count] = variable_id;
            timers->selected_types[*selected_count] = node_bytes[LAYOUT_VARIABLE_NODE_TYPE_OFFSET];
            (*selected_count)++;
        }
        node = decode_u64(node_bytes + LAYOUT_VARIABLE_NODE_NEXT_OFFSET);
    }
    return 1;
}

/** Re-reads the selected nodes with an empty page cache; changed entries become unknown. */
static int confirm_selected_variables(rs3_context *context, unsigned selected_count)
{
    ability_timer_scratch *timers = &context->inventory->timers;
    memset(timers->page_bases, 0, sizeof timers->page_bases);
    for (unsigned index = 0; index < selected_count; index++)
    {
        unsigned char node_bytes[VARIABLE_NODE_BYTES];
        uint32_t variable_id = timers->selected_ids[index];
        int64_t value = 0;
        if (!read_variable_node(context, timers->selected_addresses[index], node_bytes))
        {
            return 0;
        }
        variable_node_value(node_bytes, &value);
        if (decode_u32(node_bytes + LAYOUT_VARIABLE_NODE_KEY_OFFSET) != variable_id ||
            node_bytes[LAYOUT_VARIABLE_NODE_TYPE_OFFSET] != timers->selected_types[index] ||
            value != timers->values[variable_id])
        {
            timers->present[variable_id] = 0;
        }
    }
    return 1;
}

int abilities_read_timers(rs3_context *context, uint64_t client_address)
{
    select_wanted_variables(context);

    uint64_t header_address = client_address + LAYOUT_CLIENT_VARIABLE_TABLE_OFFSET;
    variable_table table;
    if (!variable_table_open(context, header_address, &table) ||
        !context_reserve_buckets(context, table.bucket_count) ||
        !memory_read(context, table.bucket_array, context->bucket_buffer,
                     (size_t)table.bucket_count * sizeof(uint64_t)))
    {
        return 0;
    }

    unsigned selected_count = 0;
    for (uint64_t bucket = 0; bucket < table.bucket_count; bucket++)
    {
        if (!collect_bucket(context, context->bucket_buffer[bucket], table.sentinel,
                            &selected_count))
        {
            return 0;
        }
    }
    if (!confirm_selected_variables(context, selected_count))
    {
        return 0;
    }
    if (!variable_table_unchanged(context, header_address, &table))
    {
        return context_fail(context, RS3_READ_FAILED, "Ability variable table changed during read");
    }
    return 1;
}

/**
 * Evaluates the sampled main-hand and off-hand items against the weapon
 * requirements of an ability. This is not a complete castability check.
 */
static uint32_t match_equipment(const rs3_snapshot *snapshot, const rs3_ability *ability)
{
    uint32_t both_containers = RS3_INVENTORY_BACKPACK_READ | RS3_INVENTORY_EQUIPMENT_READ;
    if ((snapshot->inventory_info.flags & both_containers) != both_containers)
    {
        return RS3_EQUIPMENT_UNKNOWN;
    }
    const rs3_item *main_hand = NULL;
    const rs3_item *off_hand = NULL;
    for (uint32_t index = 0; index < RS3_MAX_ITEMS; index++)
    {
        const rs3_item *item = &snapshot->items[index];
        if (item->container_id != RS3_CONTAINER_EQUIPMENT)
        {
            continue;
        }
        if (item->slot == EQUIPMENT_SLOT_MAIN_HAND)
        {
            main_hand = item;
        }
        if (item->slot == EQUIPMENT_SLOT_OFF_HAND)
        {
            off_hand = item;
        }
    }
    if (main_hand == NULL || off_hand == NULL)
    {
        return RS3_EQUIPMENT_UNKNOWN;
    }
    if ((main_hand->item_id >= 0 && !(main_hand->flags & RS3_ITEM_METADATA)) ||
        (off_hand->item_id >= 0 && !(off_hand->flags & RS3_ITEM_METADATA)))
    {
        return RS3_EQUIPMENT_UNKNOWN;
    }

    uint32_t requirements = ability->requirements;
    int main_hand_weapon = (main_hand->equipment_flags & RS3_WEAPON) != 0;
    int off_hand_weapon = (off_hand->equipment_flags & RS3_WEAPON) != 0;
    int style_mismatch = main_hand->combat_style != ability->combat_style;

    if ((requirements & RS3_REQ_DUAL_WIELD) && (!main_hand_weapon || !off_hand_weapon ||
                                                main_hand->combat_style != off_hand->combat_style))
    {
        return RS3_EQUIPMENT_BLOCKED;
    }
    if ((requirements & RS3_REQ_TWO_HANDED) && !(main_hand->equipment_flags & RS3_TWO_HANDED))
    {
        return RS3_EQUIPMENT_BLOCKED;
    }
    if ((requirements & RS3_REQ_WEAPON) && !main_hand_weapon)
    {
        return RS3_EQUIPMENT_BLOCKED;
    }
    if (ability->combat_style != RS3_STYLE_NONE && !main_hand_weapon &&
        !(requirements & WEAPON_REQUIREMENTS))
    {
        return RS3_EQUIPMENT_UNKNOWN;
    }
    if (ability->combat_style != RS3_STYLE_NONE && main_hand_weapon && style_mismatch)
    {
        return RS3_EQUIPMENT_BLOCKED;
    }
    if ((requirements & WEAPON_REQUIREMENTS) && ability->combat_style != RS3_STYLE_NONE &&
        style_mismatch)
    {
        return RS3_EQUIPMENT_BLOCKED;
    }
    uint32_t worn_flags = main_hand->equipment_flags | off_hand->equipment_flags;
    if ((requirements & RS3_REQ_SHIELD) && !(worn_flags & RS3_SHIELD))
    {
        /*
         * Bone Shield can substitute for some defensives, but not all. Do not
         * claim blocked or castable until the ability exception and rune check
         * are mapped.
         */
        if (!(snapshot->inventory_info.flags & RS3_INVENTORY_BONE_SHIELD_KNOWN) ||
            snapshot->inventory_info.bone_shield_tier != 0)
        {
            return RS3_EQUIPMENT_UNKNOWN;
        }
        return RS3_EQUIPMENT_BLOCKED;
    }
    return RS3_EQUIPMENT_ALLOWED;
}

/** Fills the live cooldown fields of an ability from the staged timer variables. */
static void decode_cooldown(const ability_timer_scratch *timers, uint32_t logic_tick,
                            int timers_read, rs3_ability *ability)
{
    /*
     * A mapped timer can still be absent from the sparse table. Only a valid
     * expired interval yields a zero countdown.
     */
    ability->reason =
        ability->timer_source != RS3_TIMER_UNMAPPED ? RS3_NOT_SET : RS3_UNMAPPED_TIMER;
    if (ability->timer_source == RS3_TIMER_UNMAPPED)
    {
        return;
    }
    if (!timers_read)
    {
        ability->reason = RS3_READ_FAILED;
        return;
    }
    if (!timers->present[ability->start_var] || !timers->present[ability->end_var])
    {
        return;
    }
    ability->start_tick = (uint32_t)timers->values[ability->start_var];
    ability->end_tick = (uint32_t)timers->values[ability->end_var];
    int32_t duration_ticks = tick_difference(ability->end_tick, ability->start_tick);
    int32_t elapsed_ticks = tick_difference(logic_tick, ability->start_tick);
    ability->reason = RS3_INCONSISTENT_CLOCK;
    if (duration_ticks >= 0 && duration_ticks <= MAX_COOLDOWN_TICKS && elapsed_ticks >= 0)
    {
        int32_t remaining_ticks =
            elapsed_ticks < duration_ticks ? duration_ticks - elapsed_ticks : 0;
        ability->flags |= RS3_ABILITY_TIMER_VALID;
        ability->reason = RS3_REASON_NONE;
        ability->duration_ms = duration_ticks * (int32_t)LAYOUT_LOGIC_TICK_MS;
        ability->remaining_ms = remaining_ticks * (int32_t)LAYOUT_LOGIC_TICK_MS;
    }
}

void abilities_stage(rs3_context *context, rs3_snapshot *snapshot, int timers_read)
{
    const item_catalog *catalog = &context->inventory->catalog;
    const ability_timer_scratch *timers = &context->inventory->timers;
    rs3_inventory_info *info = &snapshot->inventory_info;
    for (uint32_t index = 0; index < catalog->ability_count; index++)
    {
        catalog_ability_definition definition;
        catalog_get_ability(catalog, index, &definition);

        rs3_ability ability = {0};
        ability.struct_id = definition.struct_id;
        ability.start_var = definition.start_var;
        ability.end_var = definition.end_var;
        ability.base_cooldown_ms = definition.base_cooldown_ms;
        ability.requirements = definition.requirements;
        ability.combat_style = definition.combat_style;
        ability.timer_source = definition.timer_source;
        ability.level_requirement = definition.level_requirement;
        ability.adrenaline_requirement_raw = definition.adrenaline_requirement_raw;
        if (definition.definition_flags & CATALOG_ABILITY_IN_BOOK)
        {
            ability.flags |= RS3_ABILITY_IN_BOOK;
        }
        if (definition.definition_flags & CATALOG_ABILITY_BASE_COOLDOWN_KNOWN)
        {
            ability.flags |= RS3_ABILITY_BASE_COOLDOWN_KNOWN;
        }
        catalog_copy_name(catalog, definition.name_offset, ability.name);
        decode_cooldown(timers, snapshot->info.logic_tick, timers_read, &ability);
        ability.equipment_match = match_equipment(snapshot, &ability);
        snapshot->abilities[info->ability_count++] = ability;
    }
}
