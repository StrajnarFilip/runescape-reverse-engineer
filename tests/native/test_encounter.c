/* P2 must distinguish raw evidence, validated semantics and unknown clocks. */
#include "fixture.h"
#include "encounter.h"

#define PACKED_VARP 6363u
#define RAW_NODE    0x880000ull

static void present_stage_is_not_a_phase_or_game_tick(void)
{
    fixture *test_fixture = fixture_create();
    fixture_add_block(test_fixture, RAW_NODE, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE, PACKED_VARP, 3u << 21);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER);
    rs3_encounter_state *state = &test_fixture->snapshot->encounter;
    assert(state->stage_raw.valid && state->stage_raw.value == 3);
    assert(!state->phase.valid && state->phase.reason == RS3_ENCOUNTER_UNMAPPED);
    assert(!state->enrage.valid && !state->streak.valid && !state->meter.valid &&
           !state->freedom.valid);
    assert(!state->game_tick.valid && state->game_tick.reason == RS3_ENCOUNTER_UNMAPPED);
    fixture_put_u64(test_fixture, RAW_NODE + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, 5u << 21);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER);
    assert(state->stage_raw.valid && state->stage_raw.value == 5 && !state->phase.valid);
    fixture_destroy(test_fixture);
}

static void absence_and_zero_are_distinct(void)
{
    fixture *test_fixture = fixture_create();
    /* The basic fixture may contain Telos packed data; remove its sparse bucket. */
    uint64_t bucket = FIXTURE_PLAYER_TABLE + (PACKED_VARP % FIXTURE_BUCKET_COUNT) * 8;
    fixture_put_u64(test_fixture, bucket, FIXTURE_SENTINEL);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER);
    rs3_encounter_state *state = &test_fixture->snapshot->encounter;
    assert(!state->stage_raw.valid && state->stage_raw.reason == RS3_NOT_SET);
    fixture_add_block(test_fixture, RAW_NODE, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE, PACKED_VARP, 0);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER);
    assert(state->stage_raw.valid && state->stage_raw.value == 0);
    assert(!state->phase.valid);
    fixture_destroy(test_fixture);
}

static void changed_table_invalidates_raw_fields(void)
{
    fixture *test_fixture = fixture_create();
    fixture_mutate_on_read(test_fixture, FIXTURE_PLAYER + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET, 2,
                           FIXTURE_PLAYER + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET, 0);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER);
    assert(!test_fixture->snapshot->encounter.stage_raw.valid);
    for (uint32_t index = 0; index < RS3_ENCOUNTER_RAW_CAPACITY; index++)
    {
        assert(!test_fixture->snapshot->encounter.raw[index].valid);
    }
    fixture_destroy(test_fixture);
}

static void snapshot_accessor_and_commit_preserve_provenance(void)
{
    fixture *test_fixture = fixture_create();
    test_fixture->snapshot->info.sequence = 7;
    test_fixture->snapshot->combat.scene_epoch = 9;
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER);
    encounter_commit(test_fixture->context, test_fixture->snapshot);
    rs3_encounter_state state;
    assert(rs3_snapshot_encounter(test_fixture->snapshot, &state, sizeof state) == RS3_OK);
    assert(state.sample_sequence == 7 && state.scene_epoch == 9);
    assert(state.pid == FIXTURE_PID &&
           state.process_creation_time == FIXTURE_PROCESS_CREATION_TIME);
    assert(rs3_snapshot_encounter(test_fixture->snapshot, &state, 1) == RS3_E_ABI);
    assert(rs3_encounter_sizeof(RS3_ENCOUNTER_SIZE_STATE) == sizeof state);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(present_stage_is_not_a_phase_or_game_tick);
    RUN_TEST(absence_and_zero_are_distinct);
    RUN_TEST(changed_table_invalidates_raw_fields);
    RUN_TEST(snapshot_accessor_and_commit_preserve_provenance);
    return 0;
}
