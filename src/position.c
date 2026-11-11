/* Local player tile of the Compatibility Mode client. */
#include "position.h"

#include "decode_utils.h"
#include "memory.h"

/** Fine coordinates use 512 units per tile. */
#define FINE_UNITS_PER_TILE 512.0
#define MAX_PLANE           3
/** Read attempts before giving up on two agreeing observations. */
#define POSITION_READ_ATTEMPTS 4u

int position_player_in_scene(rs3_context *context, uint64_t *scene_client)
{
    int32_t client_state;
    return memory_read_pointer(context, context->target.module_base + POSITION_CLIENT_POINTER_RVA,
                               scene_client) &&
           *scene_client != 0 &&
           memory_read(context, *scene_client + POSITION_CLIENT_STATE_OFFSET, &client_state,
                       sizeof client_state) &&
           client_state == (int32_t)POSITION_IN_GAME_STATE;
}

/** Returns the local player actor address, or 0. */
static uint64_t find_local_player(rs3_context *context)
{
    uint64_t scene_client;
    uint64_t player_context;
    uint64_t player_context_owner;
    uint64_t player_actor;
    if (!position_player_in_scene(context, &scene_client) ||
        !memory_read_pointer(context, scene_client + POSITION_PLAYER_CONTEXT_OFFSET,
                             &player_context) ||
        player_context == 0 ||
        !memory_read_pointer(context, player_context, &player_context_owner) ||
        player_context_owner != scene_client ||
        !memory_read_pointer(context, player_context + POSITION_PLAYER_OVERRIDE_OFFSET,
                             &player_actor))
    {
        return 0;
    }
    /* Use the context's player override when set; otherwise look up the player index. */
    if (player_actor != 0)
    {
        return player_actor;
    }

    int32_t player_index;
    uint64_t player_table;
    uint64_t slot_range[2]; /* [begin, end) of the slot pointer array. */
    uint64_t player_slot;
    if (!memory_read(context, player_context + POSITION_PLAYER_INDEX_OFFSET, &player_index,
                     sizeof player_index) ||
        player_index < 0 ||
        !memory_read_pointer(context, scene_client + POSITION_PLAYER_TABLE_OFFSET, &player_table) ||
        player_table == 0 ||
        !memory_read(context, player_table + POSITION_TABLE_RANGE_OFFSET, slot_range,
                     sizeof slot_range) ||
        !is_valid_remote_pointer(slot_range[0]) || slot_range[1] < slot_range[0])
    {
        return 0;
    }
    uint64_t slot_count = (slot_range[1] - slot_range[0]) / sizeof player_slot;
    if ((uint64_t)player_index >= slot_count ||
        !memory_read_pointer(context, slot_range[0] + (uint64_t)player_index * sizeof player_slot,
                             &player_slot) ||
        player_slot == 0 ||
        !memory_read_pointer(context, player_slot + POSITION_SLOT_ENTITY_OFFSET, &player_actor))
    {
        return 0;
    }
    return player_actor;
}

/** Reads one tile observation. Returns the actor address, or 0 when unavailable. */
static uint64_t read_tile(rs3_context *context, rs3_position *tile)
{
    uint64_t player_actor = find_local_player(context);
    int32_t plane;
    int32_t footprint_size;
    float fine_coordinates[3]; /* x (east), height, z (north). */
    if (player_actor == 0 ||
        !memory_read(context, player_actor + POSITION_PLANE_OFFSET, &plane, sizeof plane) ||
        !memory_read(context, player_actor + POSITION_SIZE_OFFSET, &footprint_size,
                     sizeof footprint_size) ||
        !memory_read(context, player_actor + POSITION_FINE_COORDINATES_OFFSET, fine_coordinates,
                     sizeof fine_coordinates) ||
        plane < 0 || plane > MAX_PLANE || footprint_size < 1)
    {
        return 0;
    }

    /* The tile describes the origin of the actor footprint. */
    double tile_x = fine_coordinates[0] / FINE_UNITS_PER_TILE - footprint_size / 2;
    double tile_y = fine_coordinates[2] / FINE_UNITS_PER_TILE - footprint_size / 2;
    if (!(tile_x >= 0 && tile_x < MAX_TILE_COORDINATE && tile_y >= 0 &&
          tile_y < MAX_TILE_COORDINATE))
    {
        return 0;
    }
    *tile = (rs3_position){1, (int32_t)tile_x, (int32_t)tile_y, plane};
    return player_actor;
}

int position_decode(rs3_context *context, rs3_position *position)
{
    uint64_t previous_actor = 0;
    rs3_position previous_tile = {0};
    for (unsigned attempt = 0; attempt < POSITION_READ_ATTEMPTS; attempt++)
    {
        rs3_position tile = {0};
        uint64_t player_actor = read_tile(context, &tile);
        if (player_actor != 0 && player_actor == previous_actor && tile.x == previous_tile.x &&
            tile.y == previous_tile.y && tile.plane == previous_tile.plane)
        {
            *position = tile;
            return 1;
        }
        previous_actor = player_actor;
        previous_tile = tile;
    }
    return 0;
}
