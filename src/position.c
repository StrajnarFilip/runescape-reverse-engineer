/* Read the local player tile from the pinned position profile. */
#include "internal.h"

#include "profile_generated.h"

static uint64_t local_player(rs3_context *c) {
    uint64_t client, context, owner, player, table, range[2], slot;
    int32_t state, index;
    if (!ptr(c, c->module_base + POS_CLIENT_PTR_RVA, &client) || !client ||
        !rd(c, client + POS_STATE, &state, sizeof state) || state != (int32_t)POS_IN_GAME_STATE ||
        !ptr(c, client + POS_PLAYER_CONTEXT, &context) || !context ||
        !ptr(c, context, &owner) || owner != client ||
        !ptr(c, context + POS_PLAYER_OVERRIDE, &player)) return 0;
    if (player) return player;

    if (!rd(c, context + POS_PLAYER_INDEX, &index, sizeof index) || index < 0 ||
        !ptr(c, client + POS_PLAYER_TABLE, &table) || !table ||
        !rd(c, table + POS_TABLE_RANGE, range, sizeof range) ||
        !pointer_valid(range[0]) || range[1] < range[0] ||
        (uint64_t)index >= (range[1] - range[0]) / sizeof slot ||
        !ptr(c, range[0] + (uint64_t)index * sizeof slot, &slot) || !slot ||
        !ptr(c, slot + POS_SLOT_ENTITY, &player)) return 0;
    return player;
}

static uint64_t read_tile(rs3_context *c, rs3_position *tile) {
    uint64_t player = local_player(c);
    int32_t plane, size;
    float fine[3];
    if (!player || !rd(c, player + POS_PLANE, &plane, sizeof plane) ||
        !rd(c, player + POS_SIZE, &size, sizeof size) ||
        !rd(c, player + POS_FINE_POSITION, fine, sizeof fine) ||
        plane < 0 || plane > 3 || size < 1) return 0;

    /* The tile describes the origin of the actor footprint. Fine Y is height. */
    double x = fine[0] / 512.0 - size / 2;
    double y = fine[2] / 512.0 - size / 2;
    if (!(x >= 0 && x < 12800 && y >= 0 && y < 12800)) return 0;
    *tile = (rs3_position){1, (int32_t)x, (int32_t)y, plane};
    return player;
}

int decode_position(rs3_context *c, rs3_snapshot *s) {
    uint64_t previous_player = 0;
    rs3_position previous = {0};
    for (unsigned attempt = 0; attempt < 4; attempt++) {
        rs3_position tile = {0};
        uint64_t player = read_tile(c, &tile);
        if (player && player == previous_player &&
            tile.x == previous.x && tile.y == previous.y && tile.plane == previous.plane) {
            s->position = tile;
            s->info.status = RS3_PARTIAL;
            s->player.gcd_reason = RS3_NOT_SET;
            s->inventory_info.status = RS3_UNAVAILABLE;
            s->inventory_info.reason = RS3_NOT_SET;
            return 1;
        }
        previous_player = player;
        previous = tile;
    }
    return fail(c, RS3_NOT_SET, "Player tile unavailable");
}
