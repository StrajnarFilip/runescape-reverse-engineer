/* Sampling lifecycle, failed-frame invalidation, the worker and the player tile profile. */
#include "fixture.h"

#include <string.h>

#include "process.h"

/** Marks the fixture context as attached to this test process. */
static void attach_to_current_process(fixture *test_fixture, DWORD access)
{
    test_fixture->context->target.process_handle =
        OpenProcess(access, FALSE, GetCurrentProcessId());
    assert(test_fixture->context->target.process_handle != NULL);
    test_fixture->context->target.attached = 1;
}

static void failed_sample_clears_every_field(void)
{
    fixture *test_fixture = fixture_create();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    attach_to_current_process(test_fixture, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ);
    assert(rs3_sample(test_fixture->context, snapshot) == RS3_OK);
    assert(snapshot->player.valid_variables != 0);

    fixture_put_u64(test_fixture, FIXTURE_CLIENT + LAYOUT_STATE_POINTER_OFFSET, 0);
    assert(rs3_sample(test_fixture->context, snapshot) == RS3_OK);
    assert(snapshot->info.status == RS3_UNAVAILABLE);
    assert(snapshot->info.reason == RS3_READ_FAILED);
    assert(snapshot->player.valid_variables == 0);
    assert(snapshot->info.npc_count == 0 && snapshot->info.hit_count == 0);
    assert(test_fixture->context->hit_event_sequence == 1);
    fixture_destroy(test_fixture);
}

static void worker_and_synchronous_sampling_are_exclusive(void)
{
    rs3_context *context = NULL;
    rs3_snapshot *snapshot = NULL;
    rs3_options options = {sizeof options, UINT32_MAX, 20, 0};
    assert(rs3_create(&options, &context) == RS3_OK);
    assert(rs3_snapshot_create(&snapshot) == RS3_OK);
    assert(rs3_start(context, 0) == RS3_E_ARGUMENT);
    assert(rs3_start(context, 100) == RS3_OK);
    assert(rs3_start(context, 100) == RS3_E_STATE);
    assert(rs3_sample(context, snapshot) == RS3_E_STATE);

    Sleep(60);
    assert(rs3_copy_latest(context, snapshot) == RS3_OK);
    assert(snapshot->info.sequence > 0 && snapshot->info.status == RS3_UNAVAILABLE);
    assert(snapshot->player.valid_variables == 0);
    assert(snapshot->info.reason == RS3_NO_CLIENT);
    assert(snapshot->info.npc_count == 0 && snapshot->info.hit_count == 0);

    assert(rs3_stop(context) == RS3_OK && rs3_stop(context) == RS3_OK);
    assert(rs3_sample(context, snapshot) == RS3_OK);
    rs3_destroy(context);
    rs3_snapshot_destroy(snapshot);
}

/* Fake addresses of the Compatibility Mode player tile structures. */
#define SCENE_CLIENT   0x900000ull
#define PLAYER_CONTEXT 0xa00000ull
#define PLAYER_TABLE   0xb00000ull
#define PLAYER_SLOTS   0xc00000ull
#define PLAYER_SLOT    0xd00000ull
#define PLAYER_ACTOR   0xe00000ull

/** Maps the loaded code signatures of a profile so they verify. */
static void add_signatures(fixture *test_fixture, const client_profile *profile)
{
    for (size_t index = 0; index < profile->signature_count; index++)
    {
        const profile_signature *signature = &profile->signatures[index];
        uint64_t address = FIXTURE_MODULE_BASE + signature->rva;
        fixture_add_block(test_fixture, address, signature->length);
        memcpy(fixture_bytes(test_fixture, address, signature->length), signature->bytes,
               signature->length);
    }
}

/** Maps a player at index 1 of the player table, two tiles wide. */
static void add_player_tile(fixture *test_fixture, float fine_coordinates[3])
{
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA, 8);
    fixture_add_block(test_fixture, SCENE_CLIENT, POSITION_PLAYER_CONTEXT_OFFSET + 8);
    fixture_add_block(test_fixture, PLAYER_CONTEXT, POSITION_PLAYER_OVERRIDE_OFFSET + 8);
    fixture_add_block(test_fixture, PLAYER_TABLE, POSITION_TABLE_RANGE_OFFSET + 16);
    fixture_add_block(test_fixture, PLAYER_SLOTS, 16);
    fixture_add_block(test_fixture, PLAYER_SLOT, POSITION_SLOT_ENTITY_OFFSET + 8);
    fixture_add_block(test_fixture, PLAYER_ACTOR, POSITION_FINE_COORDINATES_OFFSET + 12);
    fixture_put_u64(test_fixture, FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA, SCENE_CLIENT);
    fixture_put_u32(test_fixture, SCENE_CLIENT + POSITION_CLIENT_STATE_OFFSET,
                    POSITION_IN_GAME_STATE);
    fixture_put_u64(test_fixture, SCENE_CLIENT + POSITION_PLAYER_CONTEXT_OFFSET, PLAYER_CONTEXT);
    fixture_put_u64(test_fixture, PLAYER_CONTEXT, SCENE_CLIENT);
    fixture_put_u64(test_fixture, SCENE_CLIENT + POSITION_PLAYER_TABLE_OFFSET, PLAYER_TABLE);
    fixture_put_u64(test_fixture, PLAYER_TABLE + POSITION_TABLE_RANGE_OFFSET, PLAYER_SLOTS);
    fixture_put_u64(test_fixture, PLAYER_TABLE + POSITION_TABLE_RANGE_OFFSET + 8,
                    PLAYER_SLOTS + 16);
    fixture_put_u32(test_fixture, PLAYER_CONTEXT + POSITION_PLAYER_INDEX_OFFSET, 1);
    fixture_put_u64(test_fixture, PLAYER_SLOTS + 8, PLAYER_SLOT);
    fixture_put_u64(test_fixture, PLAYER_SLOT + POSITION_SLOT_ENTITY_OFFSET, PLAYER_ACTOR);
    fixture_put_u32(test_fixture, PLAYER_ACTOR + POSITION_PLANE_OFFSET, 2);
    fixture_put_u32(test_fixture, PLAYER_ACTOR + POSITION_SIZE_OFFSET, 2);
    memcpy(fixture_bytes(test_fixture, PLAYER_ACTOR + POSITION_FINE_COORDINATES_OFFSET, 12),
           fine_coordinates, 12);
}

static void compatibility_profile_reports_the_player_tile(void)
{
    fixture *test_fixture = fixture_create_for_profile(&profile_compatibility);
    rs3_context *context = test_fixture->context;
    rs3_snapshot *snapshot = test_fixture->snapshot;
    add_signatures(test_fixture, &profile_compatibility);

    /* One changed signature byte rejects the profile. */
    unsigned char *first_signature_byte = fixture_bytes(
        test_fixture, FIXTURE_MODULE_BASE + profile_compatibility.signatures[0].rva, 1);
    *first_signature_byte ^= 1;
    assert(!process_select_profile(context, PROFILE_COMPATIBILITY_SHA256));
    assert(context->target.profile == NULL);
    *first_signature_byte ^= 1;
    assert(process_select_profile(context, PROFILE_COMPATIBILITY_SHA256));
    assert(context->target.profile->has_position);

    float fine_coordinates[3] = {3166.5f * 512, 1234, 3483.5f * 512};
    add_player_tile(test_fixture, fine_coordinates);
    attach_to_current_process(test_fixture, PROCESS_QUERY_INFORMATION);

    rs3_position position;
    assert(rs3_sample(context, snapshot) == RS3_OK);
    assert(snapshot->info.status == RS3_AVAILABLE);
    assert(rs3_copy_latest(context, snapshot) == RS3_OK);
    assert(strcmp(snapshot->info.client_version, PROFILE_COMPATIBILITY_SHORT_VERSION) == 0);
    assert(strcmp(rs3_snapshot_client_version(snapshot), PROFILE_COMPATIBILITY_VERSION) == 0);
    assert(snapshot->player.raw_variables[RS3_HEALTH] == 8700);
    assert(snapshot->player.prayer_base_level == 84);
    assert(snapshot->info.npc_count == 1 && snapshot->npcs[0].health_current == 326);
    assert(rs3_snapshot_position(snapshot, &position, 4) == RS3_E_ABI);
    assert(rs3_snapshot_position(snapshot, &position, sizeof position) == RS3_OK);
    assert(position.valid && position.x == 3165 && position.y == 3482 && position.plane == 2);

    /* An index past the slot array clears the tile but keeps the combat fields. */
    fixture_put_u32(test_fixture, PLAYER_CONTEXT + POSITION_PLAYER_INDEX_OFFSET, 2);
    assert(rs3_sample(context, snapshot) == RS3_OK);
    assert(snapshot->info.status == RS3_AVAILABLE);
    assert(!snapshot->position.valid && snapshot->player.valid_variables != 0);

    /* A tile without combat fields is a partial frame. */
    fixture_put_u64(test_fixture, PLAYER_CONTEXT + POSITION_PLAYER_OVERRIDE_OFFSET, PLAYER_ACTOR);
    uint64_t registry_root =
        FIXTURE_MODULE_BASE + profile_compatibility.registry_rva + LAYOUT_REGISTRY_ROOT_OFFSET;
    fixture_put_u64(test_fixture, registry_root, 0);
    fine_coordinates[0] += 512;
    memcpy(fixture_bytes(test_fixture, PLAYER_ACTOR + POSITION_FINE_COORDINATES_OFFSET, 12),
           fine_coordinates, 12);
    assert(rs3_sample(context, snapshot) == RS3_OK);
    assert(snapshot->info.status == RS3_PARTIAL);
    assert(snapshot->position.valid && snapshot->position.x == 3166);
    assert(snapshot->player.valid_variables == 0);

    /* Leaving the game scene makes the whole frame unavailable. */
    fixture_put_u32(test_fixture, SCENE_CLIENT + POSITION_CLIENT_STATE_OFFSET, 20);
    fixture_put_u64(test_fixture, registry_root, FIXTURE_DOMAIN_NODES);
    assert(rs3_sample(context, snapshot) == RS3_OK);
    assert(rs3_copy_latest(context, snapshot) == RS3_OK);
    assert(snapshot->info.status == RS3_UNAVAILABLE);
    assert(!snapshot->position.valid && snapshot->player.valid_variables == 0);
    assert(snapshot->info.npc_count == 0);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(failed_sample_clears_every_field);
    RUN_TEST(worker_and_synchronous_sampling_are_exclusive);
    RUN_TEST(compatibility_profile_reports_the_player_tile);
    puts("PASS: lifecycle tests");
    return 0;
}
