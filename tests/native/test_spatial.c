/* Spatial invariants: identity, consistent reads, unknown targets and physical projection. */
#include "fixture.h"
#include "spatial.h"

#include <math.h>
#include <string.h>

#define POSE_PLANE_OFFSET      0x50u
#define POSE_SIZE_OFFSET       0x18cu
#define POSE_QUATERNION_OFFSET 0x1ccu
#define POSE_FINE_OFFSET       0x280u

static fixture *pose_fixture(void)
{
    fixture *test_fixture = fixture_create();
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + POSE_PLANE_OFFSET, 1);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + POSE_SIZE_OFFSET, 1);
    float fine[3] = {512.0f * 3000.5f, 100.0f, 512.0f * 3001.5f};
    float quaternion[4] = {0, 0, 0, 1};
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR + POSE_FINE_OFFSET, sizeof fine), fine,
           sizeof fine);
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR + POSE_QUATERNION_OFFSET, sizeof quaternion),
           quaternion, sizeof quaternion);
    return test_fixture;
}

static void decodes_pose_but_does_not_fabricate_target(void)
{
    fixture *test_fixture = pose_fixture();
    rs3_actor_spatial pose;
    assert(spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                profile_primary.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                &pose));
    assert(pose.tile.valid && pose.tile.x == 3000 && pose.tile.y == 3001 && pose.tile.plane == 1);
    assert(pose.facing_degrees == 0 && pose.facing_reason == 0);
    assert(!(pose.valid_mask & RS3_SPATIAL_TARGET));
    assert(pose.target_reason == RS3_SPATIAL_UNMAPPED && pose.target_runtime_id == -1);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + 0x88, FIXTURE_NPC_RUNTIME_ID + 1);
    assert(!spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                 profile_primary.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                 &pose));
    assert(!pose.valid_mask);
    fixture_destroy(test_fixture);
}

static void rejects_torn_and_nonfinite_poses(void)
{
    fixture *test_fixture = pose_fixture();
    rs3_actor_spatial pose;
    fixture_mutate_on_read(test_fixture, FIXTURE_ACTOR, 2, FIXTURE_ACTOR + POSE_PLANE_OFFSET, 2);
    assert(!spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                 profile_primary.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                 &pose));
    fixture_clear_mutation(test_fixture);
    float invalid = NAN;
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR + POSE_FINE_OFFSET, sizeof invalid), &invalid,
           sizeof invalid);
    assert(spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                profile_primary.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                &pose));
    assert(!pose.tile.valid && !(pose.valid_mask & RS3_SPATIAL_FINE_POSITION));
    assert(pose.valid_mask & RS3_SPATIAL_FACING);
    fixture_destroy(test_fixture);
}

static void compatibility_npc_tiles_require_the_shared_position_getter(void)
{
    const uint64_t getter = FIXTURE_MODULE_BASE + 0x6ee110u;
    const uint64_t owner_instruction = FIXTURE_MODULE_BASE + 0x38523u;
    const uint64_t player_slot =
        FIXTURE_MODULE_BASE + profile_compatibility.player_actor_vtable_rva + 0xb0u;
    const uint64_t npc_slot =
        FIXTURE_MODULE_BASE + profile_compatibility.npc_entity_vtable_rva + 0xb0u;
    const unsigned char getter_bytes[] = {0x48, 0x8d, 0x81, 0x80, 0x02, 0, 0, 0xc3};
    const unsigned char owner_bytes[] = {0x49, 0x8b, 0x40, 0x08, 0x48, 0x89, 0x41, 0x60};
    fixture *test_fixture = fixture_create_for_profile(&profile_compatibility);
    fixture_add_block(test_fixture, getter, sizeof getter_bytes);
    fixture_add_block(test_fixture, owner_instruction, sizeof owner_bytes);
    fixture_add_block(test_fixture, player_slot, sizeof getter);
    fixture_add_block(test_fixture, npc_slot, sizeof getter);
    memcpy(fixture_bytes(test_fixture, getter, sizeof getter_bytes), getter_bytes,
           sizeof getter_bytes);
    memcpy(fixture_bytes(test_fixture, owner_instruction, sizeof owner_bytes), owner_bytes,
           sizeof owner_bytes);
    fixture_put_u64(test_fixture, player_slot, getter);
    fixture_put_u64(test_fixture, npc_slot, getter);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + POSE_PLANE_OFFSET, 1);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + POSE_SIZE_OFFSET, 2);
    const float fine[] = {512.0f * 3001.5f, 100.0f, 512.0f * 3002.5f};
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR + POSE_FINE_OFFSET, sizeof fine), fine,
           sizeof fine);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.npc_count = 1;
    snapshot->npcs[0].runtime_id = FIXTURE_NPC_RUNTIME_ID;
    test_fixture->context->npcs.staged_actor_addresses[0] = FIXTURE_ACTOR;
    spatial_stage(test_fixture->context, snapshot, FIXTURE_WORLD);
    assert(snapshot->spatial.npc_count == 1);
    const rs3_actor_spatial *pose = &snapshot->npc_spatial[0];
    assert(pose->tile.valid && pose->tile.x == 3000 && pose->tile.y == 3001 &&
           pose->tile.plane == 1);
    assert(pose->facing_reason == RS3_SPATIAL_UNMAPPED && !snapshot->spatial.camera.valid_mask);
    fixture_put_u64(test_fixture, npc_slot, getter + 1);
    spatial_stage(test_fixture->context, snapshot, FIXTURE_WORLD);
    assert(snapshot->spatial.status == RS3_UNAVAILABLE && snapshot->spatial.npc_count == 0);
    fixture_destroy(test_fixture);
}

static rs3_camera test_camera(void)
{
    rs3_camera camera = {.valid_mask = RS3_CAMERA_COMPLETE,
                         .plane = 1,
                         .viewport_width = 800,
                         .viewport_height = 600,
                         .client_screen_x = 100,
                         .client_screen_y = 200};
    camera.world_to_clip[0] = 1;
    camera.world_to_clip[5] = 1;
    camera.world_to_clip[10] = 1;
    camera.world_to_clip[15] = 1;
    return camera;
}

static void projects_with_physical_origin_and_y_inversion(void)
{
    rs3_camera camera = test_camera();
    rs3_actor_spatial actor = {.valid_mask = RS3_SPATIAL_FINE_POSITION | RS3_SPATIAL_SAME_WORLD,
                               .tile = {1, 0, 0, 1},
                               .fine_x = 0.5f,
                               .fine_height = 0.5f};
    rs3_screen_point point;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK);
    assert(point.valid && point.x == 700 && point.y == 350);
    actor.valid_mask &= ~RS3_SPATIAL_SAME_WORLD;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK);
    assert(!point.valid && point.reason == RS3_SPATIAL_WRONG_WORLD);
    actor.valid_mask |= RS3_SPATIAL_SAME_WORLD;
    camera.valid_mask &= ~RS3_CAMERA_PHYSICAL_ORIGIN;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK && !point.valid);
    camera = test_camera();
    actor.fine_x = 2;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK);
    assert(!point.valid && point.reason == RS3_SPATIAL_OFF_SCREEN);
    actor.fine_x = 0;
    camera.world_to_clip[15] = -1;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK);
    assert(!point.valid && point.reason == RS3_SPATIAL_BEHIND_CAMERA);
}

static void rejects_unknown_height_plane_and_abi(void)
{
    rs3_camera camera = test_camera();
    rs3_position tile = {1, 0, 0, 1};
    rs3_screen_point point;
    assert(rs3_project_tile(&camera, &tile, NAN, &point, sizeof point) == RS3_OK);
    assert(!point.valid && point.reason == RS3_SPATIAL_NONFINITE);
    tile.plane = 2;
    assert(rs3_project_tile(&camera, &tile, 0, &point, sizeof point) == RS3_OK);
    assert(!point.valid && point.reason == RS3_SPATIAL_WRONG_PLANE);
    assert(rs3_project_tile(&camera, &tile, 0, &point, 1) == RS3_E_ABI);
    assert(rs3_spatial_sizeof(RS3_SPATIAL_SIZE_ACTOR) == sizeof(rs3_actor_spatial));
}

static void snapshot_accessors_enforce_capacity_and_preserve_identity(void)
{
    fixture *test_fixture = pose_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->spatial.npc_count = 1;
    snapshot->spatial.sample_sequence = 77;
    snapshot->npc_spatial[0].instance_id = 99;
    uint32_t required = 0;
    rs3_actor_spatial actor;
    rs3_spatial_info info;
    assert(rs3_snapshot_npc_spatial(snapshot, NULL, 0, &required) == RS3_E_BUFFER && required == 1);
    assert(rs3_snapshot_npc_spatial(snapshot, &actor, 1, &required) == RS3_OK &&
           actor.instance_id == 99);
    assert(rs3_snapshot_spatial(snapshot, &info, sizeof info) == RS3_OK &&
           info.sample_sequence == 77);
    assert(rs3_snapshot_spatial(snapshot, &info, 1) == RS3_E_ABI);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(decodes_pose_but_does_not_fabricate_target);
    RUN_TEST(rejects_torn_and_nonfinite_poses);
    RUN_TEST(compatibility_npc_tiles_require_the_shared_position_getter);
    RUN_TEST(projects_with_physical_origin_and_y_inversion);
    RUN_TEST(rejects_unknown_height_plane_and_abi);
    RUN_TEST(snapshot_accessors_enforce_capacity_and_preserve_identity);
    return 0;
}
