/* Player effects: candidate provenance is separate from validated semantics. */
#include "effects.h"

#include <string.h>

#include "variables.h"

_Static_assert(sizeof(rs3_effect) == 72, "ABI effect layout");
_Static_assert(offsetof(rs3_effect, raw_expiry) == 32, "ABI effect raw alignment");
_Static_assert(sizeof(rs3_effects_info) == 16, "ABI effect info layout");

#define MAX_EFFECT_CLOCK_INTERVAL_TICKS 180000

typedef struct effect_candidate
{
    uint32_t source;
    uint32_t expiry_var;
    uint32_t stacks_var;
    uint32_t active_var;
} effect_candidate;

/*
 * Symbol-name leads from the gamevals reference. These are not a buff-list
 * decoder or proof of presence. In particular an end_effect variable is not
 * the ability's start/end cooldown pair. Original 951-3 only until calibrated.
 */
static const effect_candidate candidates[RS3_EFFECT_COUNT] = {
    {RS3_EFFECT_SOURCE_VARPLAYER, 12657, 0, 12656}, /* Berserk, modernisation lead. */
    {RS3_EFFECT_SOURCE_VARPLAYER, 0, 12655, 12640}, /* Bloodlust. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3732, 0, 0},      /* Overload. */
    {0, 0, 0, 0},                                   /* Adrenaline renewal. */
    {0, 0, 0, 0},                                   /* Anima virus. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3748, 0, 0},      /* Stun. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3749, 0, 0},      /* Bind. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3741, 0, 0},      /* Resonance. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3736, 0, 0},      /* Anticipation. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3733, 0, 0},      /* Freedom. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3901, 0, 0},      /* Devotion. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3735, 0, 0},      /* Barricade. */
    {RS3_EFFECT_SOURCE_VARCLIENT, 3734, 0, 0},      /* Heroism: Immortality candidate. */
    {RS3_EFFECT_SOURCE_VARPLAYER, 0, 0, 4}          /* Varbit 9, bit 13: shield lead. */
};

uint32_t rs3_effects_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case 1:
            return sizeof(rs3_effects_info);
        case 2:
            return sizeof(rs3_effect);
        default:
            return 0;
    }
}

void effects_clear(rs3_snapshot *snapshot)
{
    memset(&snapshot->effects_info, 0, sizeof snapshot->effects_info);
    memset(snapshot->effects, 0, sizeof snapshot->effects);
    snapshot->effects_info.status = RS3_UNAVAILABLE;
    snapshot->effects_info.reason = RS3_EFFECT_FRAME_UNAVAILABLE;
}

/** Read one optional source twice to reject a changing value, not just a reused key. */
static int read_candidate(rs3_context *context, const variable_table *table, uint32_t variable,
                          int64_t *value, uint32_t bit, uint32_t *valid_mask)
{
    if (variable == 0)
    {
        return 1;
    }
    int present = 0;
    int present_after = 0;
    int64_t value_after = 0;
    if (!variable_table_find(context, table, variable, value, &present) ||
        !variable_table_find(context, table, variable, &value_after, &present_after) ||
        present != present_after || (present && *value != value_after))
    {
        *value = 0;
        return 0;
    }
    if (present)
    {
        *valid_mask |= bit;
    }
    return 1;
}

void effects_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                   uint64_t client_address)
{
    effects_clear(snapshot);
    snapshot->effects_info.count = RS3_EFFECT_COUNT;
    snapshot->effects_info.status = RS3_PARTIAL;
    snapshot->effects_info.reason = RS3_EFFECT_UNVALIDATED;
    int supported = context->target.profile == &profile_primary;
    if (!supported)
    {
        snapshot->effects_info.reason = RS3_EFFECT_UNSUPPORTED_PROFILE;
    }
    variable_table tables[2] = {0};
    uint64_t headers[2] = {player_address + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET,
                           client_address + LAYOUT_CLIENT_VARIABLE_TABLE_OFFSET};
    int table_available[2] = {0};
    if (supported)
    {
        for (unsigned index = 0; index < 2; index++)
        {
            table_available[index] = variable_table_open(context, headers[index], &tables[index]);
        }
    }
    for (unsigned index = 0; index < RS3_EFFECT_COUNT; index++)
    {
        rs3_effect *effect = &snapshot->effects[index];
        const effect_candidate *candidate = &candidates[index];
        effect->effect_id = index + 1;
        effect->active = -1;
        effect->stacks = -1;
        effect->remaining_ms = -1;
        effect->source = candidate->source;
        effect->expiry_var = candidate->expiry_var;
        effect->stacks_var = candidate->stacks_var;
        effect->active_var = candidate->active_var;
        effect->reason = RS3_EFFECT_UNMAPPED;
        if (candidate->source == RS3_EFFECT_SOURCE_UNMAPPED)
        {
            continue;
        }
        effect->reason = RS3_EFFECT_UNSUPPORTED_PROFILE;
        if (!supported)
        {
            continue;
        }
        unsigned table_index = candidate->source - 1;
        effect->reason = RS3_EFFECT_READ_FAILED;
        if (table_available[table_index] &&
            read_candidate(context, &tables[table_index], candidate->expiry_var,
                           &effect->raw_expiry, 1, &effect->raw_valid_mask) &&
            read_candidate(context, &tables[table_index], candidate->stacks_var,
                           &effect->raw_stacks, 2, &effect->raw_valid_mask) &&
            read_candidate(context, &tables[table_index], candidate->active_var,
                           &effect->raw_active, 4, &effect->raw_valid_mask))
        {
            effect->reason = effect->raw_valid_mask ? RS3_EFFECT_UNVALIDATED : RS3_EFFECT_NOT_SET;
        }
        else
        {
            effect->raw_valid_mask = 0;
        }
    }
    /*
     * Four individually labelled effects changed in the user's 2026-11-11
     * capture. The overload countdown also matched the displayed sub-minute
     * timer against the 20 ms clock. Only the overload countdown has a live duration check. This
     * does not validate presence or early cancellation: never set ACTIVE_KNOWN from a positive
     * timer.
     */
    const uint32_t expiry_validated[] = {RS3_EFFECT_OVERLOAD};
    for (unsigned index = 0; index < sizeof expiry_validated / sizeof *expiry_validated; index++)
    {
        rs3_effect *effect = &snapshot->effects[expiry_validated[index] - 1];
        if (effect->reason == RS3_EFFECT_UNVALIDATED && (effect->raw_valid_mask & 1) &&
            effect->raw_expiry >= 0 && effect->raw_expiry <= UINT32_MAX)
        {
            uint32_t difference = (uint32_t)effect->raw_expiry - snapshot->info.logic_tick;
            int64_t remaining =
                difference <= INT32_MAX ? difference : (int64_t)difference - 4294967296ll;
            /* Reject future timestamps beyond one hour; past endpoints mean timer expiry only. */
            if (remaining <= MAX_EFFECT_CLOCK_INTERVAL_TICKS &&
                remaining >= -MAX_EFFECT_CLOCK_INTERVAL_TICKS)
            {
                effect->remaining_ms =
                    remaining > 0 ? (int32_t)remaining * LAYOUT_LOGIC_TICK_MS : 0;
                effect->flags |= RS3_EFFECT_REMAINING_KNOWN;
                effect->reason = RS3_EFFECT_PRESENCE_UNMAPPED;
            }
        }
    }
    for (unsigned table_index = 0; table_index < 2; table_index++)
    {
        if (table_available[table_index] &&
            !variable_table_unchanged(context, headers[table_index], &tables[table_index]))
        {
            for (unsigned index = 0; index < RS3_EFFECT_COUNT; index++)
            {
                rs3_effect *effect = &snapshot->effects[index];
                if (effect->source == table_index + 1)
                {
                    effect->raw_valid_mask = 0;
                    effect->flags = 0;
                    effect->remaining_ms = -1;
                    effect->reason = RS3_EFFECT_READ_FAILED;
                }
            }
        }
    }
}
