/*
 * Local player hit marks on both profiles: actor resolution, ring validation,
 * baselines, the event queue, and the tile that shares the same actor.
 */
#include "fixture.h"

#include <string.h>

#include "npcs.h"
#include "position.h"

/* Fake addresses of the local player structures. */
#define LOCAL_CONTEXT      0xc00000ull
#define LOCAL_ACTOR        0xd00000ull
#define LOCAL_RING         0xe00000ull
#define LOCAL_RECORDS      0xe01000ull
#define LOCAL_PLAYER_TABLE 0xf00000ull
#define LOCAL_SLOTS        0xf01000ull
#define LOCAL_SLOT         0xf10000ull

#define LOCAL_PLAYER_INDEX 1455u
#define LOCAL_SLOT_COUNT   (LOCAL_PLAYER_INDEX + 1u)
#define SLOT_POINTER_BYTES 8u
#define OTHER_PLAYER_INDEX 1u

/* Hit ring header and record layout shared with the NPC rings. */
#define RING_HEADER_BYTES     40u
#define RING_CAPACITY_OFFSET  4u
#define RING_COUNT_OFFSET     24u
#define RING_DATA_OFFSET      32u
#define RECORD_AMOUNT_OFFSET  4u
#define RING_RECORDS_CAPACITY 2u

#define FIRST_HIT_AMOUNT  977
#define SECOND_HIT_AMOUNT 321
#define LATE_HIT_AMOUNT   400

/* Tile of the local actor: one tile wide, centred on its tile. */
#define LOCAL_TILE_X        3297
#define LOCAL_TILE_Y        10132
#define LOCAL_PLANE         1u
#define LOCAL_FOOTPRINT     1u
#define FINE_UNITS_PER_TILE 512.0f
#define TILE_CENTRE         0.5f
#define LOCAL_HEIGHT        1234.0f

/** combat_layout.player_actor_vtable_rva in profiles/profile_6ada3f59.json. */
#define COMPATIBILITY_PLAYER_ACTOR_VTABLE_RVA 0x150d8c8u
/** A vtable that belongs to no player actor. */
#define FOREIGN_VTABLE_RVA 0x10u

/** Returns the shipped Compatibility Mode profile after checking that it pins the player actor. */
static const client_profile *compatibility_profile(void)
{
    assert(profile_compatibility.player_actor_vtable_rva == COMPATIBILITY_PLAYER_ACTOR_VTABLE_RVA);
    return &profile_compatibility;
}

/** Writes the fake actor's tile fields at the profile-independent actor layout. */
static void put_local_tile(fixture *test_fixture, int32_t tile_x, int32_t tile_y)
{
    float fine_coordinates[3] = {(tile_x + TILE_CENTRE) * FINE_UNITS_PER_TILE, LOCAL_HEIGHT,
                                 (tile_y + TILE_CENTRE) * FINE_UNITS_PER_TILE};
    fixture_put_u32(test_fixture, LOCAL_ACTOR + POSITION_PLANE_OFFSET, LOCAL_PLANE);
    fixture_put_u32(test_fixture, LOCAL_ACTOR + POSITION_SIZE_OFFSET, LOCAL_FOOTPRINT);
    memcpy(fixture_bytes(test_fixture, LOCAL_ACTOR + POSITION_FINE_COORDINATES_OFFSET,
                         sizeof fine_coordinates),
           fine_coordinates, sizeof fine_coordinates);
}

/**
 * Maps the player context, actor, ring and one active record and returns the
 * fixture. On a profile with a player tile, the scene global names the world
 * state, as the live Compatibility Mode client does.
 */
static fixture *create_local_player_fixture(const client_profile *profile)
{
    fixture *test_fixture = fixture_create_for_profile(profile);
    fixture_add_block(test_fixture, LOCAL_CONTEXT, LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET + 8);
    fixture_add_block(test_fixture, LOCAL_ACTOR, LAYOUT_LOCAL_ACTOR_HITMARKS_OFFSET + 8);
    fixture_add_block(test_fixture, LOCAL_RING, RING_HEADER_BYTES);
    fixture_add_block(test_fixture, LOCAL_RECORDS, RING_RECORDS_CAPACITY * HIT_RECORD_BYTES);
    if (profile->has_position)
    {
        fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA, 8);
        fixture_put_u64(test_fixture, FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA,
                        FIXTURE_WORLD);
    }

    fixture_put_u64(test_fixture, FIXTURE_WORLD + LAYOUT_LOCAL_ACTOR_CONTEXT_OFFSET, LOCAL_CONTEXT);
    fixture_put_u64(test_fixture, LOCAL_CONTEXT, FIXTURE_WORLD);
    fixture_put_u32(test_fixture, LOCAL_CONTEXT + LAYOUT_LOCAL_ACTOR_INDEX_OFFSET,
                    LOCAL_PLAYER_INDEX);
    fixture_put_u64(test_fixture, LOCAL_CONTEXT + LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET, LOCAL_ACTOR);

    fixture_put_u64(test_fixture, LOCAL_ACTOR,
                    FIXTURE_MODULE_BASE + profile->player_actor_vtable_rva);
    fixture_put_u32(test_fixture, LOCAL_ACTOR + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET,
                    LOCAL_PLAYER_INDEX);
    fixture_put_u64(test_fixture, LOCAL_ACTOR + LAYOUT_LOCAL_ACTOR_HITMARKS_OFFSET, LOCAL_RING);
    put_local_tile(test_fixture, LOCAL_TILE_X, LOCAL_TILE_Y);

    fixture_put_u32(test_fixture, LOCAL_RING + RING_CAPACITY_OFFSET, 1);
    fixture_put_u64(test_fixture, LOCAL_RING + RING_COUNT_OFFSET, 1);
    fixture_put_u64(test_fixture, LOCAL_RING + RING_DATA_OFFSET, LOCAL_RECORDS);
    /* Type, amount, end tick, secondary type, secondary amount and duration. */
    int32_t hit_record[6] = {133, FIRST_HIT_AMOUNT, 1060, -1, -1, 60};
    memcpy(fixture_bytes(test_fixture, LOCAL_RECORDS, sizeof hit_record), hit_record,
           sizeof hit_record);
    return test_fixture;
}

static uint64_t player_hit_event_sequence(const fixture *test_fixture)
{
    return test_fixture->context->player_hit_event_sequence;
}

static void reports_new_hits_after_a_baseline(const client_profile *profile)
{
    fixture *test_fixture = create_local_player_fixture(profile);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_AVAILABLE);
    assert(snapshot->player_hit_info.hit_count == 1);
    assert(snapshot->player_hits[0].amount == FIRST_HIT_AMOUNT);
    /* Hits already active at attachment are a baseline, not events. */
    assert(player_hit_event_sequence(test_fixture) == 0);

    rs3_player_hit_info info;
    uint32_t required;
    assert(rs3_combat_sizeof(RS3_COMBAT_SIZE_PLAYER_HIT_INFO) == sizeof info);
    assert(rs3_combat_sizeof(RS3_COMBAT_SIZE_PLAYER_HIT_EVENT) == sizeof(rs3_player_hit_event));
    assert(rs3_snapshot_player_hit_info(snapshot, &info, sizeof info) == RS3_OK);
    assert(info.player_index == LOCAL_PLAYER_INDEX);
    assert(rs3_snapshot_player_hits(snapshot, NULL, 0, &required) == RS3_E_BUFFER && required == 1);

    fixture_put_u32(test_fixture, LOCAL_RECORDS + RECORD_AMOUNT_OFFSET, SECOND_HIT_AMOUNT);
    assert(fixture_decode(test_fixture));
    rs3_player_hit_event event;
    uint32_t written;
    uint64_t next;
    uint64_t lost;
    assert(rs3_player_hit_events_since(test_fixture->context, 0, &event, 1, &written, &next,
                                       &lost) == RS3_OK);
    assert(written == 1 && next == 1 && lost == 0);
    assert(event.hit.amount == SECOND_HIT_AMOUNT && event.player_index == LOCAL_PLAYER_INDEX);
    assert(event.scene_epoch == snapshot->combat.scene_epoch);
    assert(event.instance_id == info.instance_id);
    assert(event.pid == FIXTURE_PID &&
           event.process_creation_time == FIXTURE_PROCESS_CREATION_TIME);

    /* The same record in the same slot is reported once. */
    assert(fixture_decode(test_fixture));
    assert(player_hit_event_sequence(test_fixture) == 1);
    fixture_destroy(test_fixture);
}

static void optional_channel_failures_keep_the_frame(const client_profile *profile)
{
    fixture *test_fixture = create_local_player_fixture(profile);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    const uint64_t actor_runtime_id = LOCAL_ACTOR + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET;
    assert(fixture_decode(test_fixture));
    assert(fixture_decode(test_fixture));

    /* A bad local ring is optional; NPCs and the player fields stay available. */
    fixture_put_u64(test_fixture, LOCAL_RING + RING_COUNT_OFFSET, 2);
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_UNAVAILABLE);
    assert(snapshot->player_hit_info.hit_count == 0);
    assert(snapshot->player.valid_variables && snapshot->info.npc_count == 1);

    /* Re-observing an active hit after a gap cannot assign it a fresh hit time. */
    fixture_put_u64(test_fixture, LOCAL_RING + RING_COUNT_OFFSET, 1);
    fixture_put_u32(test_fixture, LOCAL_RECORDS + RECORD_AMOUNT_OFFSET, LATE_HIT_AMOUNT);
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_AVAILABLE);
    assert(player_hit_event_sequence(test_fixture) == 0);

    /* An actor that carries a different player index is not the local player. */
    fixture_put_u32(test_fixture, actor_runtime_id, OTHER_PLAYER_INDEX);
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_UNAVAILABLE);
    fixture_put_u32(test_fixture, actor_runtime_id, LOCAL_PLAYER_INDEX);
    fixture_destroy(test_fixture);
}

static void resolves_the_actor_through_the_player_table(const client_profile *profile)
{
    fixture *test_fixture = create_local_player_fixture(profile);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    const uint64_t actor_runtime_id = LOCAL_ACTOR + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET;
    const uint64_t slot_range = LOCAL_PLAYER_TABLE + LAYOUT_LOCAL_ACTOR_RANGE_OFFSET;
    fixture_add_block(test_fixture, LOCAL_PLAYER_TABLE, LAYOUT_LOCAL_ACTOR_RANGE_OFFSET + 16);
    fixture_add_block(test_fixture, LOCAL_SLOTS, LOCAL_SLOT_COUNT * SLOT_POINTER_BYTES);
    fixture_add_block(test_fixture, LOCAL_SLOT, LAYOUT_LOCAL_ACTOR_SLOT_ENTITY_OFFSET + 8);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + LAYOUT_LOCAL_ACTOR_TABLE_OFFSET,
                    LOCAL_PLAYER_TABLE);
    fixture_put_u64(test_fixture, slot_range, LOCAL_SLOTS);
    fixture_put_u64(test_fixture, slot_range + SLOT_POINTER_BYTES,
                    LOCAL_SLOTS + LOCAL_SLOT_COUNT * SLOT_POINTER_BYTES);
    fixture_put_u64(test_fixture, LOCAL_SLOTS + LOCAL_PLAYER_INDEX * SLOT_POINTER_BYTES,
                    LOCAL_SLOT);
    fixture_put_u64(test_fixture, LOCAL_SLOT + LAYOUT_LOCAL_ACTOR_SLOT_ENTITY_OFFSET, LOCAL_ACTOR);
    fixture_put_u64(test_fixture, LOCAL_CONTEXT + LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET, 0);
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_AVAILABLE);
    assert(snapshot->player_hit_info.player_index == LOCAL_PLAYER_INDEX);

    /* An actor that changes identity during the read is rejected. */
    fixture_mutate_on_read(test_fixture, actor_runtime_id, 2, actor_runtime_id, OTHER_PLAYER_INDEX);
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_UNAVAILABLE);
    fixture_clear_mutation(test_fixture);
    fixture_put_u32(test_fixture, actor_runtime_id, LOCAL_PLAYER_INDEX);

    /*
     * Leaving the game clears the channel and the scene epoch. A profile with a
     * player tile checks the scene first, so the whole frame fails there.
     */
    fixture_put_u32(test_fixture, FIXTURE_WORLD + LAYOUT_GAME_STATE_OFFSET, 10);
    assert(fixture_decode(test_fixture) == !profile->has_position);
    assert(snapshot->player_hit_info.hit_count == 0 && snapshot->combat.scene_epoch == 0);
    fixture_destroy(test_fixture);
}

static void reports_cursor_errors_and_overflow(const client_profile *profile)
{
    fixture *test_fixture = create_local_player_fixture(profile);
    assert(fixture_decode(test_fixture));
    fixture_put_u32(test_fixture, LOCAL_RECORDS + RECORD_AMOUNT_OFFSET, SECOND_HIT_AMOUNT);
    assert(fixture_decode(test_fixture));
    assert(player_hit_event_sequence(test_fixture) == 1);

    rs3_player_hit_event event;
    uint32_t written;
    uint64_t next;
    uint64_t lost;
    /* A cursor ahead of the newest event is a caller error. */
    assert(rs3_player_hit_events_since(test_fixture->context, 2, &event, 1, &written, &next,
                                       &lost) == RS3_E_ARGUMENT);

    /* Overflow is reported to cursors behind the oldest retained event. */
    test_fixture->context->player_hit_event_sequence = PLAYER_HIT_EVENT_CAPACITY + 3;
    assert(rs3_player_hit_events_since(test_fixture->context, 0, NULL, 0, &written, &next, &lost) ==
           RS3_OK);
    assert(written == 0 && lost == 3 && next == 3);
    fixture_destroy(test_fixture);
}

static void unsupported_profile_reports_its_reason(const client_profile *profile)
{
    client_profile without_actor = *profile;
    without_actor.player_actor_vtable_rva = 0;
    fixture *test_fixture = fixture_create_for_profile(&without_actor);
    if (without_actor.has_position)
    {
        fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA, 8);
        fixture_put_u64(test_fixture, FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA,
                        FIXTURE_WORLD);
    }
    assert(fixture_decode(test_fixture));
    assert(test_fixture->snapshot->player_hit_info.status == RS3_UNAVAILABLE);
    assert(test_fixture->snapshot->player_hit_info.reason == RS3_UNSUPPORTED_CLIENT);
    fixture_destroy(test_fixture);
}

static void compatibility_profile_reads_tile_and_hits_from_one_actor(void)
{
    const client_profile *profile = compatibility_profile();
    fixture *test_fixture = create_local_player_fixture(profile);
    rs3_context *context = test_fixture->context;
    rs3_snapshot *snapshot = test_fixture->snapshot;
    rs3_position tile = {0};
    assert(fixture_decode(test_fixture));
    assert(position_decode(context, &tile));
    assert(tile.valid && tile.x == LOCAL_TILE_X && tile.y == LOCAL_TILE_Y);
    assert(tile.plane == (int32_t)LOCAL_PLANE);
    assert(snapshot->player_hit_info.status == RS3_AVAILABLE);
    assert(snapshot->player_actor_address == LOCAL_ACTOR);

    /* The next sample walks one tile east and takes a new hit. */
    put_local_tile(test_fixture, LOCAL_TILE_X + 1, LOCAL_TILE_Y);
    fixture_put_u32(test_fixture, LOCAL_RECORDS + RECORD_AMOUNT_OFFSET, SECOND_HIT_AMOUNT);
    assert(fixture_decode(test_fixture));
    assert(position_decode(context, &tile) && tile.x == LOCAL_TILE_X + 1);
    rs3_player_hit_event event;
    uint32_t written;
    uint64_t next;
    uint64_t lost;
    assert(rs3_player_hit_events_since(context, 0, &event, 1, &written, &next, &lost) == RS3_OK);
    assert(written == 1 && lost == 0 && event.hit.amount == SECOND_HIT_AMOUNT);
    assert(event.player_index == LOCAL_PLAYER_INDEX);

    /* An actor without the player vtable gives neither a tile nor hit marks. */
    fixture_put_u64(test_fixture, LOCAL_ACTOR, FIXTURE_MODULE_BASE + FOREIGN_VTABLE_RVA);
    tile = (rs3_position){0};
    assert(fixture_decode(test_fixture));
    assert(!position_decode(context, &tile) && !tile.valid);
    assert(snapshot->player_hit_info.status == RS3_UNAVAILABLE);

    /* Neither does an actor that stores another player's index. */
    fixture_put_u64(test_fixture, LOCAL_ACTOR,
                    FIXTURE_MODULE_BASE + profile->player_actor_vtable_rva);
    fixture_put_u32(test_fixture, LOCAL_ACTOR + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET,
                    OTHER_PLAYER_INDEX);
    assert(fixture_decode(test_fixture));
    assert(!position_decode(context, &tile));
    assert(snapshot->player_hit_info.status == RS3_UNAVAILABLE);
    fixture_destroy(test_fixture);
}

/** Prints the test and profile names and runs the test against that profile. */
#define RUN_PROFILE_TEST(test_function, profile)                                                   \
    do                                                                                             \
    {                                                                                              \
        printf("RUN  %s (%s)\n", #test_function, (profile)->version);                              \
        test_function(profile);                                                                    \
    } while (0)

int main(void)
{
    const client_profile *profiles[] = {&profile_primary, compatibility_profile()};
    for (size_t index = 0; index < sizeof profiles / sizeof profiles[0]; index++)
    {
        RUN_PROFILE_TEST(reports_new_hits_after_a_baseline, profiles[index]);
        RUN_PROFILE_TEST(optional_channel_failures_keep_the_frame, profiles[index]);
        RUN_PROFILE_TEST(resolves_the_actor_through_the_player_table, profiles[index]);
        RUN_PROFILE_TEST(reports_cursor_errors_and_overflow, profiles[index]);
        RUN_PROFILE_TEST(unsupported_profile_reports_its_reason, profiles[index]);
    }
    RUN_TEST(compatibility_profile_reads_tile_and_hits_from_one_actor);
    puts("PASS: player hit tests");
    return 0;
}
