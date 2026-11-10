/*
 * Overhead prayer flags from locally decoded VarBit definitions, not pixels,
 * and the scene epoch that scopes combat observations.
 */
#include "combat.h"

#include <string.h>

#include "memory.h"
#include "variables.h"

_Static_assert(sizeof(rs3_npc_action_event) == 80, "ABI NPC action event layout");

/** Raw prayer variables in rs3_combat_state order. */
enum prayer_variable_index
{
    PRAYER_VARIABLE_NORMAL,
    PRAYER_VARIABLE_CURSES,
    PRAYER_VARIABLE_NORMAL_NECROMANCY,
    PRAYER_VARIABLE_CURSES_NECROMANCY,
    PRAYER_VARIABLE_COUNT
};

static const uint32_t prayer_variable_ids[PRAYER_VARIABLE_COUNT] = {3272, 3275, 3273, 5859};

#define PRAYER_VALID_NORMAL            (1u << PRAYER_VARIABLE_NORMAL)
#define PRAYER_VALID_CURSES            (1u << PRAYER_VARIABLE_CURSES)
#define PRAYER_VALID_NORMAL_NECROMANCY (1u << PRAYER_VARIABLE_NORMAL_NECROMANCY)
#define PRAYER_VALID_CURSES_NECROMANCY (1u << PRAYER_VARIABLE_CURSES_NECROMANCY)
#define PRAYER_VALID_BOTH_BOOKS        (PRAYER_VALID_NORMAL | PRAYER_VALID_CURSES)
#define PRAYER_VALID_BOTH_NECROMANCY_BOOKS                                                         \
    (PRAYER_VALID_NORMAL_NECROMANCY | PRAYER_VALID_CURSES_NECROMANCY)

/* Active-flag bits inside the raw prayer variables. */
#define NORMAL_PROTECT_MAGIC_BIT      (1u << 6)
#define NORMAL_PROTECT_RANGED_BIT     (1u << 7)
#define NORMAL_PROTECT_MELEE_BIT      (1u << 8)
#define CURSES_DEFLECT_MAGIC_BIT      (1u << 7)
#define CURSES_DEFLECT_RANGED_BIT     (1u << 8)
#define CURSES_DEFLECT_MELEE_BIT      (1u << 9)
#define CURSES_SOUL_SPLIT_BIT         (1u << 18)
#define NORMAL_PROTECT_NECROMANCY_BIT (1u << 3)
#define CURSES_DEFLECT_NECROMANCY_BIT (1u << 17)

uint32_t rs3_combat_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_COMBAT_SIZE_STATE:
            return sizeof(rs3_combat_state);
        case RS3_COMBAT_SIZE_ANIMATION:
            return sizeof(rs3_npc_animation);
        case RS3_COMBAT_SIZE_ACTION_EVENT:
            return sizeof(rs3_npc_action_event);
        default:
            return 0;
    }
}

uint32_t rs3_combat_active_prayer(const rs3_combat_state *state)
{
    if (state == NULL || state->status == RS3_UNAVAILABLE || state->status == RS3_NO_SAMPLE)
    {
        return RS3_PRAYER_UNKNOWN;
    }
    uint32_t active_overheads = state->active_mask & RS3_PRAYER_OVERHEADS;
    int exactly_one_active =
        active_overheads != 0 && (active_overheads & (active_overheads - 1)) == 0;
    if (exactly_one_active && (state->known_mask & active_overheads) != 0)
    {
        for (uint32_t prayer = RS3_PRAYER_MELEE; prayer <= RS3_PRAYER_NECROMANCY; prayer++)
        {
            if (active_overheads == RS3_PRAYER_BIT(prayer))
            {
                return prayer;
            }
        }
    }
    if (active_overheads == 0 && (state->known_mask & RS3_PRAYER_OVERHEADS) == RS3_PRAYER_OVERHEADS)
    {
        return RS3_PRAYER_NONE;
    }
    return RS3_PRAYER_UNKNOWN;
}

/** Reads the four raw prayer variables; *valid_mask receives one bit per present value. */
static int read_prayer_variables(rs3_context *context, uint64_t player_address,
                                 int32_t raw_values[PRAYER_VARIABLE_COUNT], uint32_t *valid_mask)
{
    *valid_mask = 0;
    uint64_t header_address = player_address + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET;
    variable_table table;
    if (!variable_table_open(context, header_address, &table))
    {
        return 0;
    }
    for (uint32_t index = 0; index < PRAYER_VARIABLE_COUNT; index++)
    {
        int present;
        int64_t value = 0;
        if (!variable_table_find(context, &table, prayer_variable_ids[index], &value, &present))
        {
            return 0;
        }
        if (present)
        {
            if (value < INT32_MIN || value > INT32_MAX)
            {
                return 0;
            }
            raw_values[index] = (int32_t)value;
            *valid_mask |= 1u << index;
        }
    }
    return variable_table_unchanged(context, header_address, &table);
}

/** Marks a protection prayer known/active from its standard and curse flag bits. */
static void apply_overhead_flags(rs3_combat_state *state, uint32_t prayer, uint32_t normal_bit,
                                 uint32_t curses_bit)
{
    uint32_t prayer_bit = RS3_PRAYER_BIT(prayer);
    int normal_active =
        (state->raw_valid_mask & PRAYER_VALID_NORMAL) && ((uint32_t)state->normal_raw & normal_bit);
    int curses_active =
        (state->raw_valid_mask & PRAYER_VALID_CURSES) && ((uint32_t)state->curses_raw & curses_bit);
    if (normal_active || curses_active)
    {
        state->known_mask |= prayer_bit;
        state->active_mask |= prayer_bit;
    }
    else if ((state->raw_valid_mask & PRAYER_VALID_BOTH_BOOKS) == PRAYER_VALID_BOTH_BOOKS)
    {
        state->known_mask |= prayer_bit;
    }
}

/** Fills the raw fields and the known/active masks from consistent raw values. */
static void decode_overheads(rs3_combat_state *state, const int32_t raw_values[], uint32_t valid)
{
    state->normal_raw = raw_values[PRAYER_VARIABLE_NORMAL];
    state->curses_raw = raw_values[PRAYER_VARIABLE_CURSES];
    state->normal_necro_raw = raw_values[PRAYER_VARIABLE_NORMAL_NECROMANCY];
    state->curses_necro_raw = raw_values[PRAYER_VARIABLE_CURSES_NECROMANCY];
    state->raw_valid_mask = valid;

    apply_overhead_flags(state, RS3_PRAYER_MAGIC, NORMAL_PROTECT_MAGIC_BIT,
                         CURSES_DEFLECT_MAGIC_BIT);
    apply_overhead_flags(state, RS3_PRAYER_RANGED, NORMAL_PROTECT_RANGED_BIT,
                         CURSES_DEFLECT_RANGED_BIT);
    apply_overhead_flags(state, RS3_PRAYER_MELEE, NORMAL_PROTECT_MELEE_BIT,
                         CURSES_DEFLECT_MELEE_BIT);

    uint32_t soul_split_bit = RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT);
    if (valid & PRAYER_VALID_CURSES)
    {
        state->known_mask |= soul_split_bit;
        if ((uint32_t)state->curses_raw & CURSES_SOUL_SPLIT_BIT)
        {
            state->active_mask |= soul_split_bit;
        }
    }

    uint32_t necromancy_bit = RS3_PRAYER_BIT(RS3_PRAYER_NECROMANCY);
    int normal_necromancy_active =
        (valid & PRAYER_VALID_NORMAL_NECROMANCY) &&
        ((uint32_t)state->normal_necro_raw & NORMAL_PROTECT_NECROMANCY_BIT);
    int curses_necromancy_active =
        (valid & PRAYER_VALID_CURSES_NECROMANCY) &&
        ((uint32_t)state->curses_necro_raw & CURSES_DEFLECT_NECROMANCY_BIT);
    if (normal_necromancy_active || curses_necromancy_active)
    {
        state->known_mask |= necromancy_bit;
        state->active_mask |= necromancy_bit;
    }
    else if ((valid & PRAYER_VALID_BOTH_NECROMANCY_BOOKS) == PRAYER_VALID_BOTH_NECROMANCY_BOOKS)
    {
        state->known_mask |= necromancy_bit;
    }
}

void combat_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                  uint64_t world_state)
{
    /* Preserve the frame's failure state: this optional decode must not replace it. */
    uint32_t saved_reason = context->failure_reason;
    char saved_message[FAILURE_MESSAGE_CAPACITY];
    memcpy(saved_message, context->failure_message, sizeof saved_message);

    rs3_combat_state *state = &snapshot->combat;
    memset(state, 0, sizeof *state);
    state->status = RS3_PARTIAL;
    state->reason = RS3_NOT_SET;

    int32_t scene_state;
    if (memory_read(context, world_state + LAYOUT_GAME_STATE_OFFSET, &scene_state,
                    sizeof scene_state))
    {
        state->scene_state = scene_state;
    }
    int scene_active = state->scene_state == (int32_t)LAYOUT_IN_GAME_STATE;

    /* Two identical consecutive reads guard against torn prayer flags. */
    int32_t first_values[PRAYER_VARIABLE_COUNT] = {0};
    int32_t second_values[PRAYER_VARIABLE_COUNT] = {0};
    uint32_t first_valid = 0;
    uint32_t second_valid = 0;
    int prayers_consistent =
        scene_active &&
        read_prayer_variables(context, player_address, first_values, &first_valid) &&
        read_prayer_variables(context, player_address, second_values, &second_valid) &&
        first_valid == second_valid &&
        memcmp(first_values, second_values, sizeof first_values) == 0;

    /* Verify the scene independently of the optional prayer reads. */
    int32_t scene_state_after;
    if (!scene_active)
    {
        state->status = RS3_UNAVAILABLE;
    }
    else if (!memory_read(context, world_state + LAYOUT_GAME_STATE_OFFSET, &scene_state_after,
                          sizeof scene_state_after) ||
             scene_state_after != state->scene_state)
    {
        state->scene_state = 0;
        state->status = RS3_UNAVAILABLE;
        state->reason = RS3_READ_FAILED;
    }
    else if (!prayers_consistent)
    {
        state->status = RS3_UNAVAILABLE;
        state->reason = RS3_READ_FAILED;
    }
    else
    {
        decode_overheads(state, first_values, first_valid);
        if (rs3_combat_active_prayer(state) != RS3_PRAYER_UNKNOWN)
        {
            state->status = RS3_AVAILABLE;
            state->reason = RS3_REASON_NONE;
        }
    }

    context->failure_reason = saved_reason;
    memcpy(context->failure_message, saved_message, sizeof saved_message);
}

void combat_commit(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    combat_tracker *tracker = &context->combat;
    if (snapshot->combat.scene_state != (int32_t)LAYOUT_IN_GAME_STATE)
    {
        tracker->world_state = 0;
        snapshot->combat.scene_epoch = 0;
        return;
    }
    /* A new world state or a restarted process starts a new scene epoch. */
    if (tracker->world_state != world_state ||
        tracker->process_creation_time != context->target.process_creation_time)
    {
        tracker->world_state = world_state;
        tracker->process_creation_time = context->target.process_creation_time;
        tracker->scene_epoch++;
    }
    snapshot->combat.scene_epoch = tracker->scene_epoch;
}
