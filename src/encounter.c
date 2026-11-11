/* Telos variables: calibrated semantics and raw evidence must remain separate. */
#include "encounter.h"

#include <string.h>

#include "clock.h"
#include "variables.h"

#define TELOS_PACKED_VARP  6363u
#define TELOS_STAGE_VARBIT 32640u
#define TELOS_STAGE_SHIFT  21u
#define TELOS_STAGE_MASK   7u

/* Neighbouring definitions are research candidates, NOT asserted enrage/streak mappings. */
static const uint32_t research_varps[RS3_ENCOUNTER_RAW_CAPACITY] = {
    6359u, 6360u, 6361u, 6362u, TELOS_PACKED_VARP, 6364u};

_Static_assert(sizeof(rs3_encounter_value) == 32, "ABI encounter value");
_Static_assert(sizeof(rs3_encounter_raw) == 32, "ABI encounter raw");
_Static_assert(sizeof(rs3_game_tick) == 40, "ABI game tick");
_Static_assert(sizeof(rs3_encounter_state) == 472, "ABI encounter state");

static void invalidate_semantics(rs3_encounter_state *state)
{
    state->phase.reason = RS3_ENCOUNTER_UNMAPPED;
    state->enrage.reason = RS3_ENCOUNTER_UNMAPPED;
    state->streak.reason = RS3_ENCOUNTER_UNMAPPED;
    state->meter.reason = RS3_ENCOUNTER_UNMAPPED;
    state->freedom.reason = RS3_ENCOUNTER_UNMAPPED;
    state->game_tick.reason = RS3_ENCOUNTER_UNMAPPED;
    state->stage_raw.reason = RS3_NOT_SET;
    state->stage_raw.source_varp = TELOS_PACKED_VARP;
    state->stage_raw.source_varbit = TELOS_STAGE_VARBIT;
}

void encounter_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address)
{
    rs3_encounter_state *state = &snapshot->encounter;
    memset(state, 0, sizeof *state);
    invalidate_semantics(state);
    state->status = RS3_UNAVAILABLE;
    state->reason = RS3_NOT_SET;
    state->raw_count = RS3_ENCOUNTER_RAW_CAPACITY;
    variable_table table;
    uint64_t header = player_address + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET;
    int opened = variable_table_open(context, header, &table);
    for (uint32_t index = 0; index < RS3_ENCOUNTER_RAW_CAPACITY; index++)
    {
        rs3_encounter_raw *raw = &state->raw[index];
        raw->varp_id = research_varps[index];
        raw->reason = RS3_READ_FAILED;
        if (!opened)
        {
            continue;
        }
        int first_present, second_present;
        int64_t first_value = 0, second_value = 0;
        if (!variable_table_find(context, &table, raw->varp_id, &first_value, &first_present) ||
            !variable_table_find(context, &table, raw->varp_id, &second_value, &second_present) ||
            first_present != second_present || (first_present && first_value != second_value))
        {
            continue;
        }
        raw->reason = RS3_NOT_SET;
        if (first_present)
        {
            raw->value = first_value;
            raw->valid = 1;
            raw->reason = RS3_REASON_NONE;
            raw->observed_ns = clock_monotonic_ns();
        }
    }
    if (!opened || !variable_table_unchanged(context, header, &table))
    {
        for (uint32_t index = 0; index < state->raw_count; index++)
        {
            state->raw[index].valid = 0;
            state->raw[index].reason = RS3_READ_FAILED;
            state->raw[index].value = 0;
        }
        state->reason = RS3_READ_FAILED;
        return;
    }
    for (uint32_t index = 0; index < state->raw_count; index++)
    {
        const rs3_encounter_raw *raw = &state->raw[index];
        if (raw->valid)
        {
            state->status = RS3_PARTIAL;
            state->reason = RS3_ENCOUNTER_UNMAPPED;
        }
        if (raw->varp_id == TELOS_PACKED_VARP && raw->valid && raw->value >= INT32_MIN &&
            raw->value <= UINT32_MAX)
        {
            state->stage_raw.value = ((uint32_t)raw->value >> TELOS_STAGE_SHIFT) & TELOS_STAGE_MASK;
            state->stage_raw.valid = 1;
            state->stage_raw.reason = RS3_REASON_NONE;
            state->stage_raw.observed_ns = raw->observed_ns;
        }
    }
    /* A raw stage, sparse presence, or client_logic_tick / 30 does not validate a phase or game
     * tick. */
}

void encounter_commit(rs3_context *context, rs3_snapshot *snapshot)
{
    rs3_encounter_state *state = &snapshot->encounter;
    state->sample_sequence = snapshot->info.sequence;
    state->observed_ns = snapshot->info.monotonic_ns;
    state->scene_epoch = snapshot->combat.scene_epoch;
    state->pid = context->target.pid;
    state->process_creation_time = context->target.process_creation_time;
}

uint32_t rs3_encounter_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_ENCOUNTER_SIZE_VALUE:
            return sizeof(rs3_encounter_value);
        case RS3_ENCOUNTER_SIZE_RAW:
            return sizeof(rs3_encounter_raw);
        case RS3_ENCOUNTER_SIZE_GAME_TICK:
            return sizeof(rs3_game_tick);
        case RS3_ENCOUNTER_SIZE_STATE:
            return sizeof(rs3_encounter_state);
        default:
            return 0;
    }
}
