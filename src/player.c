/* Local player statistics, variables, global cooldown and change tracking. */
#include "player.h"

#include "decode_utils.h"
#include "memory.h"
#include "variables.h"

/** Index of the Prayer skill in the client's skill array. */
#define PRAYER_SKILL_INDEX        5u
#define MIN_SKILL_LEVEL           1
#define MAX_SKILL_LEVEL           255
#define MAX_LIFE_POINTS           1000000
#define MAX_ADRENALINE_TENTHS     10000
#define MAX_GLOBAL_COOLDOWN_TICKS 10000

static int variable_is_valid(const rs3_player *player, unsigned variable)
{
    return (player->valid_variables & (1ull << variable)) != 0;
}

/** The client-clock global cooldown pair lives in VarClient; the rest in VarPlayer. */
static int variable_is_in_client_domain(unsigned variable)
{
    return variable == RS3_GCD_CLIENT_START || variable == RS3_GCD_CLIENT_END;
}

int player_decode_statistics(rs3_context *context, uint64_t client_address, rs3_player *player)
{
    uint64_t client_vtable;
    if (!memory_read_pointer(context, client_address, &client_vtable))
    {
        return 0;
    }
    if (client_vtable != context->target.module_base + context->target.profile->client_vtable_rva)
    {
        return context_fail(context, RS3_UNSUPPORTED_CLIENT, "Client context profile mismatch");
    }

    uint64_t skills_manager;
    if (!memory_read_pointer(context, client_address + LAYOUT_SKILLS_MANAGER_OFFSET,
                             &skills_manager))
    {
        return 0;
    }
    if (skills_manager == 0)
    {
        return context_fail(context, RS3_READ_FAILED, "Player statistics unavailable");
    }
    uint64_t skill_entries;
    if (!memory_read_pointer(context, skills_manager + LAYOUT_SKILLS_ENTRIES_OFFSET,
                             &skill_entries))
    {
        return 0;
    }
    if (skill_entries == 0)
    {
        return context_fail(context, RS3_READ_FAILED, "Player statistics unavailable");
    }

    uint64_t prayer_level_address =
        skill_entries + PRAYER_SKILL_INDEX * LAYOUT_SKILL_STRIDE + LAYOUT_SKILL_BASE_LEVEL_OFFSET;
    if (!memory_read(context, prayer_level_address, &player->prayer_base_level,
                     sizeof player->prayer_base_level))
    {
        return 0;
    }
    if (player->prayer_base_level < MIN_SKILL_LEVEL || player->prayer_base_level > MAX_SKILL_LEVEL)
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid prayer base level");
    }
    return 1;
}

int player_read_variables(rs3_context *context, uint64_t player_address, uint64_t client_address,
                          rs3_player *player)
{
    variable_table player_table;
    variable_table client_table;
    if (!variable_table_open(context, player_address + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET,
                             &player_table))
    {
        return 0;
    }
    /* Read the player-domain variables before opening the client table. */
    for (unsigned pass = 0; pass < 2; pass++)
    {
        int client_pass = pass == 1;
        if (client_pass &&
            !variable_table_open(context, client_address + LAYOUT_CLIENT_VARIABLE_TABLE_OFFSET,
                                 &client_table))
        {
            return 0;
        }
        const variable_table *table = client_pass ? &client_table : &player_table;
        for (unsigned variable = 0; variable < RS3_VARIABLE_COUNT; variable++)
        {
            if (variable_is_in_client_domain(variable) != client_pass)
            {
                continue;
            }
            int present;
            if (!variable_table_find(context, table, profile_variable_ids[variable],
                                     &player->raw_variables[variable], &present))
            {
                return 0;
            }
            if (present)
            {
                player->valid_variables |= 1ull << variable;
            }
        }
    }
    return 1;
}

int player_check_plausibility(rs3_context *context, const rs3_player *player)
{
    if (variable_is_valid(player, RS3_HEALTH))
    {
        int64_t life_points = player->raw_variables[RS3_HEALTH];
        if (life_points < 0 || life_points > MAX_LIFE_POINTS)
        {
            return context_fail(context, RS3_READ_FAILED, "Implausible player life points");
        }
    }
    if (variable_is_valid(player, RS3_ADRENALINE))
    {
        int64_t adrenaline = player->raw_variables[RS3_ADRENALINE];
        if (adrenaline < 0 || adrenaline > MAX_ADRENALINE_TENTHS)
        {
            return context_fail(context, RS3_READ_FAILED, "Implausible adrenaline");
        }
    }
    return 1;
}

void player_decode_global_cooldown(rs3_player *player, uint32_t logic_tick)
{
    player->gcd_reason = RS3_NOT_SET;
    if (!variable_is_valid(player, RS3_GCD_CLIENT_START) ||
        !variable_is_valid(player, RS3_GCD_CLIENT_END))
    {
        return;
    }
    uint32_t start_tick = (uint32_t)player->raw_variables[RS3_GCD_CLIENT_START];
    uint32_t end_tick = (uint32_t)player->raw_variables[RS3_GCD_CLIENT_END];
    int32_t duration_ticks = tick_difference(end_tick, start_tick);
    int32_t elapsed_ticks = tick_difference(logic_tick, start_tick);
    if (duration_ticks < 0 || duration_ticks > MAX_GLOBAL_COOLDOWN_TICKS || elapsed_ticks < 0)
    {
        player->gcd_reason = RS3_INCONSISTENT_CLOCK;
        return;
    }
    int32_t remaining_ticks = elapsed_ticks < duration_ticks ? duration_ticks - elapsed_ticks : 0;
    player->gcd_reason = RS3_REASON_NONE;
    player->gcd_duration_ms = duration_ticks * (int32_t)LAYOUT_LOGIC_TICK_MS;
    player->gcd_remaining_ms = remaining_ticks * (int32_t)LAYOUT_LOGIC_TICK_MS;
}

void player_track_changes(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    player_change_tracker *tracker = &context->player_changes;
    rs3_player *player = &snapshot->player;
    uint64_t now_ns = snapshot->info.monotonic_ns;

    if (tracker->clock_world_state != world_state ||
        tracker->last_logic_tick != snapshot->info.logic_tick)
    {
        tracker->clock_world_state = world_state;
        tracker->last_logic_tick = snapshot->info.logic_tick;
        tracker->clock_changed_ns = now_ns;
    }
    player->clock_stalled_ns = now_ns - tracker->clock_changed_ns;

    static const unsigned tracked_variables[TRACKED_PLAYER_VALUE_COUNT] = {
        RS3_HEALTH, RS3_PRAYER_PACKED, RS3_ADRENALINE};
    uint64_t *change_times[TRACKED_PLAYER_VALUE_COUNT] = {
        &player->hp_changed_ns, &player->prayer_changed_ns, &player->adrenaline_changed_ns};

    for (unsigned tracked = 0; tracked < TRACKED_PLAYER_VALUE_COUNT; tracked++)
    {
        unsigned variable = tracked_variables[tracked];
        uint32_t tracked_bit = 1u << tracked;
        if (!variable_is_valid(player, variable))
        {
            tracker->valid_mask &= ~tracked_bit;
            continue;
        }
        /* Compare prayer points only; the packed variable carries other bits. */
        int64_t value = player->raw_variables[variable];
        if (variable == RS3_PRAYER_PACKED)
        {
            value &= PLAYER_PRAYER_POINTS_MASK;
        }
        if (!(tracker->valid_mask & tracked_bit) || value != tracker->values[tracked])
        {
            tracker->changed_ns[tracked] = now_ns;
        }
        tracker->values[tracked] = value;
        tracker->valid_mask |= tracked_bit;
        *change_times[tracked] = tracker->changed_ns[tracked];
    }
}
