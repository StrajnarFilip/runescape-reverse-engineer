/* P2 must distinguish raw evidence, validated semantics and unknown clocks. */
#include "fixture.h"
#include "encounter.h"

#include <math.h>
#include <string.h>

#define PACKED_VARP            6363u
#define RAW_NODE               0x880000ull
#define TICK_OWNER             0x990000ull
#define TICK_OWNER_GLOBAL_RVA  0x17345a8u
#define TICK_HANDLER_GUARD_RVA 0x36960du
#define TICK_COUNTER_OFFSET    0xdbf0u
#define STALE_TICK_AGE_NS      2000000000ull

static void present_stage_is_not_a_phase_or_game_tick(void)
{
    fixture *test_fixture = fixture_create();
    fixture_add_block(test_fixture, RAW_NODE, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE, PACKED_VARP, 3u << 21);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    rs3_encounter_state *state = &test_fixture->snapshot->encounter;
    assert(state->stage_raw.valid && state->stage_raw.value == 3);
    assert(!state->phase.valid && state->phase.reason == RS3_ENCOUNTER_NOT_ACTIVE);
    assert(!state->enrage.valid && !state->streak.valid && !state->meter.valid &&
           !state->freedom.valid);
    assert(!state->game_tick.valid && state->game_tick.reason == RS3_ENCOUNTER_UNMAPPED);
    fixture_put_u64(test_fixture, RAW_NODE + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, 5u << 21);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(state->stage_raw.valid && state->stage_raw.value == 5 && !state->phase.valid);
    fixture_destroy(test_fixture);
}

static void absence_and_zero_are_distinct(void)
{
    fixture *test_fixture = fixture_create();
    /* The basic fixture may contain Telos packed data; remove its sparse bucket. */
    uint64_t bucket = FIXTURE_PLAYER_TABLE + (PACKED_VARP % FIXTURE_BUCKET_COUNT) * 8;
    fixture_put_u64(test_fixture, bucket, FIXTURE_SENTINEL);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    rs3_encounter_state *state = &test_fixture->snapshot->encounter;
    assert(!state->stage_raw.valid && state->stage_raw.reason == RS3_NOT_SET);
    fixture_add_block(test_fixture, RAW_NODE, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE, PACKED_VARP, 0);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(state->stage_raw.valid && state->stage_raw.value == 0);
    assert(!state->phase.valid);
    fixture_destroy(test_fixture);
}

static void changed_table_invalidates_raw_fields(void)
{
    fixture *test_fixture = fixture_create();
    fixture_mutate_on_read(test_fixture, FIXTURE_PLAYER + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET, 2,
                           FIXTURE_PLAYER + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET, 0);
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
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
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
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

static void labelled_phases_and_enrage_require_active_telos(void)
{
    fixture *test_fixture = fixture_create();
    fixture_add_block(test_fixture, RAW_NODE, FIXTURE_VARIABLE_NODE_BYTES * 2);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE, PACKED_VARP, 3u << 21);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE + FIXTURE_VARIABLE_NODE_BYTES,
                         6359, 0x7953012cu);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.npc_count = 1;
    snapshot->npcs[0].flags = RS3_NPC_CONFIG;
    snapshot->npcs[0].config_id = 22891;
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(snapshot->encounter.phase.valid && snapshot->encounter.phase.value == 4);
    assert(snapshot->encounter.enrage.valid && snapshot->encounter.enrage.value == 300);
    assert(snapshot->encounter.enrage.source_varp == 6359);
    const uint32_t captured_stages[] = {0, 1, 2, 3};
    const int64_t expected_phases[] = {1, 2, 3, 4};
    for (unsigned index = 0; index < 4; index++)
    {
        fixture_put_u64(test_fixture, RAW_NODE + LAYOUT_VARIABLE_NODE_VALUE_OFFSET,
                        captured_stages[index] << 21);
        encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
        assert(snapshot->encounter.phase.valid &&
               snapshot->encounter.phase.value == expected_phases[index]);
    }
    fixture_put_u64(test_fixture,
                    RAW_NODE + FIXTURE_VARIABLE_NODE_BYTES + LAYOUT_VARIABLE_NODE_VALUE_OFFSET,
                    0x795301f4u);
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(snapshot->encounter.enrage.valid && snapshot->encounter.enrage.value == 500);
    fixture_put_u64(test_fixture, RAW_NODE + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, 4u << 21);
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(!snapshot->encounter.phase.valid &&
           snapshot->encounter.phase.reason == RS3_ENCOUNTER_UNMAPPED);
    snapshot->npcs[0].config_id = 22892;
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(!snapshot->encounter.phase.valid && !snapshot->encounter.enrage.valid);
    snapshot->npcs[0].config_id = 22891;
    fixture_put_u64(test_fixture,
                    RAW_NODE + FIXTURE_VARIABLE_NODE_BYTES + LAYOUT_VARIABLE_NODE_VALUE_OFFSET,
                    0x595301f4u);
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(!snapshot->encounter.phase.valid && !snapshot->encounter.enrage.valid);
    fixture_put_u64(test_fixture,
                    RAW_NODE + FIXTURE_VARIABLE_NODE_BYTES + LAYOUT_VARIABLE_NODE_VALUE_OFFSET,
                    0x795301f4u);
    test_fixture->context->target.profile = &profile_compatibility;
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    /* Compatibility Mode publishes the enrage varbit (live-matched at 0-500%); phases stay
     * limited to the calibrated 0% case. */
    assert(!snapshot->encounter.phase.valid);
    assert(snapshot->encounter.enrage.valid && snapshot->encounter.enrage.value == 500);
    fixture_destroy(test_fixture);
}

static void compatibility_phase_requires_zero_enrage_and_matching_hp(void)
{
    fixture *test_fixture = fixture_create_for_profile(&profile_compatibility);
    const uint64_t enrage_node = RAW_NODE + FIXTURE_VARIABLE_NODE_BYTES;
    fixture_add_block(test_fixture, RAW_NODE, FIXTURE_VARIABLE_NODE_BYTES * 2);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, RAW_NODE, PACKED_VARP, 2u << 21);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, enrage_node, 6359, 0x78360000u);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.npc_count = 1;
    snapshot->npcs[0] = (rs3_npc){.flags = RS3_NPC_CONFIG | RS3_NPC_HEALTH,
                                  .config_id = 22891,
                                  .health_current = 200000,
                                  .health_max = 400000};
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(snapshot->encounter.phase.valid && snapshot->encounter.phase.value == 3);
    assert(snapshot->encounter.enrage.valid && snapshot->encounter.enrage.value == 0);
    assert(!snapshot->encounter.game_tick.valid);
    snapshot->npcs[0].health_current++;
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(!snapshot->encounter.phase.valid);
    snapshot->npcs[0].health_current--;
    /* 300% enrage: the enrage is published, but the 0%-calibrated phase is withheld. */
    fixture_put_u64(test_fixture, enrage_node + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, 0x7836012cu);
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(!snapshot->encounter.phase.valid);
    assert(snapshot->encounter.enrage.valid && snapshot->encounter.enrage.value == 300);
    /* Out-of-range enrage bits are not published. */
    fixture_put_u64(test_fixture, enrage_node + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, 0x78360fffu);
    encounter_stage(test_fixture->context, snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    assert(!snapshot->encounter.enrage.valid);
    fixture_destroy(test_fixture);
}

static fixture *tick_fixture(void)
{
    fixture *test_fixture = fixture_create();
    const unsigned char handler_guard[] = {0x48, 0x8b, 0x0d, 0x94, 0xaf, 0x3c, 0x01, 0xff, 0x81,
                                           0xf0, 0xdb, 0x00, 0x00, 0xe8, 0xc1, 0x0c, 0x8b, 0x00};
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + TICK_HANDLER_GUARD_RVA,
                      sizeof handler_guard);
    memcpy(fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + TICK_HANDLER_GUARD_RVA,
                         sizeof handler_guard),
           handler_guard, sizeof handler_guard);
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + TICK_OWNER_GLOBAL_RVA, 8);
    fixture_add_block(test_fixture, TICK_OWNER, TICK_COUNTER_OFFSET + 4);
    fixture_put_u64(test_fixture, FIXTURE_MODULE_BASE + TICK_OWNER_GLOBAL_RVA, TICK_OWNER);
    test_fixture->snapshot->combat.scene_state = LAYOUT_IN_GAME_STATE;
    return test_fixture;
}

static void sample_tick(fixture *test_fixture, uint32_t counter)
{
    fixture_put_u32(test_fixture, TICK_OWNER + TICK_COUNTER_OFFSET, counter);
    test_fixture->snapshot->info.sequence++;
    encounter_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_WORLD);
    encounter_commit(test_fixture->context, test_fixture->snapshot);
}

static void publish_tick_baseline(fixture *test_fixture)
{
    /* Model the normal frame publisher; direct decoder fixtures do not publish themselves. */
    memcpy(test_fixture->context->latest_frame, test_fixture->snapshot,
           sizeof *test_fixture->snapshot);
}

static void received_counter_is_independent_of_client_clock_and_telos(void)
{
    fixture *test_fixture = tick_fixture();
    sample_tick(test_fixture, 100);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_ENCOUNTER_TICK_BASELINE);
    publish_tick_baseline(test_fixture);
    test_fixture->snapshot->info.logic_tick = UINT32_MAX;
    sample_tick(test_fixture, 101);
    const rs3_game_tick *tick = &test_fixture->snapshot->encounter.game_tick;
    assert(tick->valid && tick->counter == 101 && !tick->reason);
    assert(isnan(tick->phase_ms) && isnan(tick->uncertainty_ms));
    assert(!test_fixture->snapshot->encounter.phase.valid);
    uint64_t changed_ns = test_fixture->snapshot->encounter_tick_last_change_ns;
    publish_tick_baseline(test_fixture);
    test_fixture->snapshot->info.logic_tick = 3;
    sample_tick(test_fixture, 101);
    assert(tick->valid && tick->counter == 101);
    assert(test_fixture->snapshot->encounter_tick_last_change_ns == changed_ns);
    fixture_destroy(test_fixture);
}

static void compatibility_received_counter_requires_its_own_handler(void)
{
    fixture *test_fixture = fixture_create_for_profile(&profile_compatibility);
    const uint64_t handler = FIXTURE_MODULE_BASE + 0x36952du;
    const uint64_t owner_global = FIXTURE_MODULE_BASE + 0x16d15e8u;
    const unsigned char guard[] = {0x48, 0x8b, 0x0d, 0xb4, 0x80, 0x36, 0x01, 0xff, 0x81,
                                   0xf0, 0xdb, 0x00, 0x00, 0xe8, 0x91, 0x0b, 0x8b, 0x00};
    fixture_add_block(test_fixture, handler, sizeof guard);
    fixture_add_block(test_fixture, owner_global, sizeof(uint64_t));
    fixture_add_block(test_fixture, TICK_OWNER, TICK_COUNTER_OFFSET + sizeof(uint32_t));
    memcpy(fixture_bytes(test_fixture, handler, sizeof guard), guard, sizeof guard);
    fixture_put_u64(test_fixture, owner_global, TICK_OWNER);
    test_fixture->snapshot->combat.scene_state = LAYOUT_IN_GAME_STATE;
    sample_tick(test_fixture, 100);
    publish_tick_baseline(test_fixture);
    sample_tick(test_fixture, 101);
    assert(test_fixture->snapshot->encounter.game_tick.valid);
    assert(test_fixture->snapshot->encounter.game_tick.counter == 101);
    assert(isnan(test_fixture->snapshot->encounter.game_tick.phase_ms));
    *fixture_bytes(test_fixture, handler, 1) ^= 1;
    publish_tick_baseline(test_fixture);
    sample_tick(test_fixture, 102);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    fixture_destroy(test_fixture);
}

static void tick_rejects_torn_owner_counter_and_instruction_guard(void)
{
    fixture *test_fixture = tick_fixture();
    fixture_mutate_on_read(test_fixture, FIXTURE_MODULE_BASE + TICK_OWNER_GLOBAL_RVA, 2,
                           FIXTURE_MODULE_BASE + TICK_OWNER_GLOBAL_RVA, 0);
    sample_tick(test_fixture, 100);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    fixture_put_u64(test_fixture, FIXTURE_MODULE_BASE + TICK_OWNER_GLOBAL_RVA, TICK_OWNER);
    fixture_mutate_on_read(test_fixture, TICK_OWNER + TICK_COUNTER_OFFSET, 2,
                           TICK_OWNER + TICK_COUNTER_OFFSET, 101);
    sample_tick(test_fixture, 100);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + TICK_HANDLER_GUARD_RVA, 1)[0] = 0;
    sample_tick(test_fixture, 102);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    assert(test_fixture->snapshot->encounter_tick_owner == 0);
    fixture_destroy(test_fixture);
}

static void tick_stall_reset_and_identity_changes_drop_validity(void)
{
    fixture *test_fixture = tick_fixture();
    sample_tick(test_fixture, 100);
    publish_tick_baseline(test_fixture);
    sample_tick(test_fixture, 101);
    publish_tick_baseline(test_fixture);
    test_fixture->context->latest_frame->encounter_tick_last_change_ns -= STALE_TICK_AGE_NS;
    sample_tick(test_fixture, 101);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_ENCOUNTER_TICK_STALLED);
    publish_tick_baseline(test_fixture);
    sample_tick(test_fixture, 1);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_ENCOUNTER_TICK_RESET);
    publish_tick_baseline(test_fixture);
    test_fixture->context->target.process_creation_time++;
    sample_tick(test_fixture, 2);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_ENCOUNTER_TICK_BASELINE);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    publish_tick_baseline(test_fixture);
    test_fixture->snapshot->info.sequence++;
    sample_tick(test_fixture, 3);
    assert(test_fixture->snapshot->encounter.game_tick.reason == RS3_ENCOUNTER_TICK_BASELINE);
    fixture_destroy(test_fixture);
}

static void tick_wraps_without_regressing_and_clear_removes_baseline(void)
{
    fixture *test_fixture = tick_fixture();
    sample_tick(test_fixture, UINT32_MAX - 1);
    publish_tick_baseline(test_fixture);
    sample_tick(test_fixture, UINT32_MAX);
    assert(test_fixture->snapshot->encounter.game_tick.valid);
    publish_tick_baseline(test_fixture);
    sample_tick(test_fixture, 0);
    assert(test_fixture->snapshot->encounter.game_tick.valid);
    assert(test_fixture->snapshot->encounter.game_tick.counter == (uint64_t)UINT32_MAX + 1);
    encounter_clear(test_fixture->snapshot);
    assert(!test_fixture->snapshot->encounter.game_tick.valid);
    assert(!test_fixture->snapshot->encounter_tick_owner);
    assert(!test_fixture->snapshot->encounter_tick_counter);
    assert(!test_fixture->snapshot->encounter_tick_last_change_ns);
    assert(!test_fixture->snapshot->encounter_tick_primed);
    assert(isnan(test_fixture->snapshot->encounter.game_tick.phase_ms));
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(present_stage_is_not_a_phase_or_game_tick);
    RUN_TEST(absence_and_zero_are_distinct);
    RUN_TEST(changed_table_invalidates_raw_fields);
    RUN_TEST(snapshot_accessor_and_commit_preserve_provenance);
    RUN_TEST(labelled_phases_and_enrage_require_active_telos);
    RUN_TEST(compatibility_phase_requires_zero_enrage_and_matching_hp);
    RUN_TEST(received_counter_is_independent_of_client_clock_and_telos);
    RUN_TEST(compatibility_received_counter_requires_its_own_handler);
    RUN_TEST(tick_rejects_torn_owner_counter_and_instruction_guard);
    RUN_TEST(tick_stall_reset_and_identity_changes_drop_validity);
    RUN_TEST(tick_wraps_without_regressing_and_clear_removes_baseline);
    return 0;
}
