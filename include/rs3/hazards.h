/**
 * @file hazards.h
 * @brief Additive, caller-owned arena observations from one native snapshot.
 *
 * These records distinguish an observed NPC identity from a calibrated mechanic.
 * A font's actor footprint is not its interaction or damage area. Unknown timing,
 * roles and coverage must never be interpreted as safe tiles or absent hazards.
 * No entry currently has RS3_HAZARD_MECHANICS_VALIDATED set.
 */
#ifndef RS3_HAZARDS_H
#define RS3_HAZARDS_H

#include "rs3.h"

#ifdef __cplusplus
extern "C"
{
#endif

/** Bounded array capacity. Overflow is explicit in rs3_hazard_info.dropped_count. */
#define RS3_MAX_HAZARDS 128u

enum rs3_hazard_kind
{
    RS3_HAZARD_UNKNOWN = 0,
    RS3_HAZARD_GREEN_BEAM,
    RS3_HAZARD_BLACK_BEAM,
    RS3_HAZARD_RED_BEAM,
    RS3_HAZARD_FONT,
    RS3_HAZARD_FALLING_ROCK,
    RS3_HAZARD_TENDRILS,
    RS3_HAZARD_ANIMA_BOMB,
    RS3_HAZARD_PROJECTILE,
    RS3_HAZARD_GOLEM
};

/** Identity variants from captured NPC names; these do not assign a combat role. */
enum rs3_hazard_variant
{
    RS3_HAZARD_VARIANT_UNKNOWN = 0,
    RS3_HAZARD_PURE,
    RS3_HAZARD_CORRUPT,
    RS3_HAZARD_VOLCANIC
};

/** One bit per hazard family, independently of enum rs3_hazard_kind. */
enum rs3_hazard_channel
{
    RS3_HAZARD_CHANNEL_GREEN_BEAM = 1u,
    RS3_HAZARD_CHANNEL_BLACK_BEAM = 2u,
    RS3_HAZARD_CHANNEL_RED_BEAM = 4u,
    RS3_HAZARD_CHANNEL_FONTS = 8u,
    RS3_HAZARD_CHANNEL_ROCKS = 16u,
    RS3_HAZARD_CHANNEL_TENDRILS = 32u,
    RS3_HAZARD_CHANNEL_BOMBS = 64u,
    RS3_HAZARD_CHANNEL_PROJECTILES = 128u,
    RS3_HAZARD_CHANNEL_GOLEMS = 256u,
    RS3_HAZARD_CHANNEL_ALL = 511u
};

enum rs3_hazard_flags
{
    RS3_HAZARD_IDENTITY = 1u, /**< Captured identity for this candidate's source. */
    RS3_HAZARD_TILE = 2u,     /**< P1 supplied a consistent tile in the player's world. */
    RS3_HAZARD_FOOTPRINT = 4u,
    RS3_HAZARD_CLIENT_START = 8u,
    RS3_HAZARD_CLIENT_END = 16u,
    RS3_HAZARD_MECHANICS_VALIDATED = 32u,
    RS3_HAZARD_SCENE_LOCATION = 64u /**< Observed location tile with a classified config. */
};

enum rs3_hazard_reason
{
    RS3_HAZARD_UNMAPPED = 400u,
    RS3_HAZARD_IDENTITY_UNMAPPED,
    RS3_HAZARD_SPATIAL_UNAVAILABLE,
    RS3_HAZARD_WRONG_SCENE,
    RS3_HAZARD_OVERFLOW,
    RS3_HAZARD_NEEDS_CALIBRATION
};

/**
 * One present candidate, not a spawn event. For scene locations, instance_id
 * identifies the config and tile, and runtime_id is zero. For actors,
 * instance_id identifies the NPC lifetime. Qualify both with process and scene.
 * Invalid numeric fields are zero; their flags/reasons establish availability.
 * Client tick fields, when mapped, will use the client's clock, not server ticks.
 */
typedef struct rs3_hazard
{
    uint64_t instance_id;
    uint64_t observed_ns; /**< Native frame observation time, not the spawn time. */
    uint32_t runtime_id;
    int32_t config_id;
    uint32_t kind;
    uint32_t variant;
    uint32_t flags;
    uint32_t reason;
    rs3_position tile;
    uint32_t footprint_size; /**< NPC footprint only; no inferred hazard area. */
    uint32_t tile_reason;
    uint32_t start_client_tick;
    uint32_t end_client_tick;
    uint32_t timing_reason;
    uint32_t role_reason;
    uint32_t role; /**< Reserved: font/meter effects remain unmapped. */
    uint32_t reserved;
} rs3_hazard;

/**
 * Coverage of one frame. complete_channels is currently always zero. Neither
 * count == 0 nor status == RS3_PARTIAL proves an empty or safe arena.
 * candidate_count includes dropped candidates; count is the stored prefix.
 * Unlocated entries are retained with invalid tiles. No event queue is used.
 */
typedef struct rs3_hazard_info
{
    uint64_t observed_ns;
    uint64_t sample_sequence;
    uint64_t scene_epoch;
    uint64_t process_creation_time;
    uint32_t pid;
    uint32_t status;
    uint32_t reason;
    uint32_t count;
    uint32_t observed_channels;
    uint32_t complete_channels;
    uint32_t unmapped_channels;
    uint32_t candidate_count;
    uint32_t dropped_count;
    uint32_t unlocated_count;
    uint32_t reserved[2];
} rs3_hazard_info;

enum rs3_hazard_structure
{
    RS3_HAZARD_SIZE_INFO = 1u,
    RS3_HAZARD_SIZE_ENTRY
};

/** Return a structure size in bytes, or zero for an unknown structure ID. */
RS3_API uint32_t rs3_hazards_sizeof(uint32_t structure);

/** Copy info to caller storage. An undersized buffer returns RS3_E_ABI. */
RS3_API int32_t rs3_snapshot_hazard_info(const rs3_snapshot *snapshot, rs3_hazard_info *out,
                                         uint32_t out_size);

/**
 * Copy entries in snapshot NPC order, followed by scene locations.
 * Always set required to the stored count.
 * Insufficient capacity returns RS3_E_BUFFER without copying a partial prefix.
 * out may be NULL for a zero-entry snapshot; required must never be NULL.
 */
RS3_API int32_t rs3_snapshot_hazards(const rs3_snapshot *snapshot, rs3_hazard *out,
                                     uint32_t capacity, uint32_t *required);

#ifdef __cplusplus
}
#endif

#endif /* RS3_HAZARDS_H */
