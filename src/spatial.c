/* Read-only actor poses and projection; unmapped targets never become "no target". */
#include "spatial.h"

#include <math.h>
#include <string.h>

#include "clock.h"
#include "decode_utils.h"
#include "memory.h"
#include "position.h"

#define ACTOR_POSE_BYTES           0x290u
#define ACTOR_RUNTIME_OFFSET       0x88u
#define ACTOR_PLANE_OFFSET         0x50u
#define ACTOR_WORLD_OFFSET         0x60u
#define ACTOR_SIZE_OFFSET          0x18cu
#define ACTOR_QUATERNION_OFFSET    0x1ccu
#define ACTOR_FINE_OFFSET          0x280u
#define WORLD_TO_CLIP_OFFSET       0x13900u
#define SCENE_MANAGER_OFFSET       0x199e8u
#define VIEWPORT_OFFSET            0x84u
#define FINE_UNITS_PER_TILE        512.0
#define MAX_FOOTPRINT              64u
#define MAX_VIEWPORT_DIMENSION     32768
#define QUATERNION_NORM_TOLERANCE  0.02
#define DEGREES_PER_RADIAN         57.29577951308232
#define MIN_CLIP_W                 0.0001
#define POSITION_GETTER_RVA        0x6ee1f0u
#define COMPAT_POSITION_GETTER_RVA 0x6ee110u
#define COMPAT_ACTOR_WORLD_RVA     0x38523u
#define ACTOR_POSITION_VTABLE_SLOT 0xb0u
#define FACING_GETTER_RVA          0x44b690u
#define PROJECTION_INSTRUCTION_RVA 0x5a34eu

_Static_assert(sizeof(rs3_actor_spatial) == 88, "ABI spatial actor");
_Static_assert(sizeof(rs3_camera) == 136, "ABI camera");
_Static_assert(sizeof(rs3_spatial_info) == 272, "ABI spatial info");
_Static_assert(sizeof(rs3_screen_point) == 24, "ABI screen point");

/** Exact additional instruction guards for the original profile's newly followed fields. */
static int spatial_layout_verified(rs3_context *context)
{
    if (context->target.profile != &profile_primary)
    {
        return 0;
    }
    static const unsigned char position_getter[] = {0x48, 0x8d, 0x81, 0x80, 0x02, 0, 0, 0xc3};
    static const unsigned char facing_getter[] = {0x48, 0x83, 0xec, 0x68, 0xf3,
                                                  0x0f, 0x10, 0x69, 0x1c};
    static const unsigned char projection_instruction[] = {0x0f, 0x59, 0x81, 0x00,
                                                           0x39, 0x01, 0x00};
    unsigned char bytes[sizeof facing_getter];
    return memory_read(context, context->target.module_base + POSITION_GETTER_RVA, bytes,
                       sizeof position_getter) &&
           memcmp(bytes, position_getter, sizeof position_getter) == 0 &&
           memory_read(context, context->target.module_base + FACING_GETTER_RVA, bytes,
                       sizeof facing_getter) &&
           memcmp(bytes, facing_getter, sizeof facing_getter) == 0 &&
           memory_read(context, context->target.module_base + PROJECTION_INSTRUCTION_RVA, bytes,
                       sizeof projection_instruction) &&
           memcmp(bytes, projection_instruction, sizeof projection_instruction) == 0;
}

/** Compatibility Mode shares the tile and world-owner fields across player and NPC actors. */
static int compatibility_tiles_verified(rs3_context *context)
{
    const client_profile *profile = context->target.profile;
    if (profile != &profile_compatibility)
    {
        return 0;
    }
    static const unsigned char position_getter[] = {0x48, 0x8d, 0x81, 0x80, 0x02, 0, 0, 0xc3};
    static const unsigned char world_owner[] = {0x49, 0x8b, 0x40, 0x08, 0x48, 0x89, 0x41, 0x60};
    unsigned char bytes[sizeof position_getter];
    uint64_t base = context->target.module_base;
    uint64_t player_getter, npc_getter;
    return memory_read(context, base + COMPAT_POSITION_GETTER_RVA, bytes, sizeof bytes) &&
           memcmp(bytes, position_getter, sizeof bytes) == 0 &&
           memory_read(context, base + COMPAT_ACTOR_WORLD_RVA, bytes, sizeof bytes) &&
           memcmp(bytes, world_owner, sizeof bytes) == 0 &&
           memory_read_pointer(context,
                               base + profile->player_actor_vtable_rva + ACTOR_POSITION_VTABLE_SLOT,
                               &player_getter) &&
           memory_read_pointer(context,
                               base + profile->npc_entity_vtable_rva + ACTOR_POSITION_VTABLE_SLOT,
                               &npc_getter) &&
           player_getter == base + COMPAT_POSITION_GETTER_RVA && npc_getter == player_getter;
}

int spatial_decode_actor(rs3_context *context, uint64_t actor, uint32_t vtable_rva,
                         uint32_t runtime_id, rs3_actor_spatial *out)
{
    unsigned char before[ACTOR_POSE_BYTES];
    unsigned char after[ACTOR_POSE_BYTES];
    memset(out, 0, sizeof *out);
    out->runtime_id = runtime_id;
    out->tile_reason = RS3_READ_FAILED;
    out->facing_reason = RS3_READ_FAILED;
    out->target_reason = RS3_SPATIAL_UNMAPPED;
    out->target_runtime_id = -1;
    if (!actor || !vtable_rva || runtime_id > UINT16_MAX ||
        !memory_read(context, actor, before, sizeof before) ||
        !memory_read(context, actor, after, sizeof after) ||
        decode_u64(before) != context->target.module_base + vtable_rva ||
        decode_u64(after) != decode_u64(before) ||
        decode_u32(before + ACTOR_RUNTIME_OFFSET) % 65536u != runtime_id ||
        decode_u32(after + ACTOR_RUNTIME_OFFSET) % 65536u != runtime_id)
    {
        return 0;
    }
    const uint32_t offsets[] = {ACTOR_PLANE_OFFSET, ACTOR_SIZE_OFFSET, ACTOR_FINE_OFFSET,
                                ACTOR_WORLD_OFFSET};
    const uint32_t lengths[] = {4, 4, 12, 8};
    for (unsigned index = 0; index < sizeof offsets / sizeof offsets[0]; index++)
    {
        if (memcmp(before + offsets[index], after + offsets[index], lengths[index]) != 0)
        {
            return 0;
        }
    }
    int32_t plane = decode_i32(before + ACTOR_PLANE_OFFSET);
    uint32_t size = decode_u32(before + ACTOR_SIZE_OFFSET);
    float position[3];
    memcpy(position, before + ACTOR_FINE_OFFSET, sizeof position);
    if (plane >= 0 && plane <= 3 && size >= 1 && size <= MAX_FOOTPRINT && isfinite(position[0]) &&
        isfinite(position[1]) && isfinite(position[2]))
    {
        double x = floor(position[0] / FINE_UNITS_PER_TILE - size / 2u);
        double y = floor(position[2] / FINE_UNITS_PER_TILE - size / 2u);
        if (x >= 0 && x < MAX_TILE_COORDINATE && y >= 0 && y < MAX_TILE_COORDINATE)
        {
            out->tile = (rs3_position){1, (int32_t)x, (int32_t)y, plane};
            out->footprint_size = size;
            out->fine_x = position[0];
            out->fine_height = position[1];
            out->fine_z = position[2];
            out->valid_mask |= RS3_SPATIAL_TILE | RS3_SPATIAL_FINE_POSITION;
            out->tile_reason = RS3_REASON_NONE;
        }
    }
    if (context->target.profile == &profile_compatibility)
    {
        out->facing_reason = RS3_SPATIAL_UNMAPPED;
        out->observed_ns = clock_monotonic_ns();
        return out->valid_mask != 0;
    }
    float q[4];
    memcpy(q, before + ACTOR_QUATERNION_OFFSET, sizeof q);
    double norm =
        (double)q[0] * q[0] + (double)q[1] * q[1] + (double)q[2] * q[2] + (double)q[3] * q[3];
    if (memcmp(before + ACTOR_QUATERNION_OFFSET, after + ACTOR_QUATERNION_OFFSET, sizeof q) == 0 &&
        isfinite(norm) && fabs(norm - 1.0) <= QUATERNION_NORM_TOLERANCE)
    {
        double yaw =
            atan2(2.0 * (q[0] * q[2] + q[3] * q[1]), 1.0 - 2.0 * (q[0] * q[0] + q[1] * q[1])) *
            DEGREES_PER_RADIAN;
        out->facing_degrees = (float)(yaw < 0 ? yaw + 360.0 : yaw);
        out->valid_mask |= RS3_SPATIAL_FACING;
        out->facing_reason = RS3_REASON_NONE;
    }
    out->observed_ns = clock_monotonic_ns();
    return out->valid_mask != 0;
}

typedef struct spatial_window
{
    DWORD pid;
    HWND window;
    unsigned matches;
} spatial_window;

static BOOL CALLBACK find_spatial_window(HWND window, LPARAM parameter)
{
    spatial_window *selection = (spatial_window *)parameter;
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (pid == selection->pid && IsWindowVisible(window) && GetWindow(window, GW_OWNER) == NULL)
    {
        selection->window = window;
        selection->matches++;
    }
    return TRUE;
}

/** Read physical window geometry without moving, focusing or clicking it. */
static int physical_origin(uint32_t pid, rs3_camera *camera)
{
    spatial_window selection = {.pid = pid};
    EnumWindows(find_spatial_window, (LPARAM)&selection);
    if (selection.matches != 1 || IsIconic(selection.window))
    {
        return 0;
    }
    DPI_AWARENESS_CONTEXT previous =
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    if (previous == NULL)
    {
        return 0;
    }
    RECT client;
    POINT origin = {0};
    int valid = GetClientRect(selection.window, &client) &&
                ClientToScreen(selection.window, &origin) && camera->viewport_x >= 0 &&
                camera->viewport_y >= 0 &&
                (int64_t)camera->viewport_x + camera->viewport_width <= client.right &&
                (int64_t)camera->viewport_y + camera->viewport_height <= client.bottom;
    SetThreadDpiAwarenessContext(previous);
    if (valid)
    {
        camera->client_screen_x = origin.x;
        camera->client_screen_y = origin.y;
    }
    return valid;
}

static void read_camera(rs3_context *context, uint64_t world_state, uint64_t actor,
                        rs3_camera *camera)
{
    camera->reason = RS3_READ_FAILED;
    uint64_t world, world_after, manager, manager_after;
    float matrix_after[16];
    int32_t viewport[4], viewport_after[4];
    if (!memory_read_pointer(context, actor + ACTOR_WORLD_OFFSET, &world) || world == 0 ||
        !memory_read_pointer(context, world_state + SCENE_MANAGER_OFFSET, &manager) ||
        manager == 0 ||
        !memory_read(context, world + WORLD_TO_CLIP_OFFSET, camera->world_to_clip,
                     sizeof matrix_after) ||
        !memory_read(context, manager + VIEWPORT_OFFSET, viewport, sizeof viewport) ||
        !memory_read(context, world + WORLD_TO_CLIP_OFFSET, matrix_after, sizeof matrix_after) ||
        !memory_read(context, manager + VIEWPORT_OFFSET, viewport_after, sizeof viewport_after) ||
        !memory_read_pointer(context, actor + ACTOR_WORLD_OFFSET, &world_after) ||
        world_after != world ||
        !memory_read_pointer(context, world_state + SCENE_MANAGER_OFFSET, &manager_after) ||
        manager_after != manager ||
        memcmp(matrix_after, camera->world_to_clip, sizeof matrix_after) ||
        memcmp(viewport, viewport_after, sizeof viewport))
    {
        return;
    }
    double magnitude = 0;
    for (unsigned index = 0; index < 16; index++)
    {
        if (!isfinite(matrix_after[index]))
        {
            camera->reason = RS3_SPATIAL_NONFINITE;
            return;
        }
        magnitude += fabs(matrix_after[index]);
    }
    if (magnitude == 0 || viewport[2] <= 0 || viewport[3] <= 0 ||
        viewport[2] > MAX_VIEWPORT_DIMENSION || viewport[3] > MAX_VIEWPORT_DIMENSION)
    {
        return;
    }
    camera->viewport_x = viewport[0];
    camera->viewport_y = viewport[1];
    camera->viewport_width = viewport[2];
    camera->viewport_height = viewport[3];
    camera->valid_mask = RS3_CAMERA_MATRIX | RS3_CAMERA_VIEWPORT;
    camera->reason = RS3_SPATIAL_WINDOW_UNKNOWN;
    if (physical_origin(context->target.pid, camera))
    {
        camera->valid_mask |= RS3_CAMERA_PHYSICAL_ORIGIN;
        camera->reason = RS3_REASON_NONE;
    }
    camera->observed_ns = clock_monotonic_ns();
}

void spatial_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    rs3_spatial_info *info = &snapshot->spatial;
    memset(info, 0, sizeof *info);
    info->status = RS3_UNAVAILABLE;
    info->reason = RS3_UNSUPPORTED_CLIENT;
    info->player.tile_reason = RS3_UNSUPPORTED_CLIENT;
    info->player.facing_reason = RS3_UNSUPPORTED_CLIENT;
    info->player.target_reason = RS3_SPATIAL_UNMAPPED;
    info->player.target_runtime_id = -1;
    info->camera.reason = RS3_UNSUPPORTED_CLIENT;
    int full_layout = spatial_layout_verified(context);
    if (!full_layout && !compatibility_tiles_verified(context))
    {
        return;
    }
    info->status = RS3_PARTIAL;
    info->reason = RS3_SPATIAL_UNMAPPED;
    info->player.tile_reason = RS3_READ_FAILED;
    info->player.facing_reason = RS3_READ_FAILED;
    uint64_t actor;
    uint64_t player_world = 0;
    uint32_t index;
    if (position_find_local_actor(context, world_state, &actor, &index) &&
        (snapshot->player_actor_address == 0 || (actor == snapshot->player_actor_address &&
                                                 index == snapshot->player_hit_info.player_index)))
    {
        spatial_decode_actor(context, actor, context->target.profile->player_actor_vtable_rva,
                             index, &info->player);
        if (info->player.valid_mask & RS3_SPATIAL_FINE_POSITION)
        {
            if (memory_read_pointer(context, actor + ACTOR_WORLD_OFFSET, &player_world) &&
                player_world)
            {
                info->player.valid_mask |= RS3_SPATIAL_SAME_WORLD;
            }
            if (full_layout)
            {
                read_camera(context, world_state, actor, &info->camera);
                info->camera.plane = info->player.tile.plane;
            }
        }
    }
    info->npc_count = snapshot->info.npc_count;
    for (uint32_t npc = 0; npc < info->npc_count; npc++)
    {
        spatial_decode_actor(context, context->npcs.staged_actor_addresses[npc],
                             context->target.profile->npc_entity_vtable_rva,
                             snapshot->npcs[npc].runtime_id, &snapshot->npc_spatial[npc]);
        uint64_t npc_world = 0;
        if (player_world &&
            memory_read_pointer(context,
                                context->npcs.staged_actor_addresses[npc] + ACTOR_WORLD_OFFSET,
                                &npc_world) &&
            npc_world == player_world)
        {
            snapshot->npc_spatial[npc].valid_mask |= RS3_SPATIAL_SAME_WORLD;
        }
    }
}

void spatial_commit(rs3_context *context, rs3_snapshot *snapshot)
{
    rs3_spatial_info *info = &snapshot->spatial;
    info->sample_sequence = snapshot->info.sequence;
    info->observed_ns = snapshot->info.monotonic_ns;
    info->scene_epoch = snapshot->combat.scene_epoch;
    info->process_creation_time = context->target.process_creation_time;
    info->pid = context->target.pid;
    const uint32_t required_player_flags = RS3_SPATIAL_TILE | RS3_SPATIAL_SAME_WORLD;
    int player_pose_available =
        info->status != RS3_UNAVAILABLE &&
        (info->player.valid_mask & required_player_flags) == required_player_flags &&
        info->player.tile.valid;
    if (player_pose_available && snapshot->player_hit_info.status == RS3_AVAILABLE &&
        info->player.runtime_id == snapshot->player_hit_info.player_index)
    {
        info->player.instance_id = snapshot->player_hit_info.instance_id;
    }
    /* Reuse the guarded pose after final frame validation; never perform a second lookup. */
    if (context->target.profile == &profile_primary)
    {
        snapshot->position = player_pose_available ? info->player.tile : (rs3_position){0};
    }
    info->camera.scene_epoch = info->scene_epoch;
    info->camera.process_creation_time = info->process_creation_time;
    info->camera.pid = info->pid;
    for (uint32_t npc = 0; npc < info->npc_count; npc++)
    {
        snapshot->npc_spatial[npc].instance_id = snapshot->npcs[npc].instance_id;
    }
}

uint32_t rs3_spatial_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_SPATIAL_SIZE_ACTOR:
            return sizeof(rs3_actor_spatial);
        case RS3_SPATIAL_SIZE_CAMERA:
            return sizeof(rs3_camera);
        case RS3_SPATIAL_SIZE_INFO:
            return sizeof(rs3_spatial_info);
        case RS3_SPATIAL_SIZE_SCREEN_POINT:
            return sizeof(rs3_screen_point);
        default:
            return 0;
    }
}

static int32_t project(const rs3_camera *camera, int32_t plane, double x, double height, double z,
                       rs3_screen_point *out, uint32_t out_size)
{
    if (camera == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    if (out_size < sizeof *out)
    {
        return RS3_E_ABI;
    }
    memset(out, 0, sizeof *out);
    out->reason = camera->reason ? camera->reason : RS3_SPATIAL_UNMAPPED;
    if ((camera->valid_mask & RS3_CAMERA_COMPLETE) != RS3_CAMERA_COMPLETE)
    {
        return RS3_OK;
    }
    if (plane != camera->plane)
    {
        out->reason = RS3_SPATIAL_WRONG_PLANE;
        return RS3_OK;
    }
    if (!isfinite(x) || !isfinite(height) || !isfinite(z))
    {
        out->reason = RS3_SPATIAL_NONFINITE;
        return RS3_OK;
    }
    const float *matrix = camera->world_to_clip;
    double clip_x = x * matrix[0] + height * matrix[4] + z * matrix[8] + matrix[12];
    double clip_y = x * matrix[1] + height * matrix[5] + z * matrix[9] + matrix[13];
    double clip_w = x * matrix[3] + height * matrix[7] + z * matrix[11] + matrix[15];
    if (!isfinite(clip_x) || !isfinite(clip_y) || !isfinite(clip_w))
    {
        out->reason = RS3_SPATIAL_NONFINITE;
        return RS3_OK;
    }
    out->clip_w = (float)clip_w;
    if (clip_w <= MIN_CLIP_W)
    {
        out->reason = RS3_SPATIAL_BEHIND_CAMERA;
        return RS3_OK;
    }
    double nx = clip_x / clip_w, ny = -clip_y / clip_w;
    if (nx < -1 || nx >= 1 || ny < -1 || ny >= 1 || camera->viewport_width <= 0 ||
        camera->viewport_height <= 0)
    {
        out->reason = RS3_SPATIAL_OFF_SCREEN;
        return RS3_OK;
    }
    double pixel_x = camera->client_screen_x + (double)camera->viewport_x +
                     (nx + 1) * camera->viewport_width / 2;
    double pixel_y = camera->client_screen_y + (double)camera->viewport_y +
                     (ny + 1) * camera->viewport_height / 2;
    if (pixel_x < INT32_MIN || pixel_x > INT32_MAX || pixel_y < INT32_MIN || pixel_y > INT32_MAX)
    {
        out->reason = RS3_SPATIAL_OFF_SCREEN;
        return RS3_OK;
    }
    out->x = (int32_t)floor(pixel_x);
    out->y = (int32_t)floor(pixel_y);
    out->valid = 1;
    out->reason = RS3_REASON_NONE;
    return RS3_OK;
}

int32_t rs3_project_tile(const rs3_camera *camera, const rs3_position *tile, float fine_height,
                         rs3_screen_point *out, uint32_t out_size)
{
    if (tile == NULL || !tile->valid)
    {
        return RS3_E_ARGUMENT;
    }
    return project(camera, tile->plane, ((double)tile->x + 0.5) * FINE_UNITS_PER_TILE, fine_height,
                   ((double)tile->y + 0.5) * FINE_UNITS_PER_TILE, out, out_size);
}

int32_t rs3_project_actor(const rs3_camera *camera, const rs3_actor_spatial *actor,
                          float height_offset, rs3_screen_point *out, uint32_t out_size)
{
    if (actor == NULL || !(actor->valid_mask & RS3_SPATIAL_FINE_POSITION))
    {
        return RS3_E_ARGUMENT;
    }
    if (!(actor->valid_mask & RS3_SPATIAL_SAME_WORLD))
    {
        if (out == NULL)
        {
            return RS3_E_ARGUMENT;
        }
        if (out_size < sizeof *out)
        {
            return RS3_E_ABI;
        }
        memset(out, 0, sizeof *out);
        out->reason = RS3_SPATIAL_WRONG_WORLD;
        return RS3_OK;
    }
    return project(camera, actor->tile.plane, actor->fine_x,
                   (double)actor->fine_height + height_offset, actor->fine_z, out, out_size);
}
