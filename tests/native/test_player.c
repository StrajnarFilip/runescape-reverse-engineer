/* Player variables, sparse tables, profile selection and registry validation. */
#include "fixture.h"

#include <string.h>

#include "process.h"
#include "variables.h"

static void decodes_player_values_and_sparse_variables(void)
{
    fixture *test_fixture = fixture_create();
    rs3_player *player = &test_fixture->snapshot->player;
    assert(fixture_decode(test_fixture));
    assert(player->raw_variables[RS3_HEALTH] == 8700);
    assert(player->raw_variables[RS3_PRAYER_PACKED] == 7635);
    assert(player->gcd_remaining_ms == 1700);
    assert(player->valid_variables == (1u << RS3_VARIABLE_COUNT) - 1);

    /* A sparse UINTPTR_MAX sentinel is not followed or interpreted as zero. */
    uint64_t adrenaline_bucket =
        FIXTURE_PLAYER_TABLE + (profile_variable_ids[RS3_ADRENALINE] % FIXTURE_BUCKET_COUNT) * 8;
    fixture_put_u64(test_fixture, adrenaline_bucket, FIXTURE_SENTINEL);
    assert(fixture_decode(test_fixture));
    assert(!(player->valid_variables & (1u << RS3_ADRENALINE)));

    /* Numeric int64 variants are supported, including their sign. */
    uint64_t health_node = FIXTURE_VARIABLE_NODES + RS3_HEALTH * FIXTURE_VARIABLE_NODE_BYTES;
    fixture_put_u64(test_fixture, health_node + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, (uint64_t)-1);
    *fixture_bytes(test_fixture, health_node + LAYOUT_VARIABLE_NODE_TYPE_OFFSET, 1) =
        VARIABLE_TYPE_INT64;
    variable_table table = {FIXTURE_PLAYER_TABLE, FIXTURE_BUCKET_COUNT, FIXTURE_SENTINEL};
    int64_t value = 0;
    int present = 0;
    assert(variable_table_find(test_fixture->context, &table, profile_variable_ids[RS3_HEALTH],
                               &value, &present));
    assert(present && value == -1);
    fixture_destroy(test_fixture);
}

static void rejects_unknown_profiles_cycles_and_mismatched_contexts(void)
{
    fixture *test_fixture = fixture_create();
    rs3_context *context = test_fixture->context;
    uint64_t read_calls_before = context->read_call_count;
    assert(!process_select_profile(context, "bad hash"));
    assert(context->read_call_count == read_calls_before);

    /* A node that links to itself is a cycle, not an endless chain. */
    uint64_t health_node = FIXTURE_VARIABLE_NODES + RS3_HEALTH * FIXTURE_VARIABLE_NODE_BYTES;
    fixture_put_u32(test_fixture, health_node + LAYOUT_VARIABLE_NODE_KEY_OFFSET, 999);
    fixture_put_u64(test_fixture, health_node + LAYOUT_VARIABLE_NODE_NEXT_OFFSET, health_node);
    variable_table table = {FIXTURE_PLAYER_TABLE, FIXTURE_BUCKET_COUNT, FIXTURE_SENTINEL};
    int64_t value;
    int present;
    assert(
        !variable_table_find(context, &table, profile_variable_ids[RS3_HEALTH], &value, &present));
    assert(strstr(context->failure_message, "cycle") != NULL);
    fixture_destroy(test_fixture);

    /* An NPC whose bucket chain loops without its ID is skipped, not fatal. */
    test_fixture = fixture_create();
    fixture_put_u32(test_fixture, FIXTURE_NPC_NODE, FIXTURE_NPC_RUNTIME_ID + 1);
    fixture_put_u64(test_fixture, FIXTURE_NPC_NODE + 24, FIXTURE_NPC_NODE);
    assert(fixture_decode(test_fixture));
    assert(test_fixture->snapshot->info.skipped_npcs == 1);
    assert(test_fixture->snapshot->info.npc_count == 0);

    /* A client context with the wrong virtual table is an unsupported client. */
    fixture_put_u64(test_fixture, FIXTURE_CLIENT, FIXTURE_MODULE_BASE);
    assert(!fixture_decode(test_fixture));
    assert(test_fixture->context->failure_reason == RS3_UNSUPPORTED_CLIENT);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(decodes_player_values_and_sparse_variables);
    RUN_TEST(rejects_unknown_profiles_cycles_and_mismatched_contexts);
    puts("PASS: player tests");
    return 0;
}
