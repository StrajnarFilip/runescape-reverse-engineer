/* Spatial invariants: identity, consistent reads, unknown targets and physical projection. */
#include "fixture.h"
#include "spatial.h"

#include <math.h>
#include <string.h>

#define POSE_PLANE_OFFSET           0x50u
#define POSE_SIZE_OFFSET            0x18cu
#define POSE_QUATERNION_OFFSET      0x1ccu
#define POSE_FINE_OFFSET            0x280u
#define POSE_WORLD_OFFSET           0x60u
#define LOCAL_PLAYER_ACTOR          0x900000ull
#define LOCAL_PLAYER_CONTEXT        0x910000ull
#define LOCAL_PLAYER_INDEX          7u
#define POSITION_GETTER_RVA         0x6ee1f0u
#define FACING_GETTER_RVA           0x44b690u
#define PROJECTION_GUARD_RVA        0x5a34eu
#define ACTOR_TARGET_UID_OFFSET     0x1c4u
#define ACTOR_TARGET_KIND_OFFSET    0x238u
#define TARGET_KIND_GUARD_RVA       0x3fff66u
#define TARGET_UID_GUARD_RVA        0x3fff90u
#define EXPLICIT_NO_TARGET_KIND     127u
#define FIXTURE_VIEWPORT_MANAGER    0x920000ull
#define FIXTURE_ACTOR_WORLD         0x930000ull
#define COMPAT_MATRIX_GUARD_RVA     0x5a31eu
#define COMPAT_VIEWPORT_GUARD_RVA   0x5a371u
#define COMPAT_MANAGER_GUARD_RVA    0x4042e0u
#define FIXTURE_MENU                0x940000ull
#define FIXTURE_MENU_RECORD         0x960000ull
#define MENU_CYCLE_OFFSET           0x21d0u
#define MENU_MODE_OFFSET            0x1428u
#define MENU_MODERN_OPEN_OFFSET     0x17b0u
#define MENU_WALK_DISCRIMINATOR     0x48u
#define MENU_WALK_X_OFFSET          0x4cu
#define MENU_WALK_Y_OFFSET          0x50u
#define PRIMARY_WALK_ACTION_RVA     0x1721920u
#define FIXTURE_PRIMARY_MODULE_SIZE 0x1800000u
#define MENU_WORLD_MANAGER_OFFSET   0x19938u
#define MENU_OWNER_BACKLINK_OFFSET  8u
#define MENU_CACHED_RECORD_OFFSET   0x1400u
#define MENU_RECORD_TYPE_OFFSET     0x38u
#define MENU_RECORD_ITEM_OFFSET     0x44u
#define MENU_ACTION_ID_OFFSET       0x40u
#define MENU_RECORD_BYTES           0x60u
#define MENU_VERB_OFFSET            0x18u
#define MENU_STRING_TAG_OFFSET      23u
#define PRIMARY_MENU_VTABLE_RVA     0x15364c8u
#define PRIMARY_MENU_GUARD_RVA      0x39032bu

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
    const float fine[] = {512.0f * 15713.5f, 100.0f, 512.0f * 2830.5f};
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR + POSE_FINE_OFFSET, sizeof fine), fine,
           sizeof fine);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->info.npc_count = 1;
    snapshot->npcs[0].runtime_id = FIXTURE_NPC_RUNTIME_ID;
    test_fixture->context->npcs.staged_actor_addresses[0] = FIXTURE_ACTOR;
    spatial_stage(test_fixture->context, snapshot, FIXTURE_WORLD);
    assert(snapshot->spatial.npc_count == 1);
    const rs3_actor_spatial *pose = &snapshot->npc_spatial[0];
    assert(pose->tile.valid && pose->tile.x == 15712 && pose->tile.y == 2829 &&
           pose->tile.plane == 1);
    assert(pose->facing_reason == RS3_SPATIAL_UNMAPPED && !snapshot->spatial.camera.valid_mask);
    const float outside = 512.0f * (MAX_TILE_COORDINATE + 1.5f);
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR + POSE_FINE_OFFSET, sizeof outside), &outside,
           sizeof outside);
    spatial_stage(test_fixture->context, snapshot, FIXTURE_WORLD);
    assert(!pose->tile.valid);
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

static void projects_compatibility_viewport_at_physical_size(void)
{
    rs3_camera camera = test_camera();
    camera.valid_mask |= RS3_CAMERA_SCALED_VIEWPORT;
    camera.reserved[0] = 1200;
    camera.reserved[1] = 900;
    rs3_actor_spatial actor = {.valid_mask = RS3_SPATIAL_FINE_POSITION | RS3_SPATIAL_SAME_WORLD,
                               .tile = {1, 0, 0, 1},
                               .fine_x = 0.5f,
                               .fine_height = 0.5f};
    rs3_screen_point point;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK);
    assert(point.valid && point.x == 1000 && point.y == 425);
    camera.reserved[1] = 0;
    assert(rs3_project_actor(&camera, &actor, 0, &point, sizeof point) == RS3_OK);
    assert(!point.valid && point.reason == RS3_SPATIAL_WINDOW_UNKNOWN);
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

/** Build a complete frame with separate player/NPC actors and pinned spatial instructions. */
static fixture *player_pose_fixture_for(const client_profile *profile)
{
    fixture *test_fixture = fixture_create_for_profile(profile);
    fixture_add_block(test_fixture, LOCAL_PLAYER_ACTOR, LAYOUT_LOCAL_ACTOR_HITMARKS_OFFSET + 8);
    fixture_add_block(test_fixture, LOCAL_PLAYER_CONTEXT, LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET + 8);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + LAYOUT_LOCAL_ACTOR_CONTEXT_OFFSET,
                    LOCAL_PLAYER_CONTEXT);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_CONTEXT, FIXTURE_WORLD);
    fixture_put_u32(test_fixture, LOCAL_PLAYER_CONTEXT + LAYOUT_LOCAL_ACTOR_INDEX_OFFSET,
                    LOCAL_PLAYER_INDEX);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_CONTEXT + LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET,
                    LOCAL_PLAYER_ACTOR);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_ACTOR,
                    FIXTURE_MODULE_BASE + profile->player_actor_vtable_rva);
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET,
                    LOCAL_PLAYER_INDEX);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_ACTOR + POSE_WORLD_OFFSET, FIXTURE_WORLD);
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + POSE_PLANE_OFFSET, 1);
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + POSE_SIZE_OFFSET, 1);
    const float fine[] = {512.0f * 3000.5f, 100.0f, 512.0f * 3001.5f};
    const float quaternion[] = {0, 0, 0, 1};
    const uint64_t actors[] = {LOCAL_PLAYER_ACTOR, FIXTURE_ACTOR};
    for (unsigned index = 0; index < sizeof actors / sizeof *actors; index++)
    {
        memcpy(fixture_bytes(test_fixture, actors[index] + POSE_FINE_OFFSET, sizeof fine), fine,
               sizeof fine);
        memcpy(
            fixture_bytes(test_fixture, actors[index] + POSE_QUATERNION_OFFSET, sizeof quaternion),
            quaternion, sizeof quaternion);
    }
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + POSE_PLANE_OFFSET, 1);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + POSE_SIZE_OFFSET, 1);
    const unsigned char position_guard[] = {0x48, 0x8d, 0x81, 0x80, 2, 0, 0, 0xc3};
    const unsigned char facing_guard[] = {0x48, 0x83, 0xec, 0x68, 0xf3, 0x0f, 0x10, 0x69, 0x1c};
    const unsigned char projection_guard[] = {0x0f, 0x59, 0x81, 0, 0x39, 1, 0};
    const uint32_t guard_rvas[] = {POSITION_GETTER_RVA, FACING_GETTER_RVA, PROJECTION_GUARD_RVA};
    const unsigned char *guards[] = {position_guard, facing_guard, projection_guard};
    const size_t guard_sizes[] = {sizeof position_guard, sizeof facing_guard,
                                  sizeof projection_guard};
    for (unsigned guard = 0; guard < sizeof guard_rvas / sizeof guard_rvas[0]; guard++)
    {
        uint64_t address = FIXTURE_MODULE_BASE + guard_rvas[guard];
        fixture_add_block(test_fixture, address, guard_sizes[guard]);
        memcpy(fixture_bytes(test_fixture, address, guard_sizes[guard]), guards[guard],
               guard_sizes[guard]);
    }
    return test_fixture;
}

static fixture *player_pose_fixture(void)
{
    return player_pose_fixture_for(&profile_primary);
}

static fixture *compatibility_pose_fixture(void)
{
    fixture *test_fixture = player_pose_fixture_for(&profile_compatibility);
    const unsigned char position[] = {0x48, 0x8d, 0x81, 0x80, 2, 0, 0, 0xc3};
    const unsigned char owner[] = {0x49, 0x8b, 0x40, 8, 0x48, 0x89, 0x41, 0x60};
    const uint64_t getter = FIXTURE_MODULE_BASE + 0x6ee110u;
    const uint64_t owner_guard = FIXTURE_MODULE_BASE + 0x38523u;
    fixture_add_block(test_fixture, getter, sizeof position);
    fixture_add_block(test_fixture, owner_guard, sizeof owner);
    memcpy(fixture_bytes(test_fixture, getter, sizeof position), position, sizeof position);
    memcpy(fixture_bytes(test_fixture, owner_guard, sizeof owner), owner, sizeof owner);
    const uint32_t vtables[] = {profile_compatibility.player_actor_vtable_rva,
                                profile_compatibility.npc_entity_vtable_rva};
    for (unsigned index = 0; index < sizeof vtables / sizeof *vtables; index++)
    {
        uint64_t slot = FIXTURE_MODULE_BASE + vtables[index] + 0xb0;
        fixture_add_block(test_fixture, slot, sizeof(uint64_t));
        fixture_put_u64(test_fixture, slot, getter);
    }
    return test_fixture;
}

static void compatibility_camera_requires_the_pinned_layout(void)
{
    fixture *test_fixture = compatibility_pose_fixture();
    const unsigned char matrix[] = {0x0f, 0x59, 0x81, 0, 0x39, 1, 0};
    const unsigned char manager[] = {0x48, 0x8b, 0x90, 0xe8, 0x99, 1, 0};
    const unsigned char viewport[] = {
        0xf3, 0x41, 0x0f, 0x2a, 0x80, 0x8c, 0,    0,    0,    0xf3, 0x0f, 0x59, 0x15,
        0x6e, 0x92, 0x58, 1,    0xf3, 0x41, 0x0f, 0x2a, 0x88, 0x84, 0,    0,    0,
    };
    const uint32_t rvas[] = {COMPAT_MATRIX_GUARD_RVA, COMPAT_MANAGER_GUARD_RVA,
                             COMPAT_VIEWPORT_GUARD_RVA};
    const unsigned char *guards[] = {matrix, manager, viewport};
    const size_t sizes[] = {sizeof matrix, sizeof manager, sizeof viewport};
    for (unsigned index = 0; index < sizeof guards / sizeof *guards; index++)
    {
        uint64_t address = FIXTURE_MODULE_BASE + rvas[index];
        fixture_add_block(test_fixture, address, sizes[index]);
        memcpy(fixture_bytes(test_fixture, address, sizes[index]), guards[index], sizes[index]);
    }
    const uint32_t world_matrix_offset = 0x13900;
    const uint32_t scene_manager_offset = 0x199e8;
    const uint32_t viewport_offset = 0x84;
    rs3_camera sample = test_camera();
    fixture_add_block(test_fixture, FIXTURE_ACTOR_WORLD + world_matrix_offset,
                      sizeof sample.world_to_clip);
    memcpy(fixture_bytes(test_fixture, FIXTURE_ACTOR_WORLD + world_matrix_offset,
                         sizeof sample.world_to_clip),
           sample.world_to_clip, sizeof sample.world_to_clip);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_ACTOR + POSE_WORLD_OFFSET, FIXTURE_ACTOR_WORLD);
    fixture_add_block(test_fixture, FIXTURE_WORLD + scene_manager_offset, sizeof(uint64_t));
    fixture_put_u64(test_fixture, FIXTURE_WORLD + scene_manager_offset, FIXTURE_VIEWPORT_MANAGER);
    const int32_t viewport_bounds[] = {0, 0, 2560, 1377};
    fixture_add_block(test_fixture, FIXTURE_VIEWPORT_MANAGER + viewport_offset,
                      sizeof viewport_bounds);
    memcpy(fixture_bytes(test_fixture, FIXTURE_VIEWPORT_MANAGER + viewport_offset,
                         sizeof viewport_bounds),
           viewport_bounds, sizeof viewport_bounds);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    const rs3_spatial_info *spatial = &test_fixture->snapshot->spatial;
    assert(spatial->player.tile.valid && !(spatial->player.valid_mask & RS3_SPATIAL_FACING));
    assert(spatial->camera.valid_mask & RS3_CAMERA_MATRIX);
    assert(spatial->camera.valid_mask & RS3_CAMERA_VIEWPORT);
    assert(spatial->camera.viewport_width == 2560 && spatial->camera.viewport_height == 1377);
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + COMPAT_MANAGER_GUARD_RVA, 1)[0] = 0;
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(spatial->player.tile.valid && !spatial->camera.valid_mask);
    fixture_destroy(test_fixture);
}

static void scene_walk_uses_the_local_plane(void)
{
    fixture *test_fixture = compatibility_pose_fixture();
    const uint64_t menu = 0x940000, entries = 0x950000, entry = 0x960000;
    const uint64_t guard = FIXTURE_MODULE_BASE + 0x39024b;
    const uint64_t walk_action = FIXTURE_MODULE_BASE + 0x16be960;
    const uint32_t data_offset = 0x48;
    const unsigned char cycle_increment[] = {0xff, 0x81, 0xd0, 0x21, 0, 0};
    fixture_add_block(test_fixture, guard, sizeof cycle_increment);
    memcpy(fixture_bytes(test_fixture, guard, sizeof cycle_increment), cycle_increment,
           sizeof cycle_increment);
    fixture_add_block(test_fixture, menu, 0x21d8);
    fixture_add_block(test_fixture, entries, 16);
    fixture_add_block(test_fixture, entry, 0x54);
    fixture_add_block(test_fixture, walk_action + 0x40, sizeof(uint32_t));
    fixture_put_u64(test_fixture, FIXTURE_WORLD + 0x19938, menu);
    fixture_put_u64(test_fixture, menu, FIXTURE_MODULE_BASE + 0x150d5b0);
    fixture_put_u64(test_fixture, menu + 8, FIXTURE_WORLD);
    fixture_put_u64(test_fixture, menu + 0x90, entries);
    fixture_put_u64(test_fixture, menu + 0x98, entries + 16);
    fixture_put_u64(test_fixture, menu + 0x21d0, 24);
    fixture_put_u64(test_fixture, entries + 8, entry);
    fixture_put_u64(test_fixture, entry + 0x38, walk_action);
    fixture_put_u32(test_fixture, walk_action + 0x40, 23);
    fixture_put_u32(test_fixture, entry + data_offset + 4, 13266);
    fixture_put_u32(test_fixture, entry + data_offset + 8, 5095);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    rs3_pointer_walk walk;
    assert(rs3_snapshot_pointer_walk(test_fixture->snapshot, &walk, sizeof walk) == RS3_OK);
    assert(walk.valid && walk.menu_cycle == 24 && walk.action == 23);
    assert(walk.tile.valid && walk.tile.x == 13266 && walk.tile.y == 5095 && walk.tile.plane == 1);

    fixture_put_u32(test_fixture, entry + data_offset, 1);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(rs3_snapshot_pointer_walk(test_fixture->snapshot, &walk, sizeof walk) == RS3_OK);
    assert(walk.valid && walk.action == 23 && !walk.tile.valid);

    fixture_put_u32(test_fixture, entry + data_offset, 0);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_ACTOR + POSE_WORLD_OFFSET, 0);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(rs3_snapshot_pointer_walk(test_fixture->snapshot, &walk, sizeof walk) == RS3_OK);
    assert(walk.valid && walk.action == 23 && !walk.tile.valid);
    fixture_destroy(test_fixture);
}

/** Construct the original client's guarded closed-menu scene Walk source. */
static fixture *primary_walk_fixture(void)
{
    fixture *test_fixture = player_pose_fixture();
    test_fixture->context->target.module_size = FIXTURE_PRIMARY_MODULE_SIZE;
    static const struct
    {
        uint32_t rva;
        size_t size;
        unsigned char bytes[7];
    } guards[] = {
        {0x39032b, 6, {0xff, 0x81, 0xd0, 0x21, 0, 0}},
        {0x320c2f, 7, {0x48, 0x8b, 0x81, 0x38, 0x99, 1, 0}},
        {0x391305, 7, {0x48, 0x8b, 0x86, 0, 0x14, 0, 0}},
        {0x391d3f, 7, {0x80, 0xb9, 0x28, 0x14, 0, 0, 0}},
        {0x391d52, 7, {0xc6, 0x81, 0xb0, 0x17, 0, 0, 1}},
        {0x392689, 7, {0x44, 0x88, 0xb3, 0xb0, 0x17, 0, 0}},
    };
    for (unsigned index = 0; index < sizeof guards / sizeof *guards; index++)
    {
        uint64_t address = FIXTURE_MODULE_BASE + guards[index].rva;
        fixture_add_block(test_fixture, address, guards[index].size);
        memcpy(fixture_bytes(test_fixture, address, guards[index].size), guards[index].bytes,
               guards[index].size);
    }
    fixture_add_block(test_fixture, FIXTURE_MENU, MENU_CYCLE_OFFSET + sizeof(uint64_t));
    fixture_add_block(test_fixture, FIXTURE_MENU_RECORD, MENU_RECORD_BYTES);
    fixture_add_block(test_fixture,
                      FIXTURE_MODULE_BASE + PRIMARY_WALK_ACTION_RVA + MENU_ACTION_ID_OFFSET, 4);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + MENU_WORLD_MANAGER_OFFSET, FIXTURE_MENU);
    fixture_put_u64(test_fixture, FIXTURE_MENU, FIXTURE_MODULE_BASE + PRIMARY_MENU_VTABLE_RVA);
    fixture_put_u64(test_fixture, FIXTURE_MENU + MENU_OWNER_BACKLINK_OFFSET, FIXTURE_WORLD);
    fixture_put_u64(test_fixture, FIXTURE_MENU + MENU_CACHED_RECORD_OFFSET, FIXTURE_MENU_RECORD);
    fixture_put_u64(test_fixture, FIXTURE_MENU + MENU_CYCLE_OFFSET, 44);
    fixture_put_u64(test_fixture, FIXTURE_MENU_RECORD + MENU_RECORD_TYPE_OFFSET,
                    FIXTURE_MODULE_BASE + PRIMARY_WALK_ACTION_RVA);
    fixture_put_u32(test_fixture,
                    FIXTURE_MODULE_BASE + PRIMARY_WALK_ACTION_RVA + MENU_ACTION_ID_OFFSET, 23);
    /* Two valid inline strings: empty target name and the nine-byte verb. */
    fixture_bytes(test_fixture, FIXTURE_MENU_RECORD + MENU_STRING_TAG_OFFSET, 1)[0] = 23;
    memcpy(fixture_bytes(test_fixture, FIXTURE_MENU_RECORD + MENU_VERB_OFFSET, 9), "Walk here", 9);
    fixture_bytes(test_fixture, FIXTURE_MENU_RECORD + MENU_VERB_OFFSET + MENU_STRING_TAG_OFFSET,
                  1)[0] = 14;
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_RECORD_ITEM_OFFSET, UINT32_MAX);
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_WALK_X_OFFSET, 3005);
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_WALK_Y_OFFSET, 3006);
    return test_fixture;
}

static void primary_scene_walk_decodes_ground_and_selected_menu_backend(void)
{
    fixture *test_fixture = primary_walk_fixture();
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    const rs3_pointer_walk *walk = &test_fixture->snapshot->pointer_walk;
    assert(walk->valid && walk->action == 23 && walk->menu_cycle == 44);
    assert(walk->tile.valid && walk->tile.x == 3005 && walk->tile.y == 3006 &&
           walk->tile.plane == 1);
    fixture_bytes(test_fixture, FIXTURE_MENU + MENU_MODE_OFFSET, 1)[0] = 1;
    fixture_bytes(test_fixture, FIXTURE_MENU + MENU_MODERN_OPEN_OFFSET, 1)[0] = 1;
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(!walk->valid && !walk->tile.valid);
    fixture_bytes(test_fixture, FIXTURE_MENU + MENU_MODERN_OPEN_OFFSET, 1)[0] = 0;
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(walk->valid && walk->tile.valid);
    fixture_destroy(test_fixture);
}

static void primary_scene_walk_rejects_minimap_invalid_coordinates_and_missing_world(void)
{
    fixture *test_fixture = primary_walk_fixture();
    const rs3_pointer_walk *walk = &test_fixture->snapshot->pointer_walk;
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_WALK_DISCRIMINATOR, 1);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(walk->valid && !walk->tile.valid);
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_WALK_DISCRIMINATOR, 0);
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_WALK_X_OFFSET, UINT32_MAX);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(walk->valid && !walk->tile.valid);
    fixture_put_u32(test_fixture, FIXTURE_MENU_RECORD + MENU_WALK_X_OFFSET, 3005);
    fixture_put_u64(test_fixture, LOCAL_PLAYER_ACTOR + POSE_WORLD_OFFSET, 0);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(walk->valid && !walk->tile.valid);
    fixture_destroy(test_fixture);
}

static void primary_scene_walk_rejects_changed_cycle_vtable_and_descriptor(void)
{
    fixture *test_fixture = primary_walk_fixture();
    const rs3_pointer_walk *walk = &test_fixture->snapshot->pointer_walk;
    fixture_mutate_on_read(test_fixture, FIXTURE_MENU + MENU_CYCLE_OFFSET, 2,
                           FIXTURE_MENU + MENU_CYCLE_OFFSET, 45);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(!walk->valid && !walk->tile.valid);
    fixture_clear_mutation(test_fixture);
    fixture_put_u32(test_fixture,
                    FIXTURE_MODULE_BASE + PRIMARY_WALK_ACTION_RVA + MENU_ACTION_ID_OFFSET, 3);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(!walk->valid && !walk->tile.valid);
    fixture_put_u32(test_fixture,
                    FIXTURE_MODULE_BASE + PRIMARY_WALK_ACTION_RVA + MENU_ACTION_ID_OFFSET, 23);
    fixture_put_u64(test_fixture, FIXTURE_MENU, FIXTURE_MODULE_BASE + 0x150d5b0);
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(!walk->valid && !walk->tile.valid);
    fixture_destroy(test_fixture);
}

static void primary_scene_walk_rejects_failed_instruction_guard(void)
{
    fixture *test_fixture = primary_walk_fixture();
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + PRIMARY_MENU_GUARD_RVA, 1)[0] = 0;
    spatial_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD);
    assert(!test_fixture->snapshot->pointer_walk.valid);
    fixture_destroy(test_fixture);
}

static void original_position_and_hit_channel_share_committed_player(void)
{
    fixture *test_fixture = player_pose_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(fixture_decode(test_fixture));
    assert(snapshot->player_hit_info.status == RS3_AVAILABLE);
    assert(snapshot->player_hit_info.player_index == LOCAL_PLAYER_INDEX);
    assert(snapshot->spatial.player.instance_id == snapshot->player_hit_info.instance_id);
    assert(snapshot->spatial.player.instance_id != 0);
    rs3_position position;
    assert(rs3_snapshot_position(snapshot, &position, sizeof position) == RS3_OK);
    assert(position.valid && position.x == 3000 && position.y == 3001 && position.plane == 1);
    assert(memcmp(&position, &snapshot->spatial.player.tile, sizeof position) == 0);
    float next_position[3] = {512.0f * 3002.5f, 100.0f, 512.0f * 3004.5f};
    memcpy(fixture_bytes(test_fixture, LOCAL_PLAYER_ACTOR + POSE_FINE_OFFSET, sizeof next_position),
           next_position, sizeof next_position);
    assert(fixture_decode(test_fixture));
    assert(snapshot->position.x == 3002 && snapshot->position.y == 3004);
    /* A code guard failure clears both position views, while hit sampling can continue. */
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + POSITION_GETTER_RVA, 1)[0] = 0;
    assert(fixture_decode(test_fixture));
    assert(snapshot->spatial.status == RS3_UNAVAILABLE && !snapshot->position.valid);
    assert(!snapshot->spatial.player.tile.valid && !snapshot->spatial.player.instance_id);
    assert(snapshot->player_hit_info.status == RS3_AVAILABLE);
    fixture_destroy(test_fixture);
}

static void rejects_pose_from_actor_different_from_hit_channel(void)
{
    fixture *test_fixture = player_pose_fixture();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->player_actor_address = LOCAL_PLAYER_ACTOR + 8;
    snapshot->player_hit_info.status = RS3_AVAILABLE;
    snapshot->player_hit_info.player_index = LOCAL_PLAYER_INDEX;
    snapshot->player_hit_info.instance_id = 99;
    spatial_stage(test_fixture->context, snapshot, FIXTURE_WORLD);
    spatial_commit(test_fixture->context, snapshot);
    assert(!snapshot->spatial.player.tile.valid && !snapshot->position.valid);
    assert(!snapshot->spatial.player.instance_id);
    assert(snapshot->spatial.player.tile_reason == RS3_READ_FAILED);
    fixture_destroy(test_fixture);
}

static void spatial_commit_preserves_compatibility_position_decoder(void)
{
    fixture *test_fixture = fixture_create_for_profile(&profile_compatibility);
    rs3_snapshot *snapshot = test_fixture->snapshot;
    snapshot->position = (rs3_position){1, 3166, 3200, 0};
    spatial_stage(test_fixture->context, snapshot, FIXTURE_WORLD);
    spatial_commit(test_fixture->context, snapshot);
    assert(snapshot->spatial.status == RS3_UNAVAILABLE);
    assert(snapshot->position.valid && snapshot->position.x == 3166);
    fixture_destroy(test_fixture);
}

static fixture *player_target_fixture(void)
{
    fixture *test_fixture = player_pose_fixture();
    const unsigned char kind_guard[] = {0x80, 0xb9, 0x38, 0x02, 0, 0, 0x7f};
    const unsigned char uid_guard[] = {0x0f, 0xb6, 0x81, 0x38, 0x02, 0, 0,
                                       0x48, 0x63, 0x91, 0xc4, 0x01, 0, 0};
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + TARGET_KIND_GUARD_RVA, sizeof kind_guard);
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + TARGET_UID_GUARD_RVA, sizeof uid_guard);
    memcpy(
        fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + TARGET_KIND_GUARD_RVA, sizeof kind_guard),
        kind_guard, sizeof kind_guard);
    memcpy(
        fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + TARGET_UID_GUARD_RVA, sizeof uid_guard),
        uid_guard, sizeof uid_guard);
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_UID_OFFSET,
                    FIXTURE_NPC_RUNTIME_ID);
    fixture_bytes(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_NPC;
    return test_fixture;
}

static void decodes_npc_target_changes_and_explicit_clear(void)
{
    fixture *test_fixture = player_target_fixture();
    assert(fixture_decode(test_fixture));
    rs3_actor_spatial *player = &test_fixture->snapshot->spatial.player;
    assert(player->valid_mask & RS3_SPATIAL_TARGET);
    assert(player->target_kind == RS3_SPATIAL_TARGET_NPC);
    assert(player->target_runtime_id == FIXTURE_NPC_RUNTIME_ID && !player->target_reason);
    assert(!(test_fixture->snapshot->npc_spatial[0].valid_mask & RS3_SPATIAL_TARGET));
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_UID_OFFSET,
                    FIXTURE_NPC_RUNTIME_ID + 1);
    assert(fixture_decode(test_fixture));
    assert(player->target_runtime_id == FIXTURE_NPC_RUNTIME_ID + 1);
    fixture_bytes(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        EXPLICIT_NO_TARGET_KIND;
    assert(fixture_decode(test_fixture));
    assert(player->valid_mask & RS3_SPATIAL_TARGET);
    assert(player->target_kind == RS3_SPATIAL_TARGET_NONE && player->target_runtime_id == -1);
    fixture_destroy(test_fixture);
}

static void unknown_target_kinds_and_out_of_range_ids_stay_unknown(void)
{
    fixture *test_fixture = player_target_fixture();
    fixture_bytes(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] = 2;
    assert(fixture_decode(test_fixture));
    rs3_actor_spatial *player = &test_fixture->snapshot->spatial.player;
    assert(!(player->valid_mask & RS3_SPATIAL_TARGET));
    assert(player->target_reason == RS3_SPATIAL_UNMAPPED);
    fixture_bytes(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_NPC;
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_UID_OFFSET, UINT32_MAX);
    assert(fixture_decode(test_fixture));
    assert(!(player->valid_mask & RS3_SPATIAL_TARGET));
    fixture_put_u32(test_fixture, LOCAL_PLAYER_ACTOR + ACTOR_TARGET_UID_OFFSET,
                    (uint32_t)UINT16_MAX + 1);
    assert(fixture_decode(test_fixture));
    assert(!(player->valid_mask & RS3_SPATIAL_TARGET));
    fixture_destroy(test_fixture);
}

static void target_read_tearing_and_failed_guard_preserve_pose_only(void)
{
    fixture *test_fixture = player_target_fixture();
    fixture_mutate_on_read(test_fixture, LOCAL_PLAYER_ACTOR, 2,
                           LOCAL_PLAYER_ACTOR + ACTOR_TARGET_UID_OFFSET,
                           FIXTURE_NPC_RUNTIME_ID + 1);
    rs3_actor_spatial actor;
    assert(spatial_decode_actor(test_fixture->context, LOCAL_PLAYER_ACTOR,
                                profile_primary.player_actor_vtable_rva, LOCAL_PLAYER_INDEX,
                                &actor));
    assert(actor.valid_mask & RS3_SPATIAL_TILE);
    assert(!(actor.valid_mask & RS3_SPATIAL_TARGET) && actor.target_reason == RS3_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + TARGET_KIND_GUARD_RVA, 1)[0] = 0;
    assert(spatial_decode_actor(test_fixture->context, LOCAL_PLAYER_ACTOR,
                                profile_primary.player_actor_vtable_rva, LOCAL_PLAYER_INDEX,
                                &actor));
    assert(actor.valid_mask & RS3_SPATIAL_TILE);
    assert(!(actor.valid_mask & RS3_SPATIAL_TARGET));
    assert(actor.target_reason == RS3_SPATIAL_UNMAPPED);
    fixture_destroy(test_fixture);
}

static void npc_entity_references_decode_player_npc_and_clear_kinds(void)
{
    fixture *test_fixture = player_target_fixture();
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, LOCAL_PLAYER_INDEX);
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_PLAYER;
    assert(fixture_decode(test_fixture));
    rs3_actor_spatial *npc = &test_fixture->snapshot->npc_spatial[0];
    assert(npc->valid_mask & RS3_SPATIAL_TARGET);
    assert(npc->target_kind == RS3_SPATIAL_TARGET_PLAYER);
    assert(npc->target_runtime_id == LOCAL_PLAYER_INDEX && npc->target_reason == RS3_REASON_NONE);
    assert(npc->instance_id == test_fixture->snapshot->npcs[0].instance_id);
    /* A foreign player UID is a typed reference; the JSON layer cannot resolve its lifetime. */
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, LOCAL_PLAYER_INDEX + 1);
    assert(fixture_decode(test_fixture));
    assert(npc->target_runtime_id == LOCAL_PLAYER_INDEX + 1);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, FIXTURE_NPC_RUNTIME_ID);
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_NPC;
    assert(fixture_decode(test_fixture));
    assert(npc->target_kind == RS3_SPATIAL_TARGET_NPC);
    assert(npc->target_runtime_id == FIXTURE_NPC_RUNTIME_ID);
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        EXPLICIT_NO_TARGET_KIND;
    assert(fixture_decode(test_fixture));
    assert(npc->target_kind == RS3_SPATIAL_TARGET_NONE && npc->target_runtime_id == -1);
    fixture_destroy(test_fixture);
}

static void npc_target_unknown_kind_and_invalid_uid_preserve_unknown(void)
{
    fixture *test_fixture = player_target_fixture();
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] = 3;
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, LOCAL_PLAYER_INDEX);
    assert(fixture_decode(test_fixture));
    rs3_actor_spatial *npc = &test_fixture->snapshot->npc_spatial[0];
    assert(!(npc->valid_mask & RS3_SPATIAL_TARGET));
    assert(npc->target_reason == RS3_SPATIAL_UNMAPPED);
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_PLAYER;
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, UINT32_MAX);
    assert(fixture_decode(test_fixture));
    assert(!(npc->valid_mask & RS3_SPATIAL_TARGET));
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET,
                    (uint32_t)UINT16_MAX + 1);
    assert(fixture_decode(test_fixture));
    assert(!(npc->valid_mask & RS3_SPATIAL_TARGET));
    fixture_destroy(test_fixture);
}

static void npc_target_tearing_guard_and_world_fail_closed(void)
{
    fixture *test_fixture = player_target_fixture();
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_PLAYER;
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, LOCAL_PLAYER_INDEX);
    fixture_mutate_on_read(test_fixture, FIXTURE_ACTOR, 2, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET,
                           EXPLICIT_NO_TARGET_KIND);
    rs3_actor_spatial actor;
    assert(spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                profile_primary.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                &actor));
    assert(actor.valid_mask & RS3_SPATIAL_TILE);
    assert(!(actor.valid_mask & RS3_SPATIAL_TARGET) && actor.target_reason == RS3_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_PLAYER;
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + POSE_WORLD_OFFSET, FIXTURE_WORLD + 8);
    assert(fixture_decode(test_fixture));
    /* The raw reference is available, but publication must require SAME_WORLD. */
    assert(test_fixture->snapshot->npc_spatial[0].valid_mask & RS3_SPATIAL_TARGET);
    assert(!(test_fixture->snapshot->npc_spatial[0].valid_mask & RS3_SPATIAL_SAME_WORLD));
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + TARGET_UID_GUARD_RVA, 1)[0] = 0;
    assert(fixture_decode(test_fixture));
    assert(!(test_fixture->snapshot->npc_spatial[0].valid_mask & RS3_SPATIAL_TARGET));
    assert(test_fixture->snapshot->npc_spatial[0].valid_mask & RS3_SPATIAL_TILE);
    fixture_destroy(test_fixture);
}

static void npc_target_uid_tearing_and_unverified_profile_stay_unknown(void)
{
    fixture *test_fixture = player_target_fixture();
    fixture_bytes(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_KIND_OFFSET, 1)[0] =
        RS3_SPATIAL_TARGET_PLAYER;
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET, LOCAL_PLAYER_INDEX);
    fixture_mutate_on_read(test_fixture, FIXTURE_ACTOR, 2, FIXTURE_ACTOR + ACTOR_TARGET_UID_OFFSET,
                           LOCAL_PLAYER_INDEX + 1);
    rs3_actor_spatial actor;
    assert(spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                profile_primary.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                &actor));
    assert(actor.valid_mask & RS3_SPATIAL_TILE);
    assert(!(actor.valid_mask & RS3_SPATIAL_TARGET) && actor.target_reason == RS3_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    test_fixture->context->target.profile = &profile_compatibility;
    fixture_put_u64(test_fixture, FIXTURE_ACTOR,
                    FIXTURE_MODULE_BASE + profile_compatibility.npc_entity_vtable_rva);
    assert(spatial_decode_actor(test_fixture->context, FIXTURE_ACTOR,
                                profile_compatibility.npc_entity_vtable_rva, FIXTURE_NPC_RUNTIME_ID,
                                &actor));
    assert(!(actor.valid_mask & RS3_SPATIAL_TARGET));
    assert(actor.target_reason == RS3_SPATIAL_UNMAPPED);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(decodes_pose_but_does_not_fabricate_target);
    RUN_TEST(rejects_torn_and_nonfinite_poses);
    RUN_TEST(compatibility_npc_tiles_require_the_shared_position_getter);
    RUN_TEST(projects_with_physical_origin_and_y_inversion);
    RUN_TEST(projects_compatibility_viewport_at_physical_size);
    RUN_TEST(rejects_unknown_height_plane_and_abi);
    RUN_TEST(snapshot_accessors_enforce_capacity_and_preserve_identity);
    RUN_TEST(compatibility_camera_requires_the_pinned_layout);
    RUN_TEST(scene_walk_uses_the_local_plane);
    RUN_TEST(primary_scene_walk_decodes_ground_and_selected_menu_backend);
    RUN_TEST(primary_scene_walk_rejects_minimap_invalid_coordinates_and_missing_world);
    RUN_TEST(primary_scene_walk_rejects_changed_cycle_vtable_and_descriptor);
    RUN_TEST(primary_scene_walk_rejects_failed_instruction_guard);
    RUN_TEST(original_position_and_hit_channel_share_committed_player);
    RUN_TEST(rejects_pose_from_actor_different_from_hit_channel);
    RUN_TEST(spatial_commit_preserves_compatibility_position_decoder);
    RUN_TEST(decodes_npc_target_changes_and_explicit_clear);
    RUN_TEST(unknown_target_kinds_and_out_of_range_ids_stay_unknown);
    RUN_TEST(target_read_tearing_and_failed_guard_preserve_pose_only);
    RUN_TEST(npc_entity_references_decode_player_npc_and_clear_kinds);
    RUN_TEST(npc_target_unknown_kind_and_invalid_uid_preserve_unknown);
    RUN_TEST(npc_target_tearing_guard_and_world_fail_closed);
    RUN_TEST(npc_target_uid_tearing_and_unverified_profile_stay_unknown);
    return 0;
}
