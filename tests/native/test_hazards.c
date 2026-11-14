/* P4 fixtures: identity evidence, lifetime, coverage, stale poses and copy-out ABI. */
#include "fixture.h"
#include "hazards.h"

#include <stdlib.h>
#include <string.h>

#define PURE_FONT_ID                 22893
#define NEXT_PURE_FONT_ID            22896
#define PURE_GOLEM_ID                22905
#define UNKNOWN_FONT_ID              999999
#define SECOND_PURE_FONT_FORM_ID     22899
#define THIRD_PURE_FONT_FORM_ID      22902
#define FIRST_VOLCANIC_FONT_FORM_ID  22894
#define SECOND_VOLCANIC_FONT_FORM_ID 22897
#define THIRD_VOLCANIC_FONT_FORM_ID  22900
#define TEST_TILE_X                  3000
#define TEST_TILE_Y                  3001
#define TEST_SCENE                   7ull
#define TEST_INSTANCE                9ull
#define INLINE_STRING_CAPACITY       23u
#define CLIENT_STRING_BYTES          24u
#define NPC_CONFIG_ID_OFFSET         8u

/** Install a synthetic same-frame P1 pose; these coordinates are fixture values. */
static void put_candidate(fixture *test_fixture, uint32_t index, int32_t config_id,
                          const char *name)
{
    rs3_snapshot *snapshot = test_fixture->snapshot;
    rs3_npc *npc = &snapshot->npcs[index];
    memset(npc, 0, sizeof *npc);
    npc->runtime_id = FIXTURE_NPC_RUNTIME_ID + index;
    npc->instance_id = TEST_INSTANCE + index;
    npc->config_id = config_id;
    npc->flags = RS3_NPC_NAME | RS3_NPC_CONFIG;
    npc->name_length = (uint32_t)strlen(name);
    memcpy(npc->name, name, npc->name_length + 1);
    snapshot->npc_spatial[index] = (rs3_actor_spatial){
        .runtime_id = npc->runtime_id,
        .instance_id = npc->instance_id,
        .valid_mask = RS3_SPATIAL_TILE | RS3_SPATIAL_FINE_POSITION | RS3_SPATIAL_SAME_WORLD,
        .observed_ns = FIXTURE_TIMESTAMP_NS,
        .tile = {1, TEST_TILE_X, TEST_TILE_Y, 1},
        .footprint_size = 1,
    };
}

static fixture *hazard_fixture(void)
{
    fixture *test_fixture = fixture_create();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.status = RS3_AVAILABLE;
    snapshot->info.sequence = 5;
    snapshot->info.monotonic_ns = FIXTURE_TIMESTAMP_NS;
    snapshot->combat.scene_epoch = TEST_SCENE;
    snapshot->info.npc_count = 1;
    snapshot->spatial = (rs3_spatial_info){
        .pid = FIXTURE_PID,
        .process_creation_time = FIXTURE_PROCESS_CREATION_TIME,
        .sample_sequence = snapshot->info.sequence,
        .observed_ns = FIXTURE_TIMESTAMP_NS,
        .scene_epoch = TEST_SCENE,
        .npc_count = 1,
        .status = RS3_PARTIAL,
    };
    put_candidate(test_fixture, 0, PURE_FONT_ID, "Pure Anima Font");
    return test_fixture;
}

static void recognizes_captured_identity_without_inventing_mechanics(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    uint64_t previous_reads = test_fixture->context->read_call_count;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(test_fixture->context->read_call_count == previous_reads);
    const rs3_hazard_info *info = &snapshot->hazard_info;
    const rs3_hazard *hazard = &snapshot->hazards[0];
    assert(info->status == RS3_PARTIAL && info->count == 1);
    assert(info->pid == FIXTURE_PID && info->scene_epoch == TEST_SCENE);
    assert(info->process_creation_time == FIXTURE_PROCESS_CREATION_TIME);
    assert(info->sample_sequence == snapshot->info.sequence);
    assert(info->observed_channels == RS3_HAZARD_CHANNEL_FONTS);
    assert(info->complete_channels == 0 && info->unmapped_channels == RS3_HAZARD_CHANNEL_ALL);
    assert(hazard->kind == RS3_HAZARD_FONT && hazard->variant == RS3_HAZARD_PURE);
    assert(hazard->flags == (RS3_HAZARD_IDENTITY | RS3_HAZARD_TILE | RS3_HAZARD_FOOTPRINT));
    assert(hazard->tile.valid && hazard->tile.x == TEST_TILE_X);
    assert(hazard->tile.y == TEST_TILE_Y && hazard->footprint_size == 1);
    assert(hazard->role == 0 && hazard->role_reason == RS3_HAZARD_UNMAPPED);
    assert(hazard->start_client_tick == 0 && hazard->end_client_tick == 0);
    assert(hazard->timing_reason == RS3_HAZARD_UNMAPPED);
    fixture_destroy(test_fixture);
}

static void tracks_appearance_disappearance_and_identity_changes(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.npc_count = 0;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazard_info.count == 0 && snapshot->hazard_info.complete_channels == 0);
    snapshot->info.npc_count = 1;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].config_id == PURE_FONT_ID);
    /* A captured config change on the same actor preserves its native lifetime. */
    snapshot->npcs[0].config_id = NEXT_PURE_FONT_ID;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].config_id == NEXT_PURE_FONT_ID);
    assert(snapshot->hazards[0].instance_id == TEST_INSTANCE);
    put_candidate(test_fixture, 0, PURE_GOLEM_ID, "Pure anima-golem");
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_GOLEM);
    assert(snapshot->hazard_info.observed_channels == RS3_HAZARD_CHANNEL_GOLEMS);
    snapshot->info.npc_count = 0;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazard_info.count == 0 && snapshot->hazards[0].instance_id == 0);
    assert(snapshot->hazard_info.status == RS3_UNAVAILABLE);
    fixture_destroy(test_fixture);
}

static void scene_changes_never_reuse_old_tiles(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    hazards_commit(test_fixture->context, snapshot, 1);
    snapshot->combat.scene_epoch++;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazard_info.count == 1 && snapshot->hazard_info.unlocated_count == 1);
    assert(!(snapshot->hazards[0].flags & RS3_HAZARD_TILE));
    assert(snapshot->hazards[0].tile_reason == RS3_HAZARD_WRONG_SCENE);
    snapshot->spatial.scene_epoch = snapshot->combat.scene_epoch;
    snapshot->npc_spatial[0].tile.x++;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].tile.x == TEST_TILE_X + 1);
    assert(snapshot->hazard_info.scene_epoch == TEST_SCENE + 1);
    fixture_destroy(test_fixture);
}

/** Replay captured font form transitions without inferring charging/completion. */
static void keeps_font_identity_through_recorded_config_transitions(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    const int32_t pure_forms[] = {PURE_FONT_ID, NEXT_PURE_FONT_ID, SECOND_PURE_FONT_FORM_ID,
                                  THIRD_PURE_FONT_FORM_ID};
    const int32_t volcanic_forms[] = {FIRST_VOLCANIC_FONT_FORM_ID, SECOND_VOLCANIC_FONT_FORM_ID,
                                      THIRD_VOLCANIC_FONT_FORM_ID};
    for (size_t index = 0; index < sizeof pure_forms / sizeof pure_forms[0]; index++)
    {
        snapshot->npcs[0].config_id = pure_forms[index];
        hazards_commit(test_fixture->context, snapshot, 1);
        const rs3_hazard *hazard = &snapshot->hazards[0];
        assert(hazard->kind == RS3_HAZARD_FONT && hazard->variant == RS3_HAZARD_PURE);
        assert(hazard->instance_id == TEST_INSTANCE && hazard->config_id == pure_forms[index]);
        assert(hazard->flags & RS3_HAZARD_IDENTITY);
        assert(!(hazard->flags & RS3_HAZARD_MECHANICS_VALIDATED));
        assert(hazard->timing_reason == RS3_HAZARD_UNMAPPED);
    }
    for (size_t index = 0; index < sizeof volcanic_forms / sizeof volcanic_forms[0]; index++)
    {
        put_candidate(test_fixture, 0, volcanic_forms[index], "Volcanic Anima Font");
        hazards_commit(test_fixture->context, snapshot, 1);
        assert(snapshot->hazards[0].kind == RS3_HAZARD_FONT);
        assert(snapshot->hazards[0].variant == RS3_HAZARD_VOLCANIC);
        assert(snapshot->hazards[0].instance_id == TEST_INSTANCE);
        assert(snapshot->hazards[0].role == 0);
    }
    fixture_destroy(test_fixture);
}

/** The prefix is an observed exact name, not a normalization or a combat role. */
static void accepts_observed_starred_names_without_stripping_arbitrary_prefixes(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    put_candidate(test_fixture, 0, PURE_GOLEM_ID, "Pure anima-golem");
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_GOLEM);
    put_candidate(test_fixture, 0, PURE_GOLEM_ID, "*Pure anima-golem");
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_GOLEM);
    assert(snapshot->hazards[0].instance_id == TEST_INSTANCE);
    assert(snapshot->hazards[0].role_reason == RS3_HAZARD_UNMAPPED);
    assert(!(snapshot->hazards[0].flags & RS3_HAZARD_MECHANICS_VALIDATED));
    put_candidate(test_fixture, 0, PURE_GOLEM_ID, "**Pure anima-golem");
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_UNKNOWN);
    assert(!(snapshot->hazards[0].flags & RS3_HAZARD_IDENTITY));
    put_candidate(test_fixture, 0, UNKNOWN_FONT_ID, "*Pure anima-golem");
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_UNKNOWN);
    fixture_destroy(test_fixture);
}

static void rejects_stale_foreign_and_invalid_poses(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    rs3_spatial_info baseline = snapshot->spatial;
    rs3_actor_spatial pose = snapshot->npc_spatial[0];
    for (unsigned trial = 0; trial < 14; trial++)
    {
        snapshot->spatial = baseline;
        snapshot->npc_spatial[0] = pose;
        switch (trial)
        {
            case 0:
                snapshot->spatial.pid++;
                break;
            case 1:
                snapshot->spatial.process_creation_time++;
                break;
            case 2:
                snapshot->spatial.sample_sequence--;
                break;
            case 3:
                snapshot->spatial.observed_ns--;
                break;
            case 4:
                snapshot->spatial.npc_count = 0;
                break;
            case 5:
                snapshot->npc_spatial[0].instance_id++;
                break;
            case 6:
                snapshot->npc_spatial[0].runtime_id++;
                break;
            case 7:
                snapshot->npc_spatial[0].valid_mask &= ~RS3_SPATIAL_SAME_WORLD;
                break;
            case 8:
                snapshot->npc_spatial[0].tile.valid = 0;
                break;
            case 9:
                snapshot->npc_spatial[0].tile.x = -1;
                break;
            case 10:
                snapshot->npc_spatial[0].tile.y = MAX_TILE_COORDINATE;
                break;
            case 11:
                snapshot->npc_spatial[0].tile.plane = 4;
                break;
            case 12:
                snapshot->npc_spatial[0].footprint_size = 0;
                break;
            case 13:
                snapshot->spatial.status = RS3_UNAVAILABLE;
                break;
        }
        hazards_commit(test_fixture->context, snapshot, 1);
        assert(snapshot->hazard_info.count == 1);
        assert(snapshot->hazard_info.unlocated_count == 1);
        assert(!snapshot->hazards[0].tile.valid && snapshot->hazards[0].tile.x == 0);
        assert(!(snapshot->hazards[0].flags & RS3_HAZARD_TILE));
    }
    fixture_destroy(test_fixture);
}

static void requires_both_id_and_name_and_original_profile(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->npcs[0].config_id = UNKNOWN_FONT_ID;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazard_info.count == 1 && snapshot->hazards[0].kind == RS3_HAZARD_UNKNOWN);
    assert(!(snapshot->hazards[0].flags & RS3_HAZARD_IDENTITY));
    put_candidate(test_fixture, 0, PURE_FONT_ID, "Different NPC");
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_UNKNOWN);
    snapshot->npcs[0].flags &= ~RS3_NPC_NAME;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(!(snapshot->hazards[0].flags & RS3_HAZARD_IDENTITY));
    put_candidate(test_fixture, 0, PURE_FONT_ID, "Pure Anima Font");
    snapshot->npcs[0].name_length = RS3_NAME_CAPACITY + 1;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_UNKNOWN);
    put_candidate(test_fixture, 0, PURE_FONT_ID, "Pure Anima Font");
    test_fixture->context->target.profile = &profile_compatibility;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_UNKNOWN);
    assert(!(snapshot->hazards[0].flags & RS3_HAZARD_IDENTITY));
    assert(snapshot->hazard_info.observed_channels == 0);
    fixture_destroy(test_fixture);
}

static void failed_npc_reads_clear_all_candidates(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    hazards_commit(test_fixture->context, snapshot, 1);
    hazards_commit(test_fixture->context, snapshot, 0);
    assert(snapshot->hazard_info.status == RS3_UNAVAILABLE);
    assert(snapshot->hazard_info.reason == RS3_READ_FAILED && snapshot->hazard_info.count == 0);
    assert(snapshot->hazards[0].instance_id == 0);
    snapshot->info.status = RS3_UNAVAILABLE;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazard_info.count == 0);
    fixture_destroy(test_fixture);
}

static void reports_bounded_overflow_and_required_buffer_size(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.npc_count = RS3_MAX_HAZARDS + 3;
    snapshot->spatial.npc_count = snapshot->info.npc_count;
    for (uint32_t index = 0; index < snapshot->info.npc_count; index++)
    {
        put_candidate(test_fixture, index, PURE_FONT_ID, "Pure Anima Font");
    }
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(snapshot->hazard_info.count == RS3_MAX_HAZARDS);
    assert(snapshot->hazard_info.candidate_count == RS3_MAX_HAZARDS + 3);
    assert(snapshot->hazard_info.dropped_count == 3);
    assert(snapshot->hazard_info.reason == RS3_HAZARD_OVERFLOW);
    uint32_t required = 0;
    rs3_hazard sentinel = {.instance_id = UINT64_MAX};
    assert(rs3_snapshot_hazards(snapshot, &sentinel, 1, &required) == RS3_E_BUFFER);
    assert(required == RS3_MAX_HAZARDS && sentinel.instance_id == UINT64_MAX);
    assert(rs3_snapshot_hazards(snapshot, NULL, required, &required) == RS3_E_ARGUMENT);
    fixture_destroy(test_fixture);
}

static void public_abi_checks_arguments_and_caller_ownership(void)
{
    fixture *test_fixture = hazard_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    rs3_hazard_info info;
    rs3_hazard copy;
    uint32_t required = 0;
    hazards_commit(test_fixture->context, snapshot, 1);
    assert(rs3_hazards_sizeof(RS3_HAZARD_SIZE_INFO) == sizeof info);
    assert(rs3_hazards_sizeof(RS3_HAZARD_SIZE_ENTRY) == sizeof copy);
    assert(rs3_hazards_sizeof(0) == 0);
    assert(rs3_snapshot_hazard_info(NULL, &info, sizeof info) == RS3_E_ARGUMENT);
    assert(rs3_snapshot_hazard_info(snapshot, NULL, sizeof info) == RS3_E_ARGUMENT);
    assert(rs3_snapshot_hazard_info(snapshot, &info, sizeof info - 1) == RS3_E_ABI);
    assert(rs3_snapshot_hazard_info(snapshot, &info, sizeof info) == RS3_OK);
    assert(rs3_snapshot_hazards(NULL, &copy, 1, &required) == RS3_E_ARGUMENT);
    assert(rs3_snapshot_hazards(snapshot, &copy, 1, NULL) == RS3_E_ARGUMENT);
    assert(rs3_snapshot_hazards(snapshot, NULL, 0, &required) == RS3_E_BUFFER && required == 1);
    assert(rs3_snapshot_hazards(snapshot, &copy, 1, &required) == RS3_OK);
    hazards_commit(test_fixture->context, snapshot, 0);
    assert(copy.instance_id == TEST_INSTANCE && info.count == 1);
    assert(rs3_snapshot_hazards(snapshot, NULL, 0, &required) == RS3_OK && required == 0);
    fixture_destroy(test_fixture);
}

static void frame_wiring_uses_committed_npc_lifetimes(void)
{
    fixture *test_fixture = fixture_create();
    const char *name = "Pure Anima Font";
    unsigned char *client_string =
        fixture_bytes(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_NAME_OFFSET, CLIENT_STRING_BYTES);
    memset(client_string, 0, CLIENT_STRING_BYTES);
    memcpy(client_string, name, strlen(name));
    client_string[CLIENT_STRING_BYTES - 1] = (unsigned char)(INLINE_STRING_CAPACITY - strlen(name));
    fixture_put_u32(test_fixture, FIXTURE_NPC_CONFIG + NPC_CONFIG_ID_OFFSET, PURE_FONT_ID);
    assert(fixture_decode(test_fixture));
    rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(snapshot->hazard_info.count == 1);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_FONT);
    assert(snapshot->hazards[0].instance_id == snapshot->npcs[0].instance_id);
    assert(snapshot->hazards[0].instance_id != 0);
    assert(!snapshot->hazards[0].tile.valid); /* No P1 signatures in this fixture. */
    fixture_destroy(test_fixture);
}

static void unavailable_samples_clear_and_publish_the_hazard_channel(void)
{
    rs3_context *context;
    rs3_snapshot *sample;
    rs3_snapshot *published;
    rs3_options options = {.struct_size = sizeof options, .pid = UINT32_MAX};
    assert(rs3_create(&options, &context) == RS3_OK);
    assert(rs3_snapshot_create(&sample) == RS3_OK);
    assert(rs3_snapshot_create(&published) == RS3_OK);
    sample->hazard_info.count = 1;
    sample->hazards[0].instance_id = TEST_INSTANCE;
    assert(rs3_sample(context, sample) == RS3_OK);
    assert(sample->hazard_info.status == RS3_UNAVAILABLE);
    assert(sample->hazard_info.count == 0 && sample->hazards[0].instance_id == 0);
    assert(rs3_copy_latest(context, published) == RS3_OK);
    assert(memcmp(&sample->hazard_info, &published->hazard_info, sizeof(rs3_hazard_info)) == 0);
    rs3_snapshot_destroy(published);
    rs3_snapshot_destroy(sample);
    rs3_destroy(context);
}

/** Match the scene chain used by the Compatibility Mode LocList implementation. */
static void compatibility_scene_beam_tiles_follow_the_live_location_lists(void)
{
    const uint64_t manager = 0x900000, map = 0x920000, square = 0x940000, maps = 0x960000,
                   rows = 0x961000, columns = 0x962000, chunk_rows = 0x963000,
                   chunk_refs = 0x964000, chunk = 0x965000, locations = 0x966000,
                   location = 0x967000, config = 0x968000;
    fixture *sample = fixture_create_for_profile(&profile_compatibility);
    rs3_snapshot *snapshot = sample->snapshot;
    snapshot->info.status = RS3_AVAILABLE;
    snapshot->info.npc_count = 1;
    snapshot->info.monotonic_ns = FIXTURE_TIMESTAMP_NS;
    snapshot->encounter.stage_raw.valid = 1;
    put_candidate(sample, 0, 22891, "Telos, the Warden");
    fixture_add_block(sample, manager, 0x100);
    fixture_add_block(sample, map, 0x14780);
    fixture_add_block(sample, square, 0xeba0);
    const uint64_t vectors[] = {maps, rows, columns, chunk_rows, chunk_refs, locations};
    for (unsigned index = 0; index < sizeof vectors / sizeof *vectors; index++)
    {
        fixture_add_block(sample, vectors[index], 0x100);
    }
    fixture_add_block(sample, chunk, 0x500);
    fixture_add_block(sample, location, 0x190);
    fixture_add_block(sample, config, 0x10);
    const uint64_t map_iterator = FIXTURE_MODULE_BASE + 0xc219dau;
    const uint64_t grid_getter = FIXTURE_MODULE_BASE + 0xccf1e0u;
    const unsigned char map_guard[] = {0x48, 0x8b, 0xb1, 0x60, 0x47, 1, 0};
    const unsigned char grid_guard[] = {0x48, 0x8d, 0x81, 0x70, 1, 0, 0, 0xc3};
    fixture_add_block(sample, map_iterator, sizeof map_guard);
    fixture_add_block(sample, grid_getter, sizeof grid_guard);
    memcpy(fixture_bytes(sample, map_iterator, sizeof map_guard), map_guard, sizeof map_guard);
    memcpy(fixture_bytes(sample, grid_getter, sizeof grid_guard), grid_guard, sizeof grid_guard);
    fixture_put_u64(sample, FIXTURE_WORLD + 0x199e8, manager);
    fixture_put_u32(sample, FIXTURE_WORLD + POSITION_CLIENT_STATE_OFFSET, POSITION_IN_GAME_STATE);
    const uint64_t scene_global = FIXTURE_MODULE_BASE + POSITION_CLIENT_POINTER_RVA;
    fixture_add_block(sample, scene_global, sizeof(uint64_t));
    fixture_put_u64(sample, scene_global, FIXTURE_WORLD);
    fixture_put_u64(sample, manager + 0x58, maps);
    fixture_put_u64(sample, maps + 8, map);
    fixture_put_u32(sample, map + 0x14714, TEST_TILE_X / 64);
    fixture_put_u32(sample, map + 0x14718, TEST_TILE_Y / 64);
    fixture_put_u64(sample, map + 0x14760, rows);
    fixture_put_u64(sample, map + 0x14768, rows + 0x18);
    fixture_put_u64(sample, rows, columns);
    fixture_put_u64(sample, rows + 8, columns + 0x18);
    fixture_put_u64(sample, columns + 8, square);
    fixture_put_u64(sample, square + 0xeb90, chunk_rows);
    fixture_put_u64(sample, square + 0xeb98, chunk_rows + 4 * 0x18);
    uint64_t row = chunk_rows + ((TEST_TILE_X / 16) % 4) * 0x18;
    fixture_put_u64(sample, row, chunk_refs);
    fixture_put_u64(sample, row + 8, chunk_refs + 4 * 0x10);
    fixture_put_u64(sample, chunk_refs + ((TEST_TILE_Y / 16) % 4) * 0x10 + 8, chunk);
    fixture_put_u64(sample, chunk + 0x308, locations);
    fixture_put_u64(sample, chunk + 0x310, locations + 0x20);
    fixture_put_u64(sample, locations + 8, location);
    fixture_put_u64(sample, locations + 0x18, location);
    fixture_put_u64(sample, location, FIXTURE_MODULE_BASE + 0x15b52d8);
    fixture_put_u64(sample, location + 0xd0, config);
    fixture_put_u32(sample, location + 0x170, 1);
    fixture_put_u32(sample, location + 0x174, TEST_TILE_X);
    fixture_put_u32(sample, location + 0x178, TEST_TILE_Y);
    fixture_put_u32(sample, config + 8, 103546);
    hazards_commit(sample->context, snapshot, 1);
    hazards_read_telos_beams(sample->context, snapshot, FIXTURE_WORLD);
    assert(snapshot->hazard_info.count == 1 && snapshot->hazard_info.complete_channels == 0);
    assert(snapshot->hazards[0].kind == RS3_HAZARD_GREEN_BEAM);
    assert(snapshot->hazards[0].flags ==
           (RS3_HAZARD_IDENTITY | RS3_HAZARD_TILE | RS3_HAZARD_SCENE_LOCATION));
    assert(snapshot->hazards[0].tile.x == TEST_TILE_X &&
           snapshot->hazards[0].tile.y == TEST_TILE_Y);

    snapshot->encounter.stage_raw.value = 1;
    fixture_put_u32(sample, config + 8, 103548);
    hazards_commit(sample->context, snapshot, 1);
    hazards_read_telos_beams(sample->context, snapshot, FIXTURE_WORLD);
    assert(snapshot->hazard_info.count == 1 && snapshot->hazards[0].kind == RS3_HAZARD_BLACK_BEAM);
    fixture_mutate_on_read(sample, location + 0x170, 2, location + 0x178, TEST_TILE_Y + 1);
    hazards_commit(sample->context, snapshot, 1);
    hazards_read_telos_beams(sample->context, snapshot, FIXTURE_WORLD);
    assert(snapshot->hazard_info.count == 0 && snapshot->hazard_info.reason == RS3_READ_FAILED);
    fixture_destroy(sample);
}

int main(void)
{
    RUN_TEST(recognizes_captured_identity_without_inventing_mechanics);
    RUN_TEST(tracks_appearance_disappearance_and_identity_changes);
    RUN_TEST(scene_changes_never_reuse_old_tiles);
    RUN_TEST(keeps_font_identity_through_recorded_config_transitions);
    RUN_TEST(accepts_observed_starred_names_without_stripping_arbitrary_prefixes);
    RUN_TEST(rejects_stale_foreign_and_invalid_poses);
    RUN_TEST(requires_both_id_and_name_and_original_profile);
    RUN_TEST(failed_npc_reads_clear_all_candidates);
    RUN_TEST(reports_bounded_overflow_and_required_buffer_size);
    RUN_TEST(public_abi_checks_arguments_and_caller_ownership);
    RUN_TEST(frame_wiring_uses_committed_npc_lifetimes);
    RUN_TEST(unavailable_samples_clear_and_publish_the_hazard_channel);
    RUN_TEST(compatibility_scene_beam_tiles_follow_the_live_location_lists);
    return 0;
}
