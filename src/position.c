/* Local player actor and tile; the tile layout is pinned by the Compatibility Mode profile. */
#include "position.h"

#include "decode_utils.h"
#include "memory.h"

/** Fine coordinates use 512 units per tile. */
#define FINE_UNITS_PER_TILE 512.0
#define MAX_PLANE           3
/** Read attempts before giving up on two agreeing observations. */
#define POSITION_READ_ATTEMPTS 4u
/** The actor stores the player index as a 16-bit runtime ID. */
#define MAX_PLAYER_INDEX 65535
#define MAX_PLAYER_SLOTS 65536u

/*
 * The Compatibility Mode tile layout repeats the local actor path that both
 * executables share. Keep the two profiles in agreement so that the tile and
 * the hit marks always come from the same actor.
 */
_Static_assert(POSITION_CLIENT_STATE_OFFSET == LAYOUT_GAME_STATE_OFFSET, "Scene state field");
_Static_assert(POSITION_IN_GAME_STATE == LAYOUT_IN_GAME_STATE, "In-game scene state");
_Static_assert(POSITION_PLAYER_CONTEXT_OFFSET == LAYOUT_LOCAL_ACTOR_CONTEXT_OFFSET,
               "Player context field");
_Static_assert(POSITION_PLAYER_INDEX_OFFSET == LAYOUT_LOCAL_ACTOR_INDEX_OFFSET,
               "Player index field");
_Static_assert(POSITION_PLAYER_OVERRIDE_OFFSET == LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET,
               "Player override field");
_Static_assert(POSITION_PLAYER_TABLE_OFFSET == LAYOUT_LOCAL_ACTOR_TABLE_OFFSET,
               "Player table field");
_Static_assert(POSITION_TABLE_RANGE_OFFSET == LAYOUT_LOCAL_ACTOR_RANGE_OFFSET,
               "Player table slot range");
_Static_assert(POSITION_SLOT_ENTITY_OFFSET == LAYOUT_LOCAL_ACTOR_SLOT_ENTITY_OFFSET,
               "Player slot entity field");

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

/** Looks up the actor of a player index in the world state's player table. */
static int find_table_actor(rs3_context *context, uint64_t world_state, uint32_t player_index,
                            uint64_t *actor_address)
{
    uint64_t player_table;
    uint64_t slot_range[2]; /* [begin, end) of the slot pointer array. */
    uint64_t player_slot;
    if (!memory_read_pointer(context, world_state + LAYOUT_LOCAL_ACTOR_TABLE_OFFSET,
                             &player_table) ||
        player_table == 0 ||
        !memory_read(context, player_table + LAYOUT_LOCAL_ACTOR_RANGE_OFFSET, slot_range,
                     sizeof slot_range) ||
        !is_valid_remote_pointer(slot_range[0]) || slot_range[1] < slot_range[0])
    {
        return 0;
    }
    uint64_t range_bytes = slot_range[1] - slot_range[0];
    uint64_t slot_count = range_bytes / sizeof player_slot;
    return range_bytes % sizeof player_slot == 0 && slot_count <= MAX_PLAYER_SLOTS &&
           player_index < slot_count &&
           memory_read_pointer(context, slot_range[0] + (uint64_t)player_index * sizeof player_slot,
                               &player_slot) &&
           player_slot != 0 &&
           memory_read_pointer(context, player_slot + LAYOUT_LOCAL_ACTOR_SLOT_ENTITY_OFFSET,
                               actor_address) &&
           *actor_address != 0;
}

/**
 * Returns nonzero when the actor carries the profile's player actor vtable and
 * stores player_index as its runtime ID. A profile without a pinned vtable
 * cannot check the identity; only its tile uses this path.
 */
static int is_local_player_actor(rs3_context *context, uint64_t actor_address,
                                 uint32_t player_index)
{
    uint32_t vtable_rva = context->target.profile->player_actor_vtable_rva;
    if (vtable_rva == 0)
    {
        return 1;
    }
    uint64_t actor_vtable;
    uint16_t actor_index;
    return memory_read_pointer(context, actor_address, &actor_vtable) &&
           actor_vtable == context->target.module_base + vtable_rva &&
           memory_read(context, actor_address + LAYOUT_LOCAL_ACTOR_RUNTIME_ID_OFFSET, &actor_index,
                       sizeof actor_index) &&
           actor_index == player_index;
}

int position_find_local_actor(rs3_context *context, uint64_t world_state, uint64_t *actor_address,
                              uint32_t *player_index)
{
    uint64_t player_context;
    uint64_t context_owner;
    int32_t index;
    if (!memory_read_pointer(context, world_state + LAYOUT_LOCAL_ACTOR_CONTEXT_OFFSET,
                             &player_context) ||
        player_context == 0 || !memory_read_pointer(context, player_context, &context_owner) ||
        context_owner != world_state ||
        !memory_read(context, player_context + LAYOUT_LOCAL_ACTOR_INDEX_OFFSET, &index,
                     sizeof index) ||
        index < 0 || index > MAX_PLAYER_INDEX ||
        !memory_read_pointer(context, player_context + LAYOUT_LOCAL_ACTOR_OVERRIDE_OFFSET,
                             actor_address))
    {
        return 0;
    }
    /* Use the context's actor override when set; otherwise look up the player index. */
    if (*actor_address == 0 &&
        !find_table_actor(context, world_state, (uint32_t)index, actor_address))
    {
        return 0;
    }
    if (!is_local_player_actor(context, *actor_address, (uint32_t)index))
    {
        return 0;
    }
    *player_index = (uint32_t)index;
    return 1;
}

/** Reads one tile observation. Returns the actor address, or 0 when unavailable. */
static uint64_t read_tile(rs3_context *context, rs3_position *tile)
{
    uint64_t scene_client;
    uint64_t player_actor;
    uint32_t player_index;
    int32_t plane;
    int32_t footprint_size;
    float fine_coordinates[3]; /* x (east), height, z (north). */
    if (!position_player_in_scene(context, &scene_client) ||
        !position_find_local_actor(context, scene_client, &player_actor, &player_index) ||
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
