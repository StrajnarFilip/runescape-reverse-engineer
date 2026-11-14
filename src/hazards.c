/* NPC and scene location candidates. Areas and roles require separate validation. */
#include "hazards.h"

#include <string.h>

#include "decode_utils.h"
#include "memory.h"
#include "position.h"

_Static_assert(sizeof(rs3_hazard) == 88, "ABI hazard entry");
_Static_assert(sizeof(rs3_hazard_info) == 80, "ABI hazard info");

#define MAX_HAZARD_PLANE                3
#define MAX_ACTOR_FOOTPRINT             64u
#define ACTIVE_TELOS_CONFIG_ID          22891
#define BEAM_SCAN_RADIUS                24
#define SCENE_MANAGER_OFFSET            0x199e8u
#define SCENE_LIST_OFFSET               0x58u
#define SCENE_INDEX_OFFSET              0x70u
#define MAP_BASE_OFFSET                 0x14714u
#define MAP_ROW_VECTOR_OFFSET           0x14760u
#define CHUNK_ROW_VECTOR_OFFSET         0xeb90u
#define LOCATION_LAYER_STRIDE           0x50u
#define LOCATION_LAYER_COUNT            5u
#define SECTION_LOCATIONS_OFFSET        0xa8u
#define SECTION_LOCATION_STRIDE         0xc0u
#define SECTION_LOCATION_POINTER_OFFSET 0xa8u
#define SECTION_LOCATION_BATCH          16u
#define VECTOR_STRIDE                   0x18u
#define REFERENCE_STRIDE                0x10u
#define REFERENCE_OBJECT_OFFSET         8u
#define MAX_LOCATION_RECORDS            2048u
#define MAP_SQUARE_TILES                64
#define CHUNK_TILES                     16
#define CHUNKS_PER_SQUARE               (MAP_SQUARE_TILES / CHUNK_TILES)
#define LOCATION_VTABLE_RVA             0x15b52d8u
#define LOCATION_CONFIG_OFFSET          0xd0u
#define LOCATION_GRID_OFFSET            0x170u
#define LOCATION_CONFIG_ID_OFFSET       8u
#define LOCATION_GRID_GETTER_RVA        0xccf1e0u
#define LOCATION_MAP_ITERATOR_RVA       0xc219dau
#define SCENE_HAZARD_KEY_FLAG           (UINT64_C(1) << 63)

/** Identity evidence: original-profile manual captures, 2026-11-11 and 2026-11-13. */
typedef struct captured_npc_identity
{
    int32_t config_id;
    const char *name;
    uint32_t kind;
    uint32_t variant;
} captured_npc_identity;

static const captured_npc_identity captured_identities[] = {
    {22893, "Pure Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_PURE},
    {22894, "Volcanic Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_VOLCANIC},
    {22895, "Corrupt Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_CORRUPT},
    {22896, "Pure Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_PURE},
    {22897, "Volcanic Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_VOLCANIC},
    {22899, "Pure Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_PURE},
    {22900, "Volcanic Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_VOLCANIC},
    {22902, "Pure Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_PURE},
    {22905, "Pure anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_PURE},
    {22905, "*Pure anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_PURE},
    {22906, "Corrupt anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_CORRUPT},
    {22906, "*Corrupt anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_CORRUPT},
    {22907, "Volcanic anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_VOLCANIC},
    {22907, "*Volcanic anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_VOLCANIC},
    {22918, "Anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_VARIANT_UNKNOWN},
};

/** Match a bounded name; nonterminated client strings cannot escape their buffer. */
static int name_equals(const rs3_npc *npc, const char *name)
{
    size_t length = strlen(name);
    return (npc->flags & RS3_NPC_NAME) && npc->name_length == length && length < sizeof npc->name &&
           memcmp(npc->name, name, length) == 0 && npc->name[length] == '\0';
}

/** Unknown config variants with a captured name remain unclassified candidates. */
static int find_candidate(const rs3_npc *npc, const captured_npc_identity **identity)
{
    int candidate = 0;
    *identity = NULL;
    for (size_t index = 0; index < sizeof captured_identities / sizeof captured_identities[0];
         index++)
    {
        const captured_npc_identity *known = &captured_identities[index];
        int same_id = (npc->flags & RS3_NPC_CONFIG) && npc->config_id == known->config_id;
        int same_name = name_equals(npc, known->name);
        candidate |= same_id || same_name;
        if (same_id && same_name)
        {
            *identity = known;
        }
    }
    return candidate;
}

/** Copy only a pose with matching lifetime and same-frame scene provenance. */
static void copy_tile(const rs3_snapshot *snapshot, uint32_t npc_index, rs3_hazard *hazard)
{
    const rs3_spatial_info *spatial = &snapshot->spatial;
    const rs3_actor_spatial *pose = &snapshot->npc_spatial[npc_index];
    const rs3_info *frame = &snapshot->info;
    const rs3_hazard_info *info = &snapshot->hazard_info;
    const uint32_t required_flags =
        RS3_SPATIAL_TILE | RS3_SPATIAL_FINE_POSITION | RS3_SPATIAL_SAME_WORLD;
    hazard->tile_reason = RS3_HAZARD_SPATIAL_UNAVAILABLE;
    if (spatial->status != RS3_AVAILABLE && spatial->status != RS3_PARTIAL)
    {
        return;
    }
    if (npc_index >= spatial->npc_count || spatial->pid != info->pid ||
        spatial->process_creation_time != info->process_creation_time ||
        spatial->sample_sequence != frame->sequence ||
        spatial->scene_epoch != snapshot->combat.scene_epoch ||
        spatial->observed_ns != frame->monotonic_ns || pose->instance_id != hazard->instance_id ||
        pose->runtime_id != hazard->runtime_id || hazard->instance_id == 0)
    {
        hazard->tile_reason = RS3_HAZARD_WRONG_SCENE;
        return;
    }
    if ((pose->valid_mask & required_flags) != required_flags || !pose->tile.valid ||
        pose->tile.x < 0 || pose->tile.x >= MAX_TILE_COORDINATE || pose->tile.y < 0 ||
        pose->tile.y >= MAX_TILE_COORDINATE || pose->tile.plane < 0 ||
        pose->tile.plane > MAX_HAZARD_PLANE || pose->footprint_size == 0 ||
        pose->footprint_size > MAX_ACTOR_FOOTPRINT)
    {
        return;
    }
    hazard->tile = pose->tile;
    hazard->footprint_size = pose->footprint_size;
    hazard->flags |= RS3_HAZARD_TILE | RS3_HAZARD_FOOTPRINT;
    hazard->tile_reason = RS3_REASON_NONE;
}

typedef struct scene_vector
{
    uint64_t begin;
    uint64_t end;
} scene_vector;

typedef struct beam_tiles
{
    rs3_hazard entries[RS3_MAX_HAZARDS];
    uint32_t count;
    uint32_t dropped;
} beam_tiles;

/** Check layout instructions in the executable selected by the profile hash. */
static int beam_layout_verified(rs3_context *context)
{
    static const unsigned char map_iterator[] = {0x48, 0x8b, 0xb1, 0x60, 0x47, 1, 0};
    static const unsigned char grid_getter[] = {0x48, 0x8d, 0x81, 0x70, 1, 0, 0, 0xc3};
    unsigned char bytes[sizeof grid_getter];
    uint64_t module = context->target.module_base;
    return memory_read(context, module + LOCATION_MAP_ITERATOR_RVA, bytes, sizeof map_iterator) &&
           memcmp(bytes, map_iterator, sizeof map_iterator) == 0 &&
           memory_read(context, module + LOCATION_GRID_GETTER_RVA, bytes, sizeof grid_getter) &&
           memcmp(bytes, grid_getter, sizeof grid_getter) == 0;
}

/** Validate the bounded vector before applying client strides. */
static int read_scene_vector(rs3_context *context, uint64_t address, uint32_t stride,
                             uint32_t limit, scene_vector *vector)
{
    return memory_read(context, address, vector, sizeof *vector) && vector->end >= vector->begin &&
           (vector->end - vector->begin) % stride == 0 &&
           (vector->end - vector->begin) / stride <= limit &&
           (vector->begin == vector->end || is_valid_remote_pointer(vector->begin));
}

/** Spawned strip and centre locations observed along the rendered P1 and P2 streams. */
static uint32_t beam_kind(int32_t config, int phase)
{
    if (phase == 1 && (config == 103545 || config == 103546))
    {
        return RS3_HAZARD_GREEN_BEAM;
    }
    if (phase == 2 && (config == 103547 || config == 103548))
    {
        return RS3_HAZARD_BLACK_BEAM;
    }
    return RS3_HAZARD_UNKNOWN;
}

/** Copy a location only when its type, config and coordinates agree on a second read. */
static int read_beam_location(rs3_context *context, uint64_t location, int phase,
                              const rs3_position *center, beam_tiles *tiles)
{
    if (location == 0)
    {
        return 1;
    }
    uint64_t vtable;
    if (!memory_read_pointer(context, location, &vtable))
    {
        return 0;
    }
    if (vtable != context->target.module_base + LOCATION_VTABLE_RVA)
    {
        return 1;
    }
    uint64_t config;
    int32_t id = -1;
    if (!memory_read_pointer(context, location + LOCATION_CONFIG_OFFSET, &config) ||
        (config && !memory_read(context, config + LOCATION_CONFIG_ID_OFFSET, &id, sizeof id)))
    {
        return 0;
    }
    uint32_t kind = config ? beam_kind(id, phase) : RS3_HAZARD_UNKNOWN;
    if (kind == RS3_HAZARD_UNKNOWN)
    {
        return 1;
    }
    int32_t grid[3], grid_after[3], id_after;
    uint64_t config_after, vtable_after;
    if (!memory_read(context, location + LOCATION_GRID_OFFSET, grid, sizeof grid) ||
        !memory_read(context, location + LOCATION_GRID_OFFSET, grid_after, sizeof grid_after) ||
        memcmp(grid, grid_after, sizeof grid) ||
        !memory_read(context, config + LOCATION_CONFIG_ID_OFFSET, &id_after, sizeof id_after) ||
        id != id_after ||
        !memory_read_pointer(context, location + LOCATION_CONFIG_OFFSET, &config_after) ||
        config != config_after || !memory_read_pointer(context, location, &vtable_after) ||
        vtable != vtable_after)
    {
        return 0;
    }
    int32_t plane = grid[0], x = grid[1], y = grid[2];
    if (plane != center->plane || x < 0 || x >= MAX_TILE_COORDINATE || y < 0 ||
        y >= MAX_TILE_COORDINATE || x < center->x - BEAM_SCAN_RADIUS ||
        x > center->x + BEAM_SCAN_RADIUS || y < center->y - BEAM_SCAN_RADIUS ||
        y > center->y + BEAM_SCAN_RADIUS)
    {
        return 1;
    }
    uint64_t key = SCENE_HAZARD_KEY_FLAG |
                   ((((uint64_t)id * 4 + (uint32_t)plane) * MAX_TILE_COORDINATE + (uint32_t)x) *
                        MAX_TILE_COORDINATE +
                    (uint32_t)y);
    for (uint32_t index = 0; index < tiles->count; index++)
    {
        if (tiles->entries[index].instance_id == key)
        {
            return 1;
        }
    }
    if (tiles->count == RS3_MAX_HAZARDS)
    {
        tiles->dropped++;
        return 1;
    }
    tiles->entries[tiles->count++] = (rs3_hazard){
        .instance_id = key,
        .config_id = id,
        .kind = kind,
        .flags = RS3_HAZARD_IDENTITY | RS3_HAZARD_TILE | RS3_HAZARD_SCENE_LOCATION,
        .tile = {1, x, y, plane},
        .reason = RS3_HAZARD_NEEDS_CALIBRATION,
        .timing_reason = RS3_HAZARD_UNMAPPED,
        .role_reason = RS3_HAZARD_UNMAPPED,
    };
    return 1;
}

static int scan_locations(rs3_context *context, uint64_t address, uint32_t stride,
                          uint32_t object_offset, int phase, const rs3_position *center,
                          beam_tiles *tiles)
{
    scene_vector locations, after;
    if (!read_scene_vector(context, address, stride, MAX_LOCATION_RECORDS, &locations))
    {
        return 0;
    }
    unsigned char records[SECTION_LOCATION_BATCH * SECTION_LOCATION_STRIDE];
    for (uint64_t entry = locations.begin; entry != locations.end;)
    {
        size_t length = (size_t)(locations.end - entry);
        if (length > (size_t)SECTION_LOCATION_BATCH * stride)
        {
            length = (size_t)SECTION_LOCATION_BATCH * stride;
        }
        if (!memory_read(context, entry, records, length))
        {
            return 0;
        }
        for (size_t offset = object_offset; offset < length; offset += stride)
        {
            uint64_t location = decode_u64(records + offset);
            if (!read_beam_location(context, location, phase, center, tiles))
            {
                return 0;
            }
        }
        entry += length;
    }
    return memory_read(context, address, &after, sizeof after) &&
           memcmp(&locations, &after, sizeof after) == 0;
}

/** Inspect direct locations and the section-backed lists traversed by LocList. */
static int scan_chunk(rs3_context *context, uint64_t chunk, int phase, const rs3_position *center,
                      beam_tiles *tiles)
{
    const uint32_t direct_lists[] = {0xc0u, 0x308u, 0x320u};
    for (unsigned index = 0; index < sizeof direct_lists / sizeof *direct_lists; index++)
    {
        if (!scan_locations(context, chunk + direct_lists[index], REFERENCE_STRIDE,
                            REFERENCE_OBJECT_OFFSET, phase, center, tiles))
        {
            return 0;
        }
    }
    const uint32_t section_lists[] = {0x100u, 0x360u};
    for (unsigned index = 0; index < sizeof section_lists / sizeof *section_lists; index++)
    {
        for (uint32_t layer = 0; layer < LOCATION_LAYER_COUNT; layer++)
        {
            uint64_t address = chunk + section_lists[index] + layer * LOCATION_LAYER_STRIDE;
            scene_vector sections, after;
            if (!read_scene_vector(context, address, REFERENCE_STRIDE, 128, &sections))
            {
                return 0;
            }
            for (uint64_t entry = sections.begin; entry != sections.end; entry += REFERENCE_STRIDE)
            {
                uint64_t section;
                if (!memory_read_pointer(context, entry + REFERENCE_OBJECT_OFFSET, &section) ||
                    (section &&
                     !scan_locations(context, section + SECTION_LOCATIONS_OFFSET,
                                     SECTION_LOCATION_STRIDE, SECTION_LOCATION_POINTER_OFFSET,
                                     phase, center, tiles)))
                {
                    return 0;
                }
            }
            if (!memory_read(context, address, &after, sizeof after) ||
                memcmp(&sections, &after, sizeof after))
            {
                return 0;
            }
        }
    }
    return 1;
}

/** Read one nested map vector, verifying the index before following its reference. */
static int map_reference(rs3_context *context, uint64_t vectors, uint32_t stride, int index,
                         uint32_t limit, int object, uint64_t *out)
{
    scene_vector vector;
    *out = 0;
    if (!read_scene_vector(context, vectors, stride, limit, &vector))
    {
        return 0;
    }
    if (index < 0 || (uint64_t)index >= (vector.end - vector.begin) / stride)
    {
        return 1;
    }
    *out = vector.begin + (uint64_t)index * stride;
    return !object || memory_read_pointer(context, *out + REFERENCE_OBJECT_OFFSET, out);
}

static int read_scene_root(rs3_context *context, uint64_t world_state, uint64_t *root)
{
    uint64_t manager, scenes;
    int32_t index;
    return memory_read_pointer(context, world_state + SCENE_MANAGER_OFFSET, &manager) && manager &&
           memory_read(context, manager + SCENE_INDEX_OFFSET, &index, sizeof index) && index >= 0 &&
           index < 32 && memory_read_pointer(context, manager + SCENE_LIST_OFFSET, &scenes) &&
           scenes &&
           memory_read_pointer(
               context, scenes + (uint64_t)index * REFERENCE_STRIDE + REFERENCE_OBJECT_OFFSET,
               root) &&
           *root;
}

static int read_beam_tiles(rs3_context *context, uint64_t world_state, int phase,
                           const rs3_position *center, beam_tiles *tiles)
{
    uint64_t root, root_after;
    int32_t map_base[2];
    if (!read_scene_root(context, world_state, &root) ||
        !memory_read(context, root + MAP_BASE_OFFSET, map_base, sizeof map_base) ||
        map_base[0] < 0 || map_base[1] < 0 || map_base[0] >= MAX_TILE_COORDINATE ||
        map_base[1] >= MAX_TILE_COORDINATE)
    {
        return 0;
    }
    for (int x = (center->x - BEAM_SCAN_RADIUS) / CHUNK_TILES;
         x <= (center->x + BEAM_SCAN_RADIUS) / CHUNK_TILES; x++)
    {
        uint64_t row;
        if (!map_reference(context, root + MAP_ROW_VECTOR_OFFSET, VECTOR_STRIDE,
                           x / CHUNKS_PER_SQUARE - map_base[0], 64, 0, &row))
        {
            return 0;
        }
        for (int y = (center->y - BEAM_SCAN_RADIUS) / CHUNK_TILES;
             row && y <= (center->y + BEAM_SCAN_RADIUS) / CHUNK_TILES; y++)
        {
            uint64_t square, chunk;
            if (!map_reference(context, row, VECTOR_STRIDE, y / CHUNKS_PER_SQUARE - map_base[1], 64,
                               1, &square))
            {
                return 0;
            }
            if (!square)
            {
                continue;
            }
            uint64_t chunk_row;
            if (!map_reference(context, square + CHUNK_ROW_VECTOR_OFFSET, VECTOR_STRIDE,
                               x % CHUNKS_PER_SQUARE, CHUNKS_PER_SQUARE, 0, &chunk_row))
            {
                return 0;
            }
            if (!chunk_row)
            {
                continue;
            }
            if (!map_reference(context, chunk_row, REFERENCE_STRIDE, y % CHUNKS_PER_SQUARE,
                               CHUNKS_PER_SQUARE, 1, &chunk) ||
                (chunk && !scan_chunk(context, chunk, phase, center, tiles)))
            {
                return 0;
            }
        }
    }
    int32_t map_after[2];
    return read_scene_root(context, world_state, &root_after) && root_after == root &&
           memory_read(context, root + MAP_BASE_OFFSET, map_after, sizeof map_after) &&
           memcmp(map_base, map_after, sizeof map_base) == 0;
}

void hazards_read_telos_beams(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state)
{
    if (context->target.profile != &profile_compatibility || !snapshot->encounter.stage_raw.valid ||
        snapshot->encounter.stage_raw.value < 0 || snapshot->encounter.stage_raw.value >= 2)
    {
        return;
    }
    const rs3_position *center = NULL;
    for (uint32_t index = 0; index < snapshot->info.npc_count; index++)
    {
        const rs3_npc *npc = &snapshot->npcs[index];
        if (!(npc->flags & RS3_NPC_CONFIG) || npc->config_id != ACTIVE_TELOS_CONFIG_ID)
        {
            continue;
        }
        const rs3_actor_spatial *actor = &snapshot->npc_spatial[index];
        if (center || !(actor->valid_mask & RS3_SPATIAL_SAME_WORLD) || !actor->tile.valid)
        {
            return;
        }
        center = &actor->tile;
    }
    if (!center || !beam_layout_verified(context))
    {
        return;
    }
    int phase = (int)snapshot->encounter.stage_raw.value + 1;
    beam_tiles tiles = {0};
    rs3_hazard_info *info = &snapshot->hazard_info;
    uint64_t scene_after;
    if (!read_beam_tiles(context, world_state, phase, center, &tiles) ||
        !position_player_in_scene(context, &scene_after) || scene_after != world_state)
    {
        info->reason = RS3_READ_FAILED;
        return;
    }
    if (tiles.count)
    {
        info->observed_channels |=
            phase == 1 ? RS3_HAZARD_CHANNEL_GREEN_BEAM : RS3_HAZARD_CHANNEL_BLACK_BEAM;
    }
    info->candidate_count += tiles.count + tiles.dropped;
    info->dropped_count += tiles.dropped;
    for (uint32_t index = 0; index < tiles.count; index++)
    {
        if (info->count == RS3_MAX_HAZARDS)
        {
            info->dropped_count += tiles.count - index;
            break;
        }
        tiles.entries[index].observed_ns = info->observed_ns;
        snapshot->hazards[info->count++] = tiles.entries[index];
    }
    info->status = RS3_PARTIAL;
    info->reason = info->dropped_count ? RS3_HAZARD_OVERFLOW : RS3_HAZARD_NEEDS_CALIBRATION;
}

void hazards_commit(rs3_context *context, rs3_snapshot *snapshot, int npcs_decoded)
{
    rs3_hazard_info *info = &snapshot->hazard_info;
    memset(info, 0, sizeof *info);
    /* Erase storage as well as the count: failure/scene changes retain no old entries. */
    memset(snapshot->hazards, 0, sizeof snapshot->hazards);
    info->observed_ns = snapshot->info.monotonic_ns;
    info->sample_sequence = snapshot->info.sequence;
    info->scene_epoch = snapshot->combat.scene_epoch;
    info->process_creation_time = context->target.process_creation_time;
    info->pid = context->target.pid;
    info->status = RS3_UNAVAILABLE;
    info->reason = RS3_HAZARD_UNMAPPED;
    info->unmapped_channels = RS3_HAZARD_CHANNEL_ALL;
    if ((snapshot->info.status != RS3_AVAILABLE && snapshot->info.status != RS3_PARTIAL) ||
        !npcs_decoded || snapshot->info.npc_count > RS3_MAX_NPCS)
    {
        info->reason = RS3_READ_FAILED;
        return;
    }

    for (uint32_t npc_index = 0; npc_index < snapshot->info.npc_count; npc_index++)
    {
        const rs3_npc *npc = &snapshot->npcs[npc_index];
        const captured_npc_identity *identity;
        if (!find_candidate(npc, &identity))
        {
            continue;
        }
        info->candidate_count++;
        if (info->count == RS3_MAX_HAZARDS)
        {
            info->dropped_count++;
            continue;
        }
        rs3_hazard *hazard = &snapshot->hazards[info->count++];
        hazard->observed_ns = info->observed_ns;
        hazard->instance_id = npc->instance_id;
        hazard->runtime_id = npc->runtime_id;
        hazard->config_id = (npc->flags & RS3_NPC_CONFIG) ? npc->config_id : -1;
        hazard->reason = RS3_HAZARD_IDENTITY_UNMAPPED;
        hazard->timing_reason = RS3_HAZARD_UNMAPPED;
        hazard->role_reason = RS3_HAZARD_UNMAPPED;
        /* Captured identities are scoped to their executable, not extrapolated to others. */
        if (context->target.profile == &profile_primary && identity != NULL &&
            npc->instance_id != 0)
        {
            hazard->kind = identity->kind;
            hazard->variant = identity->variant;
            hazard->flags |= RS3_HAZARD_IDENTITY;
            hazard->reason = RS3_HAZARD_NEEDS_CALIBRATION;
            info->observed_channels |= identity->kind == RS3_HAZARD_FONT
                                           ? RS3_HAZARD_CHANNEL_FONTS
                                           : RS3_HAZARD_CHANNEL_GOLEMS;
        }
        copy_tile(snapshot, npc_index, hazard);
        if (!(hazard->flags & RS3_HAZARD_TILE))
        {
            info->unlocated_count++;
        }
    }
    if (info->count != 0)
    {
        info->status = RS3_PARTIAL;
        info->reason = info->dropped_count ? RS3_HAZARD_OVERFLOW : RS3_HAZARD_NEEDS_CALIBRATION;
    }
}

uint32_t rs3_hazards_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_HAZARD_SIZE_INFO:
            return sizeof(rs3_hazard_info);
        case RS3_HAZARD_SIZE_ENTRY:
            return sizeof(rs3_hazard);
        default:
            return 0;
    }
}
