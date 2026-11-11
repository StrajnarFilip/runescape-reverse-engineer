/* Public snapshot allocation and accessors. Every accessor copies out of the snapshot. */
#include <stdlib.h>
#include <string.h>

#include "context.h"
#include "inventory.h"

/** Copies a fixed-size record after checking the caller's structure size. */
static int32_t copy_record(const void *source, size_t record_size, void *output,
                           uint32_t output_size)
{
    if (output_size < record_size)
    {
        return RS3_E_ABI;
    }
    memcpy(output, source, record_size);
    return RS3_OK;
}

/** Copies an array prefix after reporting the required count. */
static int32_t copy_records(const void *source, uint32_t record_count, size_t record_size,
                            void *output, uint32_t capacity, uint32_t *required)
{
    *required = record_count;
    if (capacity < record_count)
    {
        return RS3_E_BUFFER;
    }
    if (record_count != 0 && output == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    if (record_count != 0)
    {
        memcpy(output, source, record_count * record_size);
    }
    return RS3_OK;
}

int32_t rs3_snapshot_create(rs3_snapshot **out)
{
    if (out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    *out = calloc(1, sizeof **out);
    return *out != NULL ? RS3_OK : RS3_E_MEMORY;
}

void rs3_snapshot_destroy(rs3_snapshot *snapshot)
{
    free(snapshot);
}

int32_t rs3_snapshot_info(const rs3_snapshot *snapshot, rs3_info *out, uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->info, sizeof *out, out, out_size);
}

const char *rs3_snapshot_client_version(const rs3_snapshot *snapshot)
{
    return snapshot != NULL && snapshot->profile != NULL ? snapshot->profile->version : "";
}

int32_t rs3_snapshot_player(const rs3_snapshot *snapshot, rs3_player *out, uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->player, sizeof *out, out, out_size);
}

int32_t rs3_snapshot_position(const rs3_snapshot *snapshot, rs3_position *out, uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->position, sizeof *out, out, out_size);
}

int32_t rs3_snapshot_encounter(const rs3_snapshot *snapshot, rs3_encounter_state *out, uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->encounter, sizeof *out, out, out_size);
}

int32_t rs3_snapshot_combat(const rs3_snapshot *snapshot, rs3_combat_state *out, uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->combat, sizeof *out, out, out_size);
}

int32_t rs3_snapshot_player_hit_info(const rs3_snapshot *snapshot, rs3_player_hit_info *out,
                                     uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->player_hit_info, sizeof *out, out, out_size);
}

int32_t rs3_snapshot_player_hits(const rs3_snapshot *snapshot, rs3_hit *out, uint32_t capacity,
                                 uint32_t *required)
{
    if (snapshot == NULL || required == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_records(snapshot->player_hits, snapshot->player_hit_info.hit_count, sizeof *out,
                        out, capacity, required);
}

int32_t rs3_snapshot_inventory_info(const rs3_snapshot *snapshot, rs3_inventory_info *out,
                                    uint32_t out_size)
{
    if (snapshot == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_record(&snapshot->inventory_info, sizeof *out, out, out_size);
}

int32_t rs3_snapshot_npcs(const rs3_snapshot *snapshot, rs3_npc *out, uint32_t capacity,
                          uint32_t *required)
{
    if (snapshot == NULL || required == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_records(snapshot->npcs, snapshot->info.npc_count, sizeof *out, out, capacity,
                        required);
}

int32_t rs3_snapshot_npc_animations(const rs3_snapshot *snapshot, rs3_npc_animation *out,
                                    uint32_t capacity, uint32_t *required)
{
    if (snapshot == NULL || required == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_records(snapshot->npc_animations, snapshot->info.npc_count, sizeof *out, out,
                        capacity, required);
}

int32_t rs3_snapshot_hits(const rs3_snapshot *snapshot, rs3_hit *out, uint32_t capacity,
                          uint32_t *required)
{
    if (snapshot == NULL || required == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_records(snapshot->hits, snapshot->info.hit_count, sizeof *out, out, capacity,
                        required);
}

int32_t rs3_snapshot_abilities(const rs3_snapshot *snapshot, rs3_ability *out, uint32_t capacity,
                               uint32_t *required)
{
    if (snapshot == NULL || required == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    return copy_records(snapshot->abilities, snapshot->inventory_info.ability_count, sizeof *out,
                        out, capacity, required);
}

static int item_in_container(const rs3_item *item, uint32_t container_id)
{
    return container_id == RS3_CONTAINER_ALL || item->container_id == container_id;
}

int32_t rs3_snapshot_items(const rs3_snapshot *snapshot, uint32_t container_id, rs3_item *out,
                           uint32_t capacity, uint32_t *required)
{
    if (snapshot == NULL || required == NULL ||
        (container_id != RS3_CONTAINER_ALL && container_id != RS3_CONTAINER_BACKPACK &&
         container_id != RS3_CONTAINER_EQUIPMENT))
    {
        return RS3_E_ARGUMENT;
    }
    uint32_t item_count =
        snapshot->inventory_info.backpack_count + snapshot->inventory_info.equipment_count;
    *required = 0;
    for (uint32_t index = 0; index < item_count; index++)
    {
        if (item_in_container(&snapshot->items[index], container_id))
        {
            (*required)++;
        }
    }
    if (capacity < *required)
    {
        return RS3_E_BUFFER;
    }
    if (*required != 0 && out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    uint32_t written = 0;
    for (uint32_t index = 0; index < item_count; index++)
    {
        if (item_in_container(&snapshot->items[index], container_id))
        {
            out[written++] = snapshot->items[index];
        }
    }
    return RS3_OK;
}
