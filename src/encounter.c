/* Telos variables: calibrated semantics and raw evidence must remain separate. */
#include "encounter.h"

#include <math.h>
#include <string.h>

#include "clock.h"
#include "memory.h"
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
#define TICK_HANDLER_GUARD_RVA   0x36960du
#define TICK_OWNER_GLOBAL_RVA    0x17345a8u
#define TICK_COUNTER_OFFSET      0xdbf0u
#define TICK_MAX_SAMPLE_GAP_NS   150000000ull
#define TICK_MAX_STALL_NS        1500000000ull
#define TICK_MAX_FORWARD_STEP    4u

/* Raw channels include calibrated sources and neighbouring candidates; presence alone proves no
 * semantics. */
static const uint32_t research_varps[RS3_ENCOUNTER_RAW_CAPACITY] = {
    6359u, 6360u, 6361u, 6362u, TELOS_PACKED_VARP, 6364u};

_Static_assert(sizeof(rs3_encounter_value) == 32, "ABI encounter value");
_Static_assert(sizeof(rs3_encounter_raw) == 32, "ABI encounter raw");
_Static_assert(sizeof(rs3_game_tick) == 40, "ABI game tick");
_Static_assert(sizeof(rs3_encounter_state) == 472, "ABI encounter state");

void encounter_clear(rs3_snapshot *snapshot)
{
    memset(&snapshot->encounter, 0, sizeof snapshot->encounter);
    snapshot->encounter.game_tick.phase_ms = NAN;
    snapshot->encounter.game_tick.uncertainty_ms = NAN;
    snapshot->encounter_tick_owner = 0;
    snapshot->encounter_tick_world_state = 0;
    snapshot->encounter_tick_last_change_ns = 0;
    snapshot->encounter_tick_counter = 0;
    snapshot->encounter_tick_primed = 0;
}

/** Read the original executable's received-tick counter; never estimate a server phase. */
static void stage_game_tick(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    rs3_game_tick *tick = &snapshot->encounter.game_tick;
    static const unsigned char handler_guard[] = {0x48, 0x8b, 0x0d, 0x94, 0xaf, 0x3c,
                                                  0x01, 0xff, 0x81, 0xf0, 0xdb, 0x00,
                                                  0x00, 0xe8, 0xc1, 0x0c, 0x8b, 0x00};
    unsigned char observed_guard[sizeof handler_guard];
    if (context->target.profile != &profile_primary ||
        snapshot->combat.scene_state != (int32_t)LAYOUT_IN_GAME_STATE || !world_state ||
        !memory_read(context, context->target.module_base + TICK_HANDLER_GUARD_RVA, observed_guard,
                     sizeof observed_guard) ||
        memcmp(observed_guard, handler_guard, sizeof handler_guard))
    {
        return;
    }
    uint64_t owner, owner_after;
    uint32_t counter, counter_after;
    tick->reason = RS3_READ_FAILED;
    if (!memory_read_pointer(context, context->target.module_base + TICK_OWNER_GLOBAL_RVA,
                             &owner) ||
        !owner || !memory_read(context, owner + TICK_COUNTER_OFFSET, &counter, sizeof counter) ||
        !memory_read(context, owner + TICK_COUNTER_OFFSET, &counter_after, sizeof counter_after) ||
        counter_after != counter ||
        !memory_read_pointer(context, context->target.module_base + TICK_OWNER_GLOBAL_RVA,
                             &owner_after) ||
        owner_after != owner)
    {
        return;
    }
    uint64_t now_ns = clock_monotonic_ns();
    snapshot->encounter_tick_owner = owner;
    snapshot->encounter_tick_world_state = world_state;
    snapshot->encounter_tick_last_change_ns = now_ns;
    snapshot->encounter_tick_counter = counter;
    tick->observed_ns = now_ns;
    tick->reason = RS3_ENCOUNTER_TICK_BASELINE;

    uint64_t previous_counter = 0, previous_changed_ns = 0;
    uint32_t previous_primed = 0;
    int adjacent = 0;
    AcquireSRWLockShared(&context->latest_frame_lock);
    const rs3_snapshot *previous = context->latest_frame;
    if (previous != NULL)
    {
        uint64_t previous_read_ns = previous->encounter.game_tick.observed_ns;
        adjacent =
            previous->encounter.pid == context->target.pid &&
            previous->encounter.process_creation_time == context->target.process_creation_time &&
            previous->encounter_tick_owner == owner &&
            previous->encounter_tick_world_state == world_state &&
            previous->info.sequence + 1 == snapshot->info.sequence && previous_read_ns &&
            now_ns >= previous_read_ns && now_ns - previous_read_ns <= TICK_MAX_SAMPLE_GAP_NS;
        previous_counter = previous->encounter_tick_counter;
        previous_changed_ns = previous->encounter_tick_last_change_ns;
        previous_primed = previous->encounter_tick_primed;
    }
    ReleaseSRWLockShared(&context->latest_frame_lock);
    if (!adjacent)
    {
        return;
    }
    uint32_t delta = counter - (uint32_t)previous_counter;
    if (delta > TICK_MAX_FORWARD_STEP)
    {
        tick->reason = RS3_ENCOUNTER_TICK_RESET;
        return;
    }
    snapshot->encounter_tick_counter = previous_counter + delta;
    snapshot->encounter_tick_last_change_ns = delta ? now_ns : previous_changed_ns;
    snapshot->encounter_tick_primed = delta ? 1u : previous_primed;
    if (now_ns < snapshot->encounter_tick_last_change_ns ||
        now_ns - snapshot->encounter_tick_last_change_ns > TICK_MAX_STALL_NS)
    {
        snapshot->encounter_tick_primed = 0;
        tick->reason = RS3_ENCOUNTER_TICK_STALLED;
        return;
    }
    if (snapshot->encounter_tick_primed)
    {
        tick->counter = snapshot->encounter_tick_counter;
        tick->valid = 1;
        tick->reason = RS3_REASON_NONE;
    }
}

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

void encounter_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t world_state)
{
    rs3_encounter_state *state = &snapshot->encounter;
    encounter_clear(snapshot);
    invalidate_semantics(state);
    stage_game_tick(context, snapshot, world_state);
    state->status = RS3_UNAVAILABLE;
    if (state->game_tick.valid)
    {
        state->status = RS3_PARTIAL;
    }
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
