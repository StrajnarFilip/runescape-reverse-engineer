/* Overhead prayer flags, raw NPC animations, scene epochs and NPC action events. */
#include "fixture.h"

/* Fake address of the four raw prayer variable nodes. */
#define PRAYER_VARIABLE_NODES 0xb00000ull
#define CURSES_VALUE          (PRAYER_VARIABLE_NODES + FIXTURE_VARIABLE_NODE_BYTES + 8)
#define CURSES_DEFLECT_MAGIC  (1u << 7)
#define CURSES_DEFLECT_MELEE  (1u << 9)
#define CURSES_SOUL_SPLIT     (1u << 18)
#define TELOS_STYLE_SEQUENCE  28929

static void add_prayer_variables(fixture *test_fixture)
{
    static const uint32_t prayer_variable_ids[4] = {3272, 3275, 3273, 5859};
    fixture_add_block(test_fixture, PRAYER_VARIABLE_NODES, 4 * FIXTURE_VARIABLE_NODE_BYTES);
    for (unsigned index = 0; index < 4; index++)
    {
        fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE,
                             PRAYER_VARIABLE_NODES + index * FIXTURE_VARIABLE_NODE_BYTES,
                             prayer_variable_ids[index], 0);
    }
}

static void decodes_overheads_and_animations(void)
{
    fixture *test_fixture = fixture_create();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    add_prayer_variables(test_fixture);
    fixture_put_u32(test_fixture, CURSES_VALUE, CURSES_DEFLECT_MAGIC);
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_ANIMATION_COMPONENT_OFFSET,
                    FIXTURE_MODULE_BASE + profile_primary.npc_animation_vtable_rva);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_ACTION_SEQUENCE_OFFSET,
                    TELOS_STYLE_SEQUENCE);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_MOVEMENT_SEQUENCE_OFFSET,
                    (uint32_t)-1);
    assert(fixture_decode(test_fixture));

    rs3_combat_state combat;
    assert(rs3_snapshot_combat(snapshot, &combat, sizeof combat) == RS3_OK);
    assert(combat.raw_valid_mask == 15);
    assert(combat.known_mask == RS3_PRAYER_OVERHEADS);
    assert(rs3_combat_active_prayer(&combat) == RS3_PRAYER_MAGIC);
    assert(rs3_combat_sizeof(RS3_COMBAT_SIZE_STATE) == sizeof combat);
    assert(rs3_combat_sizeof(RS3_COMBAT_SIZE_ANIMATION) == sizeof(rs3_npc_animation));

    rs3_npc_animation animation;
    uint32_t count;
    assert(rs3_snapshot_npc_animations(snapshot, NULL, 0, &count) == RS3_E_BUFFER && count == 1);
    assert(rs3_snapshot_npc_animations(snapshot, &animation, 1, &count) == RS3_OK);
    assert(animation.valid && animation.action_sequence == TELOS_STYLE_SEQUENCE);
    assert(animation.movement_sequence == -1);
    assert(animation.instance_id == snapshot->npcs[0].instance_id);
    uint64_t first_epoch = combat.scene_epoch;

    fixture_put_u32(test_fixture, CURSES_VALUE, CURSES_SOUL_SPLIT);
    assert(fixture_decode(test_fixture));
    assert(rs3_combat_active_prayer(&snapshot->combat) == RS3_PRAYER_SOUL_SPLIT);
    fixture_put_u32(test_fixture, CURSES_VALUE, 0);
    assert(fixture_decode(test_fixture));
    assert(rs3_combat_active_prayer(&snapshot->combat) == RS3_PRAYER_NONE);

    /* Two incompatible active flags are not a trustworthy overhead state. */
    fixture_put_u32(test_fixture, CURSES_VALUE, CURSES_DEFLECT_MAGIC | CURSES_DEFLECT_MELEE);
    assert(fixture_decode(test_fixture));
    assert(rs3_combat_active_prayer(&snapshot->combat) == RS3_PRAYER_UNKNOWN);

    /* Sparse absence is not proof that every overhead is off. */
    fixture_put_u64(test_fixture, FIXTURE_PLAYER_TABLE + (3275 % FIXTURE_BUCKET_COUNT) * 8,
                    FIXTURE_SENTINEL);
    assert(fixture_decode(test_fixture));
    assert(rs3_combat_active_prayer(&snapshot->combat) == RS3_PRAYER_UNKNOWN);

    /* A new world state starts a new scene epoch; leaving the game clears it. */
    test_fixture->context->combat.world_state = 0;
    assert(fixture_decode(test_fixture));
    assert(snapshot->combat.scene_epoch > first_epoch);
    fixture_put_u32(test_fixture, FIXTURE_WORLD + LAYOUT_GAME_STATE_OFFSET, 10);
    assert(fixture_decode(test_fixture));
    assert(snapshot->combat.scene_epoch == 0 && snapshot->combat.active_mask == 0);
    assert(rs3_combat_active_prayer(&snapshot->combat) == RS3_PRAYER_UNKNOWN);

    /* A missing animation component invalidates the animation, not the NPC. */
    fixture_put_u32(test_fixture, FIXTURE_WORLD + LAYOUT_GAME_STATE_OFFSET, LAYOUT_IN_GAME_STATE);
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_ANIMATION_COMPONENT_OFFSET, 0);
    assert(fixture_decode(test_fixture));
    assert(!snapshot->npc_animations[0].valid && (snapshot->npcs[0].flags & RS3_NPC_HEALTH));
    fixture_destroy(test_fixture);

    /* A profile without a verified animation layout reports an unsupported client. */
    client_profile without_animation = profile_compatibility;
    without_animation.has_position = 0;
    without_animation.npc_animation_vtable_rva = 0;
    test_fixture = fixture_create_for_profile(&without_animation);
    assert(fixture_decode(test_fixture));
    assert(!test_fixture->snapshot->npc_animations[0].valid);
    assert(test_fixture->snapshot->npc_animations[0].reason == RS3_UNSUPPORTED_CLIENT);
    fixture_destroy(test_fixture);
}

static uint64_t action_event_sequence(const fixture *test_fixture)
{
    return test_fixture->context->npc_action_event_sequence;
}

static void reports_applied_actions_with_stamps(void)
{
    client_profile stamped_profile = profile_compatibility;
    stamped_profile.has_position = 0;
    fixture *test_fixture = fixture_create_for_profile(&stamped_profile);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    const uint64_t action = FIXTURE_ACTOR + LAYOUT_NPC_ACTION_SEQUENCE_OFFSET;
    const uint64_t stamp = FIXTURE_ACTOR + stamped_profile.npc_action_stamp_offset;
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_ANIMATION_COMPONENT_OFFSET,
                    FIXTURE_MODULE_BASE + stamped_profile.npc_animation_vtable_rva);
    fixture_put_u32(test_fixture, action, 100);
    fixture_put_u32(test_fixture, stamp, 1003);
    assert(fixture_decode(test_fixture) && snapshot->npc_animations[0].valid);
    assert(action_event_sequence(test_fixture) == 0);

    /* A new stamp is an application even when the sequence ID repeats. */
    fixture_put_u32(test_fixture, stamp, 1004);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 1);
    rs3_npc_action_event event;
    uint32_t written;
    uint64_t next;
    uint64_t lost;
    assert(rs3_combat_sizeof(RS3_COMBAT_SIZE_ACTION_EVENT) == sizeof event);
    assert(rs3_npc_action_events_since(test_fixture->context, 0, &event, 1, &written, &next,
                                       &lost) == RS3_OK);
    assert(written == 1 && next == 1 && lost == 0 && event.sequence == 1);
    assert(event.action_sequence == 100 && event.previous_action_sequence == 100);
    assert(event.application_stamp == 1004);
    assert(event.flags == RS3_NPC_ACTION_HAS_APPLICATION_STAMP);
    assert(event.sample_sequence == snapshot->info.sequence);
    assert(event.scene_epoch == snapshot->combat.scene_epoch);
    assert(event.instance_id == snapshot->npcs[0].instance_id);
    assert(event.pid == FIXTURE_PID &&
           event.process_creation_time == FIXTURE_PROCESS_CREATION_TIME);
    assert(event.observed_ns >= snapshot->info.monotonic_ns);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 1);

    /* A torn stamp suppresses the event and forces a fresh baseline. */
    fixture_mutate_on_read(test_fixture, stamp, 1, stamp, 1005);
    assert(fixture_decode(test_fixture) && !snapshot->npc_animations[0].valid);
    assert(action_event_sequence(test_fixture) == 1);
    fixture_clear_mutation(test_fixture);
    assert(fixture_decode(test_fixture) && snapshot->npc_animations[0].valid);
    assert(action_event_sequence(test_fixture) == 1);

    /* Observing the ID write before the stamp write still yields one event. */
    fixture_put_u32(test_fixture, action, 101);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 1);
    fixture_put_u32(test_fixture, stamp, 1006);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 2);

    /* A skipped sample or a new scene leaves no trustworthy edge. */
    ++test_fixture->context->sample_sequence;
    fixture_put_u32(test_fixture, stamp, 1007);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 2);
    test_fixture->context->combat.world_state = 0;
    fixture_put_u32(test_fixture, stamp, 1008);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 2);

    /* Queue overflow is reported to cursors behind the oldest retained event. */
    test_fixture->context->npc_action_event_sequence = NPC_ACTION_EVENT_CAPACITY;
    fixture_put_u32(test_fixture, stamp, 1009);
    assert(fixture_decode(test_fixture));
    assert(rs3_npc_action_events_since(test_fixture->context, 0, NULL, 0, &written, &next, &lost) ==
           RS3_OK);
    assert(written == 0 && lost == 1 && next == 1);
    assert(rs3_npc_action_events_since(test_fixture->context, NPC_ACTION_EVENT_CAPACITY, &event, 1,
                                       &written, &next, &lost) == RS3_OK);
    assert(written == 1 && lost == 0 && next == NPC_ACTION_EVENT_CAPACITY + 1);
    assert(event.sequence == next);

    /* A scene that changes during the read has no epoch and emits nothing. */
    uint64_t game_state = FIXTURE_WORLD + LAYOUT_GAME_STATE_OFFSET;
    fixture_mutate_on_read(test_fixture, game_state, 2, game_state, 10);
    fixture_put_u32(test_fixture, stamp, 1010);
    assert(fixture_decode(test_fixture) && snapshot->combat.scene_epoch == 0);
    assert(action_event_sequence(test_fixture) == next);
    fixture_destroy(test_fixture);
}

static void reports_sequence_changes_without_stamps(void)
{
    fixture *test_fixture = fixture_create();
    const uint64_t action = FIXTURE_ACTOR + LAYOUT_NPC_ACTION_SEQUENCE_OFFSET;
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_ANIMATION_COMPONENT_OFFSET,
                    FIXTURE_MODULE_BASE + profile_primary.npc_animation_vtable_rva);
    fixture_put_u32(test_fixture, action, (uint32_t)-1);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 0);
    fixture_put_u32(test_fixture, action, 200);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 1);

    rs3_npc_action_event event;
    uint32_t written;
    uint64_t next;
    uint64_t lost;
    assert(rs3_npc_action_events_since(test_fixture->context, 0, &event, 1, &written, &next,
                                       &lost) == RS3_OK);
    assert(written == 1 && event.action_sequence == 200);
    assert(event.previous_action_sequence == -1 && event.flags == 0);

    /* Returning to idle is not an action application. */
    fixture_put_u32(test_fixture, action, (uint32_t)-1);
    assert(fixture_decode(test_fixture) && action_event_sequence(test_fixture) == 1);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(decodes_overheads_and_animations);
    RUN_TEST(reports_applied_actions_with_stamps);
    RUN_TEST(reports_sequence_changes_without_stamps);
    puts("PASS: combat tests");
    return 0;
}
