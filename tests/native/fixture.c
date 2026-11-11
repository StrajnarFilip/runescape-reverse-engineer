/* Deterministic fake-memory fixtures for the native decoder tests. */
#include "fixture.h"

#include <stdlib.h>
#include <string.h>

#include "catalog.h"
#include "clock.h"
#include "decode_utils.h"
#include "frame.h"
#include "inventory.h"

#define DOMAIN_NODE_STRIDE             256u
#define REGISTRY_HANDLER_VTABLE_OFFSET 16u
#define NPC_ACTOR_RUNTIME_ID_OFFSET    0x88u
#define FIXTURE_PRAYER_BASE_LEVEL      84u

/** Serves a read from the fixture blocks, applying an arranged mutation first. */
static int read_fixture_memory(void *user_data, uint64_t address, void *output, size_t size)
{
    fixture *test_fixture = user_data;
    if (address == test_fixture->trigger_address &&
        ++test_fixture->trigger_read_count == test_fixture->mutate_on_read)
    {
        fixture_put_u32(test_fixture, test_fixture->mutation_address, test_fixture->mutation_value);
    }
    unsigned char *source = fixture_bytes(test_fixture, address, size);
    if (source == NULL)
    {
        return 0;
    }
    memcpy(output, source, size);
    return 1;
}

void fixture_add_block(fixture *test_fixture, uint64_t base_address, size_t size)
{
    assert(test_fixture->block_count <
           sizeof test_fixture->blocks / sizeof test_fixture->blocks[0]);
    memory_block *block = &test_fixture->blocks[test_fixture->block_count++];
    block->base_address = base_address;
    block->size = size;
    block->data = calloc(1, size);
    assert(block->data != NULL);
}

unsigned char *fixture_bytes(fixture *test_fixture, uint64_t address, size_t size)
{
    for (unsigned index = 0; index < test_fixture->block_count; index++)
    {
        memory_block *block = &test_fixture->blocks[index];
        if (address >= block->base_address && address - block->base_address <= block->size &&
            size <= block->size - (size_t)(address - block->base_address))
        {
            return block->data + (size_t)(address - block->base_address);
        }
    }
    return NULL;
}

void fixture_put_u32(fixture *test_fixture, uint64_t address, uint32_t value)
{
    unsigned char *destination = fixture_bytes(test_fixture, address, sizeof value);
    assert(destination != NULL);
    memcpy(destination, &value, sizeof value);
}

void fixture_put_u64(fixture *test_fixture, uint64_t address, uint64_t value)
{
    unsigned char *destination = fixture_bytes(test_fixture, address, sizeof value);
    assert(destination != NULL);
    memcpy(destination, &value, sizeof value);
}

uint64_t fixture_get_u64(fixture *test_fixture, uint64_t address)
{
    unsigned char *source = fixture_bytes(test_fixture, address, sizeof(uint64_t));
    assert(source != NULL);
    return decode_u64(source);
}

void fixture_put_variable(fixture *test_fixture, uint64_t table, uint64_t node,
                          uint32_t variable_id, uint64_t value)
{
    uint64_t bucket = table + (variable_id % FIXTURE_BUCKET_COUNT) * sizeof(uint64_t);
    fixture_put_u64(test_fixture, node + LAYOUT_VARIABLE_NODE_NEXT_OFFSET,
                    fixture_get_u64(test_fixture, bucket));
    fixture_put_u64(test_fixture, bucket, node);
    fixture_put_u32(test_fixture, node + LAYOUT_VARIABLE_NODE_KEY_OFFSET, variable_id);
    fixture_put_u64(test_fixture, node + LAYOUT_VARIABLE_NODE_VALUE_OFFSET, value);
}

void fixture_set_slot(fixture *test_fixture, uint64_t slots, unsigned slot, int32_t item_id,
                      uint32_t quantity)
{
    fixture_put_u32(test_fixture, slots + slot * 8, (uint32_t)item_id);
    fixture_put_u32(test_fixture, slots + slot * 8 + 4, quantity);
}

void fixture_mutate_on_read(fixture *test_fixture, uint64_t trigger_address, unsigned read_number,
                            uint64_t mutation_address, uint32_t mutation_value)
{
    test_fixture->trigger_address = trigger_address;
    test_fixture->trigger_read_count = 0;
    test_fixture->mutate_on_read = read_number;
    test_fixture->mutation_address = mutation_address;
    test_fixture->mutation_value = mutation_value;
}

void fixture_clear_mutation(fixture *test_fixture)
{
    test_fixture->trigger_address = 0;
}

fixture *fixture_create_empty(void)
{
    fixture *test_fixture = calloc(1, sizeof *test_fixture);
    assert(test_fixture != NULL);
    assert(rs3_create(NULL, &test_fixture->context) == RS3_OK);
    assert(rs3_snapshot_create(&test_fixture->snapshot) == RS3_OK);
    rs3_context *context = test_fixture->context;
    context->read_memory = read_fixture_memory;
    context->read_memory_user_data = test_fixture;
    context->target.module_base = FIXTURE_MODULE_BASE;
    context->target.pid = FIXTURE_PID;
    context->target.process_creation_time = FIXTURE_PROCESS_CREATION_TIME;
    return test_fixture;
}

/** Maps the domain registry with the player node at the root and the client to its left. */
static void add_domain_registry(fixture *test_fixture, const client_profile *profile)
{
    uint64_t registry_root =
        FIXTURE_MODULE_BASE + profile->registry_rva + LAYOUT_REGISTRY_ROOT_OFFSET;
    fixture_add_block(test_fixture, registry_root, 8);
    fixture_add_block(test_fixture, FIXTURE_DOMAIN_NODES, 512);
    fixture_add_block(test_fixture, FIXTURE_FUNCTION_OBJECTS, 512);
    fixture_add_block(test_fixture, FIXTURE_FUNCTION_VTABLES, 512);
    fixture_put_u64(test_fixture, registry_root, FIXTURE_DOMAIN_NODES);
    for (unsigned domain = 0; domain < 2; domain++)
    {
        int is_client = domain == 1;
        uint64_t node = FIXTURE_DOMAIN_NODES + domain * DOMAIN_NODE_STRIDE;
        uint64_t function_object = FIXTURE_FUNCTION_OBJECTS + domain * DOMAIN_NODE_STRIDE;
        uint64_t function_vtable = FIXTURE_FUNCTION_VTABLES + domain * DOMAIN_NODE_STRIDE;
        uint32_t handler_rva =
            is_client ? profile->client_handler_rva : profile->player_handler_rva;
        fixture_put_u32(test_fixture, node + LAYOUT_TREE_KEY_OFFSET,
                        is_client ? LAYOUT_CLIENT_DOMAIN_ID : LAYOUT_PLAYER_DOMAIN_ID);
        fixture_put_u64(test_fixture, node + LAYOUT_FUNCTION_POINTER_OFFSET, function_object);
        fixture_put_u64(test_fixture, function_object, function_vtable);
        fixture_put_u64(test_fixture, function_vtable + REGISTRY_HANDLER_VTABLE_OFFSET,
                        FIXTURE_MODULE_BASE + handler_rva);
        fixture_put_u64(test_fixture, function_object + LAYOUT_FUNCTION_CAPTURE_OFFSET,
                        is_client ? FIXTURE_CLIENT : FIXTURE_PLAYER);
    }
    fixture_put_u64(test_fixture, FIXTURE_DOMAIN_NODES + LAYOUT_TREE_LEFT_OFFSET,
                    FIXTURE_DOMAIN_NODES + DOMAIN_NODE_STRIDE);
}

/** Maps the player, client and world contexts with statistics and the ten variables. */
static void add_player(fixture *test_fixture, const client_profile *profile)
{
    fixture_add_block(test_fixture, FIXTURE_PLAYER, LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET + 16);
    fixture_add_block(test_fixture, FIXTURE_CLIENT, 0x7400);
    fixture_add_block(test_fixture, FIXTURE_WORLD, 0x1a000);
    fixture_put_u32(test_fixture, FIXTURE_WORLD + LAYOUT_GAME_STATE_OFFSET, LAYOUT_IN_GAME_STATE);
    fixture_put_u64(test_fixture, FIXTURE_CLIENT, FIXTURE_MODULE_BASE + profile->client_vtable_rva);
    fixture_put_u64(test_fixture, FIXTURE_CLIENT + LAYOUT_STATE_POINTER_OFFSET, FIXTURE_WORLD);
    fixture_put_u32(test_fixture, FIXTURE_WORLD + LAYOUT_LOGIC_CLOCK_OFFSET, FIXTURE_LOGIC_TICK);

    fixture_add_block(test_fixture, FIXTURE_SKILLS_MANAGER, 32);
    fixture_add_block(test_fixture, FIXTURE_SKILL_ENTRIES, 6 * LAYOUT_SKILL_STRIDE);
    fixture_put_u64(test_fixture, FIXTURE_CLIENT + LAYOUT_SKILLS_MANAGER_OFFSET,
                    FIXTURE_SKILLS_MANAGER);
    fixture_put_u64(test_fixture, FIXTURE_SKILLS_MANAGER + LAYOUT_SKILLS_ENTRIES_OFFSET,
                    FIXTURE_SKILL_ENTRIES);
    fixture_put_u32(test_fixture,
                    FIXTURE_SKILL_ENTRIES + 5 * LAYOUT_SKILL_STRIDE +
                        LAYOUT_SKILL_BASE_LEVEL_OFFSET,
                    FIXTURE_PRAYER_BASE_LEVEL);

    size_t table_bytes = (FIXTURE_BUCKET_COUNT + 1) * sizeof(uint64_t);
    fixture_add_block(test_fixture, FIXTURE_PLAYER_TABLE, table_bytes);
    fixture_add_block(test_fixture, FIXTURE_CLIENT_TABLE, table_bytes);
    fixture_add_block(test_fixture, FIXTURE_VARIABLE_NODES,
                      FIXTURE_VARIABLE_NODE_BYTES * RS3_VARIABLE_COUNT);
    fixture_put_u64(test_fixture, FIXTURE_PLAYER + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET,
                    FIXTURE_PLAYER_TABLE);
    fixture_put_u64(test_fixture, FIXTURE_PLAYER + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET + 8,
                    FIXTURE_BUCKET_COUNT);
    fixture_put_u64(test_fixture, FIXTURE_CLIENT + LAYOUT_CLIENT_VARIABLE_TABLE_OFFSET,
                    FIXTURE_CLIENT_TABLE);
    fixture_put_u64(test_fixture, FIXTURE_CLIENT + LAYOUT_CLIENT_VARIABLE_TABLE_OFFSET + 8,
                    FIXTURE_BUCKET_COUNT);
    fixture_put_u64(test_fixture, FIXTURE_PLAYER_TABLE + FIXTURE_BUCKET_COUNT * 8,
                    FIXTURE_SENTINEL);
    fixture_put_u64(test_fixture, FIXTURE_CLIENT_TABLE + FIXTURE_BUCKET_COUNT * 8,
                    FIXTURE_SENTINEL);

    /* Health, max health, prayer, adrenaline, server GCD, client GCD and Telos. */
    static const int64_t variable_values[RS3_VARIABLE_COUNT] = {8700, 8700, 7635, 500,  0,
                                                                0,    3,    1000, 1090, 0};
    for (unsigned variable = 0; variable < RS3_VARIABLE_COUNT; variable++)
    {
        int client_variable = variable == RS3_GCD_CLIENT_START || variable == RS3_GCD_CLIENT_END;
        fixture_put_variable(test_fixture,
                             client_variable ? FIXTURE_CLIENT_TABLE : FIXTURE_PLAYER_TABLE,
                             FIXTURE_VARIABLE_NODES + variable * FIXTURE_VARIABLE_NODE_BYTES,
                             profile_variable_ids[variable], (uint64_t)variable_values[variable]);
    }
}

/** Registers one loaded Goblin with known health and a single active hit record. */
static void add_npc(fixture *test_fixture, const client_profile *profile)
{
    fixture_add_block(test_fixture, FIXTURE_NPC_MANAGER, LAYOUT_NPC_COUNT_OFFSET + 4);
    fixture_add_block(test_fixture, FIXTURE_NPC_TABLE, (FIXTURE_BUCKET_COUNT + 1) * 8);
    fixture_add_block(test_fixture, FIXTURE_NPC_NODE, 32);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + LAYOUT_NPC_MANAGER_OFFSET, FIXTURE_NPC_MANAGER);
    fixture_put_u64(test_fixture, FIXTURE_NPC_MANAGER,
                    FIXTURE_MODULE_BASE + profile->npc_manager_vtable_rva);
    fixture_put_u64(test_fixture, FIXTURE_NPC_MANAGER + 16, FIXTURE_NPC_TABLE);
    fixture_put_u64(test_fixture, FIXTURE_NPC_MANAGER + 24, FIXTURE_BUCKET_COUNT);
    fixture_put_u32(test_fixture, FIXTURE_NPC_MANAGER + LAYOUT_NPC_COUNT_OFFSET, 1);
    fixture_put_u32(test_fixture, FIXTURE_NPC_MANAGER + LAYOUT_NPC_INDICES_OFFSET,
                    FIXTURE_NPC_RUNTIME_ID);
    fixture_put_u64(test_fixture, FIXTURE_NPC_TABLE + FIXTURE_NPC_RUNTIME_ID * 8, FIXTURE_NPC_NODE);
    fixture_put_u64(test_fixture, FIXTURE_NPC_TABLE + FIXTURE_BUCKET_COUNT * 8, FIXTURE_SENTINEL);
    fixture_put_u32(test_fixture, FIXTURE_NPC_NODE, FIXTURE_NPC_RUNTIME_ID);
    fixture_put_u64(test_fixture, FIXTURE_NPC_NODE + 16, FIXTURE_ACTOR);

    fixture_add_block(test_fixture, FIXTURE_ACTOR, LAYOUT_NPC_HEALTH_MAX_OFFSET + 4u);
    fixture_add_block(test_fixture, FIXTURE_NPC_CONFIG, 16);
    fixture_add_block(test_fixture, FIXTURE_HIT_RING, 40);
    fixture_add_block(test_fixture, FIXTURE_HIT_RECORD, 24);
    fixture_put_u64(test_fixture, FIXTURE_ACTOR,
                    FIXTURE_MODULE_BASE + profile->npc_entity_vtable_rva);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + NPC_ACTOR_RUNTIME_ID_OFFSET,
                    FIXTURE_NPC_RUNTIME_ID);
    /* Inline client string: six bytes and a tag holding the 17 unused bytes. */
    unsigned char *name = fixture_bytes(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_NAME_OFFSET, 24);
    memcpy(name, "Goblin", 6);
    name[23] = 17;
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_CONFIG_OFFSET, FIXTURE_NPC_CONFIG);
    fixture_put_u32(test_fixture, FIXTURE_NPC_CONFIG + 8, 12357);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_HEALTH_CURRENT_OFFSET, 326);
    fixture_put_u32(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_HEALTH_MAX_OFFSET, 550);
    fixture_put_u64(test_fixture, FIXTURE_ACTOR + LAYOUT_NPC_HITMARKS_OFFSET, FIXTURE_HIT_RING);
    fixture_put_u32(test_fixture, FIXTURE_HIT_RING + 4, 1);
    fixture_put_u64(test_fixture, FIXTURE_HIT_RING + 24, 1);
    fixture_put_u64(test_fixture, FIXTURE_HIT_RING + 32, FIXTURE_HIT_RECORD);
    /* Type, amount, end tick, secondary type, secondary amount and duration. */
    int32_t hit_record[6] = {133, 224, 1060, -1, -1, 60};
    memcpy(fixture_bytes(test_fixture, FIXTURE_HIT_RECORD, sizeof hit_record), hit_record,
           sizeof hit_record);
}

fixture *fixture_create_for_profile(const client_profile *profile)
{
    fixture *test_fixture = fixture_create_empty();
    test_fixture->context->target.profile = profile;
    add_domain_registry(test_fixture, profile);
    add_player(test_fixture, profile);
    add_npc(test_fixture, profile);
    return test_fixture;
}

fixture *fixture_create(void)
{
    return fixture_create_for_profile(&profile_primary);
}

/** Maps the backpack and equipment registry entries with every slot empty. */
static void add_inventory_registry(fixture *test_fixture)
{
    fixture_put_u64(test_fixture, FIXTURE_WORLD + LAYOUT_INVENTORY_MANAGER_OFFSET,
                    FIXTURE_INVENTORY_MANAGER);
    fixture_add_block(test_fixture, FIXTURE_INVENTORY_MANAGER, 32);
    fixture_add_block(test_fixture, FIXTURE_INVENTORY_ENTRIES, 2 * INVENTORY_REGISTRY_ENTRY_BYTES);
    fixture_add_block(test_fixture, FIXTURE_BACKPACK_SLOTS, BACKPACK_SLOT_COUNT * 8);
    fixture_add_block(test_fixture, FIXTURE_EQUIPMENT_SLOTS, EQUIPMENT_SLOT_COUNT * 8);
    uint64_t entries_end = FIXTURE_INVENTORY_ENTRIES + 2 * INVENTORY_REGISTRY_ENTRY_BYTES;
    fixture_put_u64(test_fixture, FIXTURE_INVENTORY_MANAGER + 8, FIXTURE_INVENTORY_ENTRIES);
    fixture_put_u64(test_fixture, FIXTURE_INVENTORY_MANAGER + 16, entries_end);
    fixture_put_u64(test_fixture, FIXTURE_INVENTORY_MANAGER + 24, entries_end);
    for (unsigned container = 0; container < 2; container++)
    {
        int is_equipment = container == 1;
        uint64_t entry = FIXTURE_INVENTORY_ENTRIES + container * INVENTORY_REGISTRY_ENTRY_BYTES;
        uint64_t slots = is_equipment ? FIXTURE_EQUIPMENT_SLOTS : FIXTURE_BACKPACK_SLOTS;
        unsigned slot_count = is_equipment ? EQUIPMENT_SLOT_COUNT : BACKPACK_SLOT_COUNT;
        uint32_t container_id = is_equipment ? RS3_CONTAINER_EQUIPMENT : RS3_CONTAINER_BACKPACK;
        fixture_put_u32(test_fixture, entry, container_id * 2);
        fixture_put_u32(test_fixture, entry + 16, container_id);
        fixture_put_u64(test_fixture, entry + 24, slots);
        fixture_put_u64(test_fixture, entry + 32, slots + slot_count * 8);
        fixture_put_u64(test_fixture, entry + 40, slots + slot_count * 8);
        for (unsigned slot = 0; slot < slot_count; slot++)
        {
            fixture_set_slot(test_fixture, slots, slot, -1, 0);
        }
    }
}

/** Adds the four ability timer variables and the Bone Shield variable. */
static void add_extra_variables(fixture *test_fixture)
{
    static const uint32_t variable_ids[5] = {FIXTURE_FLURRY_START_VAR, FIXTURE_FLURRY_END_VAR,
                                             FIXTURE_RESONANCE_START_VAR, FIXTURE_RESONANCE_END_VAR,
                                             LAYOUT_INVENTORY_BONE_SHIELD_VARP};
    static const uint32_t variable_values[5] = {1000, 2020, 1000, 2500, 0};
    fixture_add_block(test_fixture, FIXTURE_EXTRA_VARIABLES, 5 * FIXTURE_VARIABLE_NODE_BYTES);
    for (unsigned index = 0; index < 5; index++)
    {
        uint64_t table =
            index == FIXTURE_BONE_SHIELD_NODE_INDEX ? FIXTURE_PLAYER_TABLE : FIXTURE_CLIENT_TABLE;
        fixture_put_variable(test_fixture, table,
                             FIXTURE_EXTRA_VARIABLES + index * FIXTURE_VARIABLE_NODE_BYTES,
                             variable_ids[index], variable_values[index]);
    }
}

/** Replaces the context catalog with a tiny synthetic one; tests never need game data. */
static void install_synthetic_catalog(fixture *test_fixture)
{
    enum
    {
        ITEM_COUNT = 8,
        ABILITY_COUNT = 2,
        STRING_CAPACITY = 200
    };
    static const char *const names[ITEM_COUNT + ABILITY_COUNT] = {
        "Sword",         "Off-hand sword", "Two-handed sword", "Shield", "Defender",
        "Test brew (6)", "Test brew (5)",  "Other item",       "Flurry", "Resonance"};
    static const int32_t item_ids[ITEM_COUNT] = {
        FIXTURE_ITEM_SWORD,  FIXTURE_ITEM_OFF_HAND_SWORD, FIXTURE_ITEM_TWO_HANDED_SWORD,
        FIXTURE_ITEM_SHIELD, FIXTURE_ITEM_DEFENDER,       FIXTURE_ITEM_BREW_6,
        FIXTURE_ITEM_BREW_5, FIXTURE_ITEM_OTHER};

    item_catalog *catalog = &test_fixture->context->inventory->catalog;
    catalog_free(catalog);
    size_t records_bytes = CATALOG_HEADER_BYTES + ITEM_COUNT * CATALOG_ITEM_RECORD_BYTES +
                           ABILITY_COUNT * CATALOG_ABILITY_RECORD_BYTES;
    catalog->data = calloc(1, records_bytes + STRING_CAPACITY);
    assert(catalog->data != NULL);
    catalog->item_count = ITEM_COUNT;
    catalog->ability_count = ABILITY_COUNT;
    catalog->status = RS3_AVAILABLE;
    unsigned char *item_records = catalog->data + CATALOG_HEADER_BYTES;
    unsigned char *ability_records = item_records + ITEM_COUNT * CATALOG_ITEM_RECORD_BYTES;
    char *strings = (char *)ability_records + ABILITY_COUNT * CATALOG_ABILITY_RECORD_BYTES;
    catalog->item_records = item_records;
    catalog->ability_records = ability_records;
    catalog->strings = strings;

    uint32_t name_offsets[ITEM_COUNT + ABILITY_COUNT];
    uint32_t string_position = 1; /* Offset zero is the empty string. */
    for (unsigned index = 0; index < ITEM_COUNT + ABILITY_COUNT; index++)
    {
        name_offsets[index] = string_position;
        strcpy(strings + string_position, names[index]);
        string_position += (uint32_t)strlen(names[index]) + 1;
    }
    catalog->strings_size = string_position;
    catalog->size = records_bytes + string_position;

    for (unsigned index = 0; index < ITEM_COUNT; index++)
    {
        unsigned char *record = item_records + index * CATALOG_ITEM_RECORD_BYTES;
        encode_u32(record + CATALOG_ITEM_ID, (uint32_t)item_ids[index]);
        encode_u32(record + CATALOG_ITEM_NAME_OFFSET, name_offsets[index]);
        encode_u32(record + CATALOG_ITEM_DOSES, UINT32_MAX);
        encode_u32(record + CATALOG_ITEM_NEXT_DOSE_ITEM_ID, UINT32_MAX);
        encode_u32(record + CATALOG_ITEM_EQUIP_SLOT, UINT32_MAX);
    }
    /* Weapons and shields: equip slot, melee style and equipment flags. */
    static const uint32_t gear_equip_slots[5] = {3, 5, 3, 5, 5};
    static const uint32_t gear_flags[5] = {RS3_WEAPON, RS3_WEAPON, RS3_WEAPON | RS3_TWO_HANDED,
                                           RS3_SHIELD, RS3_WEAPON | RS3_SHIELD | RS3_DEFENDER};
    for (unsigned index = 0; index < 5; index++)
    {
        unsigned char *record = item_records + index * CATALOG_ITEM_RECORD_BYTES;
        encode_u32(record + CATALOG_ITEM_EQUIP_SLOT, gear_equip_slots[index]);
        encode_u32(record + CATALOG_ITEM_COMBAT_STYLE, RS3_STYLE_MELEE);
        encode_u32(record + CATALOG_ITEM_EQUIPMENT_FLAGS, gear_flags[index]);
    }
    /* A six-dose brew whose next dose is the five-dose brew. */
    for (unsigned index = 5; index <= 6; index++)
    {
        unsigned char *record = item_records + index * CATALOG_ITEM_RECORD_BYTES;
        int six_doses = index == 5;
        encode_u32(record + CATALOG_ITEM_FAMILY_ID, 19);
        encode_u32(record + CATALOG_ITEM_DOSES, six_doses ? 6 : 5);
        encode_u32(record + CATALOG_ITEM_NEXT_DOSE_ITEM_ID,
                   six_doses ? (uint32_t)FIXTURE_ITEM_BREW_5 : UINT32_MAX);
        encode_u32(record + CATALOG_ITEM_EFFECT_FLAGS, RS3_HEALS);
        encode_u32(record + CATALOG_ITEM_BASE_HEAL, 1000);
    }
    /* Flurry needs dual wield in melee; Resonance needs a shield. */
    for (unsigned index = 0; index < ABILITY_COUNT; index++)
    {
        unsigned char *record = ability_records + index * CATALOG_ABILITY_RECORD_BYTES;
        int is_resonance = index == 1;
        encode_u32(record + CATALOG_ABILITY_STRUCT_ID, is_resonance ? 14713 : 14684);
        encode_u32(record + CATALOG_ABILITY_NAME_OFFSET, name_offsets[ITEM_COUNT + index]);
        encode_u32(record + CATALOG_ABILITY_START_VAR,
                   is_resonance ? FIXTURE_RESONANCE_START_VAR : FIXTURE_FLURRY_START_VAR);
        encode_u32(record + CATALOG_ABILITY_END_VAR,
                   is_resonance ? FIXTURE_RESONANCE_END_VAR : FIXTURE_FLURRY_END_VAR);
        encode_u32(record + CATALOG_ABILITY_BASE_COOLDOWN_MS, is_resonance ? 30000 : 20400);
        encode_u32(record + CATALOG_ABILITY_REQUIREMENTS,
                   is_resonance ? RS3_REQ_SHIELD : RS3_REQ_DUAL_WIELD);
        encode_u32(record + CATALOG_ABILITY_COMBAT_STYLE,
                   is_resonance ? RS3_STYLE_NONE : RS3_STYLE_MELEE);
        encode_u32(record + CATALOG_ABILITY_TIMER_SOURCE, RS3_TIMER_CLIENT_PAIR);
        encode_u32(record + CATALOG_ABILITY_DEFINITION_FLAGS, CATALOG_ABILITY_IN_BOOK);
    }
    assert(catalog_build_item_index(catalog));
}

fixture *fixture_create_inventory(void)
{
    fixture *test_fixture = fixture_create();
    add_inventory_registry(test_fixture);
    add_extra_variables(test_fixture);
    install_synthetic_catalog(test_fixture);
    fixture_set_slot(test_fixture, FIXTURE_BACKPACK_SLOTS, 0, FIXTURE_ITEM_BREW_6, 1);
    fixture_set_slot(test_fixture, FIXTURE_BACKPACK_SLOTS, 1, FIXTURE_ITEM_OTHER, 1);
    fixture_set_slot(test_fixture, FIXTURE_EQUIPMENT_SLOTS, EQUIPMENT_SLOT_MAIN_HAND,
                     FIXTURE_ITEM_SWORD, 1);
    fixture_set_slot(test_fixture, FIXTURE_EQUIPMENT_SLOTS, EQUIPMENT_SLOT_OFF_HAND,
                     FIXTURE_ITEM_OFF_HAND_SWORD, 1);
    return test_fixture;
}

int fixture_decode(fixture *test_fixture)
{
    rs3_snapshot *snapshot = test_fixture->snapshot;
    memset(snapshot, 0, sizeof *snapshot);
    snapshot->info.timestamp_ns = FIXTURE_TIMESTAMP_NS;
    snapshot->info.sequence = ++test_fixture->context->sample_sequence;
    snapshot->info.monotonic_ns = clock_monotonic_ns();
    return frame_decode(test_fixture->context, snapshot);
}

void fixture_destroy(fixture *test_fixture)
{
    rs3_destroy(test_fixture->context);
    rs3_snapshot_destroy(test_fixture->snapshot);
    for (unsigned index = 0; index < test_fixture->block_count; index++)
    {
        free(test_fixture->blocks[index].data);
    }
    free(test_fixture);
}
