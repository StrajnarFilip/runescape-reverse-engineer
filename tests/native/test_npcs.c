/* NPC health, hit marks, torn reads, registry transitions and the hit event queue. */
#include "fixture.h"

#include <string.h>

#include "decode_utils.h"
#include "npcs.h"

static void decodes_health_and_hit_states(void)
{
    fixture *test_fixture = fixture_create();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    rs3_context *context = test_fixture->context;
    assert(fixture_decode(test_fixture));
    assert(snapshot->info.npc_count == 1 && snapshot->info.hit_count == 1);
    assert(snapshot->npcs[0].health_current == 326 && snapshot->hits[0].amount == 224);
    assert(strcmp(snapshot->npcs[0].name, "Goblin") == 0);
    assert(context->hit_event_sequence == 1);

    /* The same ring record is reported once. */
    assert(fixture_decode(test_fixture));
    assert(context->hit_event_sequence == 1);

    /* Maximum health 0 means not transmitted; current 0 with a positive maximum is valid. */
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_HEALTH_MAX_OFFSET, 0);
    assert(fixture_decode(test_fixture));
    assert(!(snapshot->npcs[0].flags & RS3_NPC_HEALTH));
    assert(snapshot->npcs[0].health_reason == RS3_NOT_TRANSMITTED);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_HEALTH_MAX_OFFSET, 550);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_HEALTH_CURRENT_OFFSET, 0);
    assert(fixture_decode(test_fixture));
    assert(snapshot->npcs[0].flags & RS3_NPC_HEALTH);

    /* Scheduled, expired and wrapped hit records. */
    rs3_hit hit;
    unsigned char record[HIT_RECORD_BYTES];
    memcpy(record, fixture_bytes(test_fixture, FIXTURE_HIT_RECORD, sizeof record), sizeof record);
    assert(npc_decode_hit(record, 999, 0, &hit) == HIT_DECODED);
    assert(hit.state == RS3_HIT_SCHEDULED);
    assert(npc_decode_hit(record, 1060, 0, &hit) == HIT_ABSENT);
    fixture_put_u32(test_fixture, FIXTURE_HIT_RECORD + 8, 48);
    memcpy(record, fixture_bytes(test_fixture, FIXTURE_HIT_RECORD, sizeof record), sizeof record);
    assert(npc_decode_hit(record, UINT32_MAX, 0, &hit) == HIT_DECODED);
    assert(hit.state == RS3_HIT_ACTIVE);
    assert(tick_difference(48, UINT32_MAX) == 49);
    fixture_destroy(test_fixture);
}

static void rejects_torn_reads_and_resets_on_transitions(void)
{
    /* A torn ring is skipped and cannot produce a false event. */
    fixture *test_fixture = fixture_create();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    fixture_mutate_on_read(test_fixture, FIXTURE_HIT_RECORD, 2, FIXTURE_HIT_RECORD + 4, 550);
    assert(fixture_decode(test_fixture));
    assert(snapshot->info.skipped_npcs == 1 && test_fixture->context->hit_event_sequence == 0);
    fixture_clear_mutation(test_fixture);
    assert(fixture_decode(test_fixture));
    assert(test_fixture->context->hit_event_sequence == 1);

    /* A despawn and respawn creates a new instance and reports its hit again. */
    uint64_t first_instance = snapshot->npcs[0].instance_id;
    fixture_put_u32(test_fixture, FIXTURE_NPC_MANAGER + LAYOUT_NPC_COUNT_OFFSET, 0);
    assert(fixture_decode(test_fixture));
    assert(snapshot->info.npc_count == 0);
    fixture_put_u32(test_fixture, FIXTURE_NPC_MANAGER + LAYOUT_NPC_COUNT_OFFSET, 1);
    assert(fixture_decode(test_fixture));
    assert(snapshot->npcs[0].instance_id != first_instance);
    assert(test_fixture->context->hit_event_sequence == 2);
    fixture_destroy(test_fixture);

    /* A world change after the NPC reads must not commit events or identities. */
    test_fixture = fixture_create();
    uint64_t world_pointer = FIXTURE_CLIENT + LAYOUT_STATE_POINTER_OFFSET;
    fixture_mutate_on_read(test_fixture, world_pointer, 2, world_pointer, 0);
    assert(!fixture_decode(test_fixture));
    assert(test_fixture->context->hit_event_sequence == 0);
    fixture_put_u64(test_fixture, world_pointer, FIXTURE_WORLD);
    fixture_clear_mutation(test_fixture);
    assert(fixture_decode(test_fixture));
    assert(test_fixture->context->hit_event_sequence == 1);
    fixture_destroy(test_fixture);

    /* A list that changes during the read is retried once and published once. */
    test_fixture = fixture_create();
    uint64_t npc_count = FIXTURE_NPC_MANAGER + LAYOUT_NPC_COUNT_OFFSET;
    fixture_mutate_on_read(test_fixture, npc_count, 2, npc_count, 0);
    assert(fixture_decode(test_fixture));
    assert(test_fixture->snapshot->info.npc_count == 0);
    assert(test_fixture->context->hit_event_sequence == 0);
    fixture_destroy(test_fixture);
}

static void reports_buffers_and_independent_event_cursors(void)
{
    fixture *test_fixture = fixture_create();
    rs3_context *context = test_fixture->context;
    assert(fixture_decode(test_fixture));

    uint32_t count;
    uint64_t next;
    uint64_t lost;
    rs3_event event;
    assert(rs3_snapshot_npcs(test_fixture->snapshot, NULL, 0, &count) == RS3_E_BUFFER);
    assert(count == 1);
    assert(rs3_snapshot_info(test_fixture->snapshot, (rs3_info *)&event, 4) == RS3_E_ABI);
    assert(rs3_events_since(context, 0, &event, 1, &count, &next, &lost) == RS3_OK);
    assert(count == 1 && next == 1 && lost == 0);
    assert(event.hit.amount == 224 && event.pid == FIXTURE_PID);
    assert(event.observed_at_ns == FIXTURE_TIMESTAMP_NS);

    /* A second consumer has an independent cursor. */
    assert(rs3_events_since(context, 0, &event, 1, &count, &next, &lost) == RS3_OK);
    assert(count == 1);
    assert(rs3_events_since(context, 1, &event, 1, &count, &next, &lost) == RS3_OK);
    assert(count == 0);

    /* Overflowing the queue reports the overwritten sequences explicitly. */
    for (unsigned sample = 0; sample < HIT_EVENT_CAPACITY + 2; sample++)
    {
        fixture_put_u32(test_fixture, FIXTURE_HIT_RECORD + 8, 1061 + sample);
        fixture_put_u32(test_fixture, FIXTURE_WORLD + LAYOUT_LOGIC_CLOCK_OFFSET, 1051 + sample);
        assert(fixture_decode(test_fixture));
    }
    assert(rs3_events_since(context, 0, &event, 1, &count, &next, &lost) == RS3_OK);
    assert(lost == 3 && count == 1 && next == 4 && event.sequence == 4);
    assert(rs3_events_since(context, UINT64_MAX, &event, 1, &count, &next, &lost) ==
           RS3_E_ARGUMENT);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(decodes_health_and_hit_states);
    RUN_TEST(rejects_torn_reads_and_resets_on_transitions);
    RUN_TEST(reports_buffers_and_independent_event_cursors);
    puts("PASS: NPC tests");
    return 0;
}
