/* Effect observations must never infer safety from missing or torn candidates. */
#include "fixture.h"

#include <string.h>

#include "effects.h"

#define EFFECT_NODES    0xb00000ull
#define DEVOTION_EXPIRY 3901u

static void unknown_is_not_absent(void)
{
    fixture *test_fixture = fixture_create();
    assert(fixture_decode(test_fixture));
    const rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(snapshot->effects_info.count == RS3_EFFECT_COUNT);
    assert(snapshot->effects_info.complete == 0);
    for (unsigned index = 0; index < RS3_EFFECT_COUNT; index++)
    {
        const rs3_effect *effect = &snapshot->effects[index];
        assert(effect->effect_id == index + 1);
        assert(effect->flags == 0 && effect->active == -1);
        assert(effect->stacks == -1 && effect->remaining_ms == -1);
    }
    assert(snapshot->effects[RS3_EFFECT_ANIMA_VIRUS - 1].reason == RS3_EFFECT_UNMAPPED);
    assert(snapshot->effects[RS3_EFFECT_DEVOTION - 1].reason == RS3_EFFECT_NOT_SET);
    fixture_destroy(test_fixture);
}

static void raw_changes_are_not_buff_proof(void)
{
    fixture *test_fixture = fixture_create();
    fixture_add_block(test_fixture, EFFECT_NODES, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_CLIENT_TABLE, EFFECT_NODES, DEVOTION_EXPIRY, 1300);
    assert(fixture_decode(test_fixture));
    rs3_effect *effect = &test_fixture->snapshot->effects[RS3_EFFECT_DEVOTION - 1];
    assert(effect->raw_valid_mask == 1 && effect->raw_expiry == 1300);
    assert(effect->reason == RS3_EFFECT_UNVALIDATED);
    assert(effect->flags == 0 && effect->remaining_ms == -1);
    /* Zero is an observed candidate, not validated buff absence. */
    fixture_put_u32(test_fixture, EFFECT_NODES + 8, 0);
    assert(fixture_decode(test_fixture));
    assert(effect->raw_valid_mask == 1 && effect->raw_expiry == 0);
    assert(effect->active == -1);
    fixture_destroy(test_fixture);
}

static void torn_values_invalidate_the_observation(void)
{
    fixture *test_fixture = fixture_create();
    fixture_add_block(test_fixture, EFFECT_NODES, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_CLIENT_TABLE, EFFECT_NODES, DEVOTION_EXPIRY, 1300);
    fixture_mutate_on_read(test_fixture, EFFECT_NODES, 2, EFFECT_NODES + 8, 1400);
    effects_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    const rs3_effect *effect = &test_fixture->snapshot->effects[RS3_EFFECT_DEVOTION - 1];
    assert(effect->raw_valid_mask == 0 && effect->reason == RS3_EFFECT_READ_FAILED);
    fixture_destroy(test_fixture);
}

static void profile_and_frame_invalidation(void)
{
    fixture *test_fixture = fixture_create_for_profile(&profile_compatibility);
    effects_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(test_fixture->snapshot->effects[RS3_EFFECT_DEVOTION - 1].reason ==
           RS3_EFFECT_UNSUPPORTED_PROFILE);
    effects_clear(test_fixture->snapshot);
    assert(test_fixture->snapshot->effects_info.count == 0);
    assert(test_fixture->snapshot->effects_info.status == RS3_UNAVAILABLE);
    assert(test_fixture->snapshot->effects[RS3_EFFECT_DEVOTION - 1].raw_valid_mask == 0);
    fixture_destroy(test_fixture);
}

static void expiry_does_not_prove_presence(void)
{
    fixture *test_fixture = fixture_create();
    fixture_add_block(test_fixture, EFFECT_NODES, FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_CLIENT_TABLE, EFFECT_NODES, 3732,
                         FIXTURE_LOGIC_TICK + 50);
    assert(fixture_decode(test_fixture));
    const rs3_effect *effect = &test_fixture->snapshot->effects[RS3_EFFECT_OVERLOAD - 1];
    assert(effect->remaining_ms == 1000 && effect->flags == RS3_EFFECT_REMAINING_KNOWN);
    assert(effect->active == -1 && effect->stacks == -1);
    assert(effect->reason == RS3_EFFECT_PRESENCE_UNMAPPED);
    fixture_put_u32(test_fixture, EFFECT_NODES + 8, FIXTURE_LOGIC_TICK - 1);
    assert(fixture_decode(test_fixture));
    assert(effect->remaining_ms == 0 && effect->active == -1);
    /* Reject a future endpoint outside the bounded clock interval. */
    fixture_put_u32(test_fixture, EFFECT_NODES + 8, FIXTURE_LOGIC_TICK + 180001);
    assert(fixture_decode(test_fixture));
    assert(effect->remaining_ms == -1 && effect->flags == 0);
    fixture_destroy(test_fixture);
}

static void caller_owned_accessors(void)
{
    fixture *test_fixture = fixture_create();
    assert(fixture_decode(test_fixture));
    rs3_effect output[RS3_EFFECT_COUNT];
    memset(output, 0x5a, sizeof output);
    uint32_t required = 0;
    assert(rs3_snapshot_effects(test_fixture->snapshot, output, 1, &required) == RS3_E_BUFFER);
    assert(required == RS3_EFFECT_COUNT && output[0].effect_id == 0x5a5a5a5a);
    assert(rs3_snapshot_effects(test_fixture->snapshot, output, RS3_EFFECT_COUNT, &required) ==
           RS3_OK);
    assert(output[0].effect_id == RS3_EFFECT_BERSERK);
    output[0].effect_id = 999;
    assert(test_fixture->snapshot->effects[0].effect_id == RS3_EFFECT_BERSERK);
    rs3_effects_info info;
    assert(rs3_snapshot_effects_info(test_fixture->snapshot, &info, sizeof info - 1) == RS3_E_ABI);
    assert(rs3_snapshot_effects(NULL, output, RS3_EFFECT_COUNT, &required) == RS3_E_ARGUMENT);
    assert(rs3_effects_sizeof(1) == sizeof info);
    assert(rs3_effects_sizeof(2) == sizeof output[0]);
    assert(rs3_effects_sizeof(999) == 0);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(unknown_is_not_absent);
    RUN_TEST(raw_changes_are_not_buff_proof);
    RUN_TEST(torn_values_invalidate_the_observation);
    RUN_TEST(profile_and_frame_invalidation);
    RUN_TEST(expiry_does_not_prove_presence);
    RUN_TEST(caller_owned_accessors);
    return 0;
}
