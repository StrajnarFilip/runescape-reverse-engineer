/* Additive, caller-owned spatial observations. No input or game calls. */
#ifndef RS3_SPATIAL_H
#define RS3_SPATIAL_H

#include "rs3.h"

#ifdef __cplusplus
extern "C"
{
#endif

enum rs3_spatial_flags
{
    RS3_SPATIAL_TILE = 1u,
    RS3_SPATIAL_FINE_POSITION = 2u,
    RS3_SPATIAL_FACING = 4u,
    RS3_SPATIAL_TARGET = 8u
};

enum rs3_spatial_reason
{
    RS3_SPATIAL_UNMAPPED = 100,
    RS3_SPATIAL_OFF_SCREEN,
    RS3_SPATIAL_BEHIND_CAMERA,
    RS3_SPATIAL_WRONG_PLANE,
    RS3_SPATIAL_WINDOW_UNKNOWN,
    RS3_SPATIAL_NONFINITE
};

enum rs3_camera_flags
{
    RS3_CAMERA_MATRIX = 1u,
    RS3_CAMERA_VIEWPORT = 2u,
    RS3_CAMERA_PHYSICAL_ORIGIN = 4u,
    RS3_CAMERA_COMPLETE = 7u
};

/** Consistently read pose, not a claim of completed live calibration. */
typedef struct rs3_actor_spatial
{
    uint64_t instance_id;
    uint64_t observed_ns;
    uint32_t runtime_id;
    uint32_t valid_mask;
    rs3_position tile;
    uint32_t footprint_size;
    uint32_t tile_reason;
    float fine_x;
    float fine_height;
    float fine_z;
    float facing_degrees; /**< Client-facing yaw; zero points south, increasing towards west. */
    uint32_t facing_reason;
    uint32_t target_reason;
    int32_t target_runtime_id;
    uint32_t target_kind; /**< Reserved until mapped; never inferred from facingTarget. */
    uint32_t reserved[2];
} rs3_actor_spatial;

/** Column-major world-to-clip matrix, including the view transform. Units are fine units. */
typedef struct rs3_camera
{
    uint64_t observed_ns;
    uint64_t scene_epoch;
    uint32_t pid;
    uint32_t valid_mask;
    int32_t plane;
    uint32_t reason;
    float world_to_clip[16];
    int32_t viewport_x;
    int32_t viewport_y;
    int32_t viewport_width;
    int32_t viewport_height;
    int32_t client_screen_x; /**< Physical pixel origin, obtained with per-monitor DPI awareness. */
    int32_t client_screen_y;
    uint64_t process_creation_time;
    uint32_t reserved[2];
} rs3_camera;

typedef struct rs3_spatial_info
{
    uint64_t sample_sequence;
    uint64_t observed_ns;
    uint64_t scene_epoch;
    uint64_t process_creation_time;
    uint32_t pid;
    uint32_t status;
    uint32_t npc_count;
    uint32_t reason;
    rs3_actor_spatial player;
    rs3_camera camera;
} rs3_spatial_info;

typedef struct rs3_screen_point
{
    uint32_t valid;
    uint32_t reason;
    int32_t x;
    int32_t y;
    float clip_w;
    uint32_t reserved;
} rs3_screen_point;

enum rs3_spatial_structure
{
    RS3_SPATIAL_SIZE_ACTOR = 1,
    RS3_SPATIAL_SIZE_CAMERA,
    RS3_SPATIAL_SIZE_INFO,
    RS3_SPATIAL_SIZE_SCREEN_POINT
};

/** Returns the ABI size for an rs3_spatial_structure, or zero. */
RS3_API uint32_t rs3_spatial_sizeof(uint32_t structure);
/** Copies spatial metadata, local-player pose and camera from an owned snapshot. */
RS3_API int32_t rs3_snapshot_spatial(const rs3_snapshot *snapshot, rs3_spatial_info *out,
                                     uint32_t out_size);
/** Copies NPC poses in the same order as rs3_snapshot_npcs; reports required capacity. */
RS3_API int32_t rs3_snapshot_npc_spatial(const rs3_snapshot *snapshot, rs3_actor_spatial *out,
                                         uint32_t capacity, uint32_t *required);
/**
 * Pure projection of a tile centre at an explicitly known terrain height, in fine units.
 * Terrain height is not yet mapped; callers must not substitute an assumed zero.
 * Checks plane, finite inputs, camera validity, positive clip W and viewport bounds.
 * Consumers must additionally check age and process/scene identity before using the pixel.
 */
RS3_API int32_t rs3_project_tile(const rs3_camera *camera, const rs3_position *tile,
                                 float fine_height, rs3_screen_point *out, uint32_t out_size);
/** Pure projection of the actor's observed fine position with a supplied vertical offset. */
RS3_API int32_t rs3_project_actor(const rs3_camera *camera, const rs3_actor_spatial *actor,
                                  float height_offset, rs3_screen_point *out, uint32_t out_size);

#ifdef __cplusplus
}
#endif
#endif
