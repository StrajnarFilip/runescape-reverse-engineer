/* Telos variables: calibrated semantics and raw evidence must remain separate. */
#include "encounter.h"

#include <string.h>

#include "clock.h"
#include "variables.h"

#define TELOS_PACKED_VARP        6363u
#define TELOS_STAGE_VARBIT       32640u
#define TELOS_STAGE_SHIFT        21u
#define TELOS_STAGE_MASK         7u
#define TELOS_INSTANCE_VARP      6359u
#define TELOS_ENRAGE_VARBIT      32626u
#define TELOS_ENRAGE_MASK        4095u
#define MAX_SUPPORTED_ENRAGE     4000u
#define ACTIVE_TELOS_CONFIG_ID   22891
#define LAST_VALIDATED_STAGE     3u
#define TELOS_INSTANCE_GUARD_BIT (1u << 29)

/* Raw channels include calibrated sources and neighbouring candidates; presence alone proves no
 * semantics. */
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

/** Match the single captured active form; dormant entities and duplicate forms cannot authorize
 * data. */
static int has_active_telos(const rs3_snapshot *snapshot)
{
    unsigned matches = 0;
    for (uint32_t index = 0; index < snapshot->info.npc_count; index++)
    {
        const rs3_npc *npc = &snapshot->npcs[index];
        if ((npc->flags & RS3_NPC_CONFIG) && npc->config_id == ACTIVE_TELOS_CONFIG_ID)
        {
            matches++;
        }
    }
    int instance_guard = 0;
    for (uint32_t index = 0; index < snapshot->encounter.raw_count; index++)
    {
        const rs3_encounter_raw *raw = &snapshot->encounter.raw[index];
        if (raw->varp_id == TELOS_INSTANCE_VARP && raw->valid)
        {
            instance_guard = ((uint32_t)raw->value & TELOS_INSTANCE_GUARD_BIT) != 0;
        }
    }
    return matches == 1 && instance_guard;
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
    /* These semantics were calibrated only on the original profile, phases 1..4. */
    if (context->target.profile == &profile_primary)
    {
        if (!has_active_telos(snapshot))
        {
            state->phase.reason = RS3_ENCOUNTER_NOT_ACTIVE;
            state->enrage.reason = RS3_ENCOUNTER_NOT_ACTIVE;
        }
        else
        {
            state->phase.reason = state->stage_raw.reason;
            if (state->stage_raw.valid)
            {
                if (state->stage_raw.value <= LAST_VALIDATED_STAGE)
                {
                    state->phase = state->stage_raw;
                    state->phase.value++;
                }
                else
                {
                    state->phase.reason = RS3_ENCOUNTER_UNMAPPED;
                }
            }
            for (uint32_t index = 0; index < state->raw_count; index++)
            {
                const rs3_encounter_raw *raw = &state->raw[index];
                if (raw->varp_id == TELOS_INSTANCE_VARP)
                {
                    state->enrage.reason = raw->reason;
                    if (raw->valid)
                    {
                        uint32_t enrage = (uint32_t)raw->value & TELOS_ENRAGE_MASK;
                        state->enrage.reason = RS3_INVALID_STATS;
                        if (raw->value >= INT32_MIN && raw->value <= UINT32_MAX &&
                            enrage <= MAX_SUPPORTED_ENRAGE)
                        {
                            state->enrage =
                                (rs3_encounter_value){.value = enrage,
                                                      .observed_ns = raw->observed_ns,
                                                      .valid = 1,
                                                      .source_varp = TELOS_INSTANCE_VARP,
                                                      .source_varbit = TELOS_ENRAGE_VARBIT};
                        }
                    }
                }
            }
        }
    }
    /* A predicted cadence or client_logic_tick / 30 never validates a game tick. */
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
