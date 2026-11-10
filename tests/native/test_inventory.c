/* Inventory identities, sparse slots, ability cooldowns, gear checks and the catalog loader. */
#include "fixture.h"

#include <stdlib.h>
#include <string.h>

#include "catalog.h"
#include "decode_utils.h"
#include "inventory.h"

static item_tracker *tracker_of(fixture *test_fixture)
{
    return &test_fixture->context->inventory->items;
}

static const rs3_item_event *item_event(fixture *test_fixture, uint64_t sequence)
{
    return &tracker_of(test_fixture)->events[sequence - 1];
}

static void set_backpack(fixture *test_fixture, unsigned slot, int32_t item_id, uint32_t quantity)
{
    fixture_set_slot(test_fixture, FIXTURE_BACKPACK_SLOTS, slot, item_id, quantity);
}

static void set_equipment(fixture *test_fixture, unsigned slot, int32_t item_id)
{
    fixture_set_slot(test_fixture, FIXTURE_EQUIPMENT_SLOTS, slot, item_id, item_id >= 0 ? 1 : 0);
}

static void tracks_moves_doses_replacements_and_quantities(void)
{
    fixture *test_fixture = fixture_create_inventory();
    rs3_item *items = test_fixture->snapshot->items;
    assert(fixture_decode(test_fixture));
    assert(test_fixture->snapshot->inventory_info.backpack_count == BACKPACK_SLOT_COUNT);
    assert(test_fixture->snapshot->inventory_info.equipment_count == EQUIPMENT_SLOT_COUNT);
    assert(items[0].doses == 6 && items[0].base_heal == 1000);
    uint64_t token = items[0].token;
    assert(token != 0 && tracker_of(test_fixture)->event_sequence == 0);

    /* A move keeps the token. */
    set_backpack(test_fixture, 0, -1, 0);
    set_backpack(test_fixture, 8, FIXTURE_ITEM_BREW_6, 1);
    assert(fixture_decode(test_fixture));
    assert(items[8].token == token && item_event(test_fixture, 1)->kind == RS3_ITEM_MOVED);

    /* Drinking one dose keeps the token. */
    set_backpack(test_fixture, 8, FIXTURE_ITEM_BREW_5, 1);
    assert(fixture_decode(test_fixture));
    const rs3_item_event *event = item_event(test_fixture, 2);
    assert(items[8].token == token && event->kind == RS3_ITEM_DOSES_CHANGED);
    assert(event->before.doses == 6 && event->after.doses == 5);
    rs3_item zero_family_before = event->before;
    rs3_item zero_family_after = event->after;
    zero_family_before.family_id = 0;
    zero_family_after.family_id = 0;
    assert(inventory_same_item(&zero_family_before, &zero_family_after, 1));

    /* A move and a consumed dose between the same pair of observations. */
    set_backpack(test_fixture, 8, -1, 0);
    set_backpack(test_fixture, 0, FIXTURE_ITEM_BREW_6, 1);
    assert(fixture_decode(test_fixture));
    token = items[0].token;
    set_backpack(test_fixture, 0, -1, 0);
    set_backpack(test_fixture, 7, FIXTURE_ITEM_BREW_5, 1);
    assert(fixture_decode(test_fixture));
    assert(items[7].token == token && item_event(test_fixture, 5)->kind == RS3_ITEM_DOSES_CHANGED);

    /* A different item in the same slot is a replacement with a new token. */
    set_backpack(test_fixture, 7, FIXTURE_ITEM_OTHER, 1);
    assert(fixture_decode(test_fixture));
    assert(items[7].token != token);
    event = item_event(test_fixture, 6);
    assert(event->kind == RS3_ITEM_CHANGED);
    assert(event->before.item_id == FIXTURE_ITEM_BREW_5);
    assert(event->after.item_id == FIXTURE_ITEM_OTHER);

    /* A quantity change keeps the token. */
    token = items[7].token;
    set_backpack(test_fixture, 7, FIXTURE_ITEM_OTHER, 10);
    assert(fixture_decode(test_fixture));
    assert(items[7].token == token);
    assert(item_event(test_fixture, 7)->kind == RS3_ITEM_QUANTITY_CHANGED);

    uint32_t count;
    assert(rs3_snapshot_items(test_fixture->snapshot, RS3_CONTAINER_BACKPACK, NULL, 0, &count) ==
           RS3_E_BUFFER);
    assert(count == BACKPACK_SLOT_COUNT);
    assert(rs3_snapshot_items(test_fixture->snapshot, RS3_CONTAINER_EQUIPMENT, NULL, 0, &count) ==
           RS3_E_BUFFER);
    assert(count == EQUIPMENT_SLOT_COUNT);
    fixture_destroy(test_fixture);
}

static void sparse_slot_vectors_report_empty_slots(void)
{
    fixture *test_fixture = fixture_create_inventory();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    uint64_t backpack_entry = FIXTURE_INVENTORY_ENTRIES;

    /* Slots past the vector end are empty, as in the client's slot getters. */
    fixture_put_u64(test_fixture, backpack_entry + 32, FIXTURE_BACKPACK_SLOTS + 8);
    assert(fixture_decode(test_fixture));
    assert(snapshot->inventory_info.backpack_count == BACKPACK_SLOT_COUNT);
    assert(snapshot->items[0].item_id == FIXTURE_ITEM_BREW_6 && snapshot->items[1].item_id == -1);

    /* An empty vector without storage is a fully empty container. */
    fixture_put_u64(test_fixture, backpack_entry + 24, 0);
    fixture_put_u64(test_fixture, backpack_entry + 32, 0);
    fixture_put_u64(test_fixture, backpack_entry + 40, 0);
    assert(fixture_decode(test_fixture));
    assert(snapshot->inventory_info.backpack_count == BACKPACK_SLOT_COUNT);
    for (unsigned slot = 0; slot < BACKPACK_SLOT_COUNT; slot++)
    {
        assert(snapshot->items[slot].item_id == -1 && snapshot->items[slot].quantity == 0);
    }
    assert(snapshot->inventory_info.equipment_count == EQUIPMENT_SLOT_COUNT);
    assert(snapshot->items[BACKPACK_SLOT_COUNT + EQUIPMENT_SLOT_MAIN_HAND].item_id ==
           FIXTURE_ITEM_SWORD);
    fixture_destroy(test_fixture);
}

static void evaluates_cooldowns_and_weapon_requirements(void)
{
    fixture *test_fixture = fixture_create_inventory();
    item_catalog *catalog = &test_fixture->context->inventory->catalog;
    unsigned char *flurry_record = (unsigned char *)catalog->ability_records;
    unsigned char *sword_record = (unsigned char *)catalog->item_records;
    assert(fixture_decode(test_fixture));
    rs3_ability *flurry = &test_fixture->snapshot->abilities[0];
    rs3_ability *resonance = &test_fixture->snapshot->abilities[1];
    assert(flurry->flags & RS3_ABILITY_TIMER_VALID);
    assert(flurry->duration_ms == 20400 && flurry->remaining_ms == 20300);
    assert(flurry->equipment_match == RS3_EQUIPMENT_ALLOWED);
    assert(resonance->equipment_match == RS3_EQUIPMENT_BLOCKED);

    /* A two-handed sword blocks dual wield but satisfies a two-handed requirement. */
    set_equipment(test_fixture, EQUIPMENT_SLOT_MAIN_HAND, FIXTURE_ITEM_TWO_HANDED_SWORD);
    set_equipment(test_fixture, EQUIPMENT_SLOT_OFF_HAND, -1);
    assert(fixture_decode(test_fixture));
    assert(flurry->equipment_match == RS3_EQUIPMENT_BLOCKED);
    encode_u32(flurry_record + CATALOG_ABILITY_REQUIREMENTS, RS3_REQ_TWO_HANDED);
    assert(fixture_decode(test_fixture));
    assert(flurry->equipment_match == RS3_EQUIPMENT_ALLOWED);

    /* A shield allows Resonance; a defender allows both. */
    encode_u32(flurry_record + CATALOG_ABILITY_REQUIREMENTS, RS3_REQ_DUAL_WIELD);
    set_equipment(test_fixture, EQUIPMENT_SLOT_MAIN_HAND, FIXTURE_ITEM_SWORD);
    set_equipment(test_fixture, EQUIPMENT_SLOT_OFF_HAND, FIXTURE_ITEM_SHIELD);
    assert(fixture_decode(test_fixture));
    assert(resonance->equipment_match == RS3_EQUIPMENT_ALLOWED);
    assert(flurry->equipment_match == RS3_EQUIPMENT_BLOCKED);
    set_equipment(test_fixture, EQUIPMENT_SLOT_OFF_HAND, FIXTURE_ITEM_DEFENDER);
    assert(fixture_decode(test_fixture));
    assert(flurry->equipment_match == RS3_EQUIPMENT_ALLOWED);
    assert(resonance->equipment_match == RS3_EQUIPMENT_ALLOWED);

    /* An active Bone Shield may substitute for a shield: the result stays unknown. */
    uint64_t bone_shield_value = FIXTURE_EXTRA_VARIABLES +
                                 FIXTURE_BONE_SHIELD_NODE_INDEX * FIXTURE_VARIABLE_NODE_BYTES +
                                 LAYOUT_VARIABLE_NODE_VALUE_OFFSET;
    set_equipment(test_fixture, EQUIPMENT_SLOT_OFF_HAND, -1);
    fixture_put_u32(test_fixture, bone_shield_value, 1u << 30);
    assert(fixture_decode(test_fixture));
    assert(resonance->equipment_match == RS3_EQUIPMENT_UNKNOWN);

    /* A shield-capable main-hand weapon allows Resonance. */
    encode_u32(sword_record + CATALOG_ITEM_EQUIPMENT_FLAGS, RS3_WEAPON | RS3_SHIELD);
    assert(fixture_decode(test_fixture));
    assert(resonance->equipment_match == RS3_EQUIPMENT_ALLOWED);

    /* Start just before the 32-bit wrap and sample before the post-wrap end. */
    uint64_t flurry_start = FIXTURE_EXTRA_VARIABLES + LAYOUT_VARIABLE_NODE_VALUE_OFFSET;
    uint64_t flurry_end =
        FIXTURE_EXTRA_VARIABLES + FIXTURE_VARIABLE_NODE_BYTES + LAYOUT_VARIABLE_NODE_VALUE_OFFSET;
    uint64_t logic_clock = FIXTURE_WORLD + LAYOUT_LOGIC_CLOCK_OFFSET;
    fixture_put_u32(test_fixture, flurry_start, 0xfffffff0u);
    fixture_put_u32(test_fixture, flurry_end, 0x50);
    fixture_put_u32(test_fixture, logic_clock, 0x20);
    assert(fixture_decode(test_fixture));
    assert(flurry->remaining_ms == 960 && flurry->duration_ms == 1920);
    fixture_put_u32(test_fixture, logic_clock, 3000);
    assert(fixture_decode(test_fixture));
    assert(flurry->remaining_ms == 0);

    /* An absent sparse cooldown stays unknown; it is never fabricated as ready. */
    fixture_put_u64(test_fixture,
                    FIXTURE_CLIENT_TABLE + (FIXTURE_FLURRY_START_VAR % FIXTURE_BUCKET_COUNT) * 8,
                    0);
    assert(fixture_decode(test_fixture));
    assert(!(flurry->flags & RS3_ABILITY_TIMER_VALID) && flurry->reason == RS3_NOT_SET);
    fixture_destroy(test_fixture);
}

static void torn_slots_and_duplicates_do_not_invent_identities(void)
{
    fixture *test_fixture = fixture_create_inventory();
    assert(fixture_decode(test_fixture));
    uint64_t sequence = tracker_of(test_fixture)->event_sequence;

    /* A torn slot vector makes the containers unavailable without events. */
    fixture_mutate_on_read(test_fixture, FIXTURE_BACKPACK_SLOTS, 2, FIXTURE_BACKPACK_SLOTS,
                           FIXTURE_ITEM_BREW_5);
    assert(fixture_decode(test_fixture));
    assert(test_fixture->snapshot->inventory_info.status == RS3_UNAVAILABLE);
    assert(test_fixture->snapshot->inventory_info.backpack_count == 0);
    assert(tracker_of(test_fixture)->event_sequence == sequence);
    fixture_clear_mutation(test_fixture);
    assert(fixture_decode(test_fixture));
    assert(tracker_of(test_fixture)->event_sequence == sequence + 1);

    /* Two identical copies moved simultaneously cannot be identified uniquely. */
    set_backpack(test_fixture, 0, -1, 0);
    set_backpack(test_fixture, 2, FIXTURE_ITEM_OTHER, 1);
    assert(fixture_decode(test_fixture));
    set_backpack(test_fixture, 1, -1, 0);
    set_backpack(test_fixture, 2, -1, 0);
    set_backpack(test_fixture, 3, FIXTURE_ITEM_OTHER, 1);
    set_backpack(test_fixture, 4, FIXTURE_ITEM_OTHER, 1);
    assert(fixture_decode(test_fixture));
    assert(test_fixture->snapshot->items[3].flags & RS3_ITEM_AMBIGUOUS_IDENTITY);
    assert(test_fixture->snapshot->items[4].flags & RS3_ITEM_AMBIGUOUS_IDENTITY);

    /* A world replacement establishes a new baseline without false additions. */
    sequence = tracker_of(test_fixture)->event_sequence;
    inventory_commit(test_fixture->context, test_fixture->snapshot, FIXTURE_WORLD + 4096);
    assert(tracker_of(test_fixture)->event_sequence == sequence);

    uint32_t written;
    uint64_t next;
    uint64_t lost;
    rs3_item_event event;
    assert(rs3_item_events_since(test_fixture->context, 0, &event, 1, &written, &next, &lost) ==
           RS3_OK);
    assert(written == 1 && next == 1 && lost == 0);
    assert(rs3_item_events_since(test_fixture->context, 0, &event, 1, &written, &next, &lost) ==
           RS3_OK);
    assert(written == 1);
    fixture_destroy(test_fixture);
}

/** Writes the raw SHA-256 bytes of a hexadecimal digest. */
static void write_digest(unsigned char *destination, const char *hexadecimal)
{
    for (unsigned index = 0; index < CATALOG_SHA256_BYTES; index++)
    {
        unsigned byte_value;
        assert(sscanf(&hexadecimal[index * 2], "%2x", &byte_value) == 1);
        destination[index] = (unsigned char)byte_value;
    }
}

/** Writes size bytes to a new temporary file and returns its path in path. */
static void write_temporary_file(const unsigned char *data, size_t size, wchar_t path[MAX_PATH])
{
    wchar_t folder[MAX_PATH];
    assert(GetTempPathW(MAX_PATH, folder));
    assert(GetTempFileNameW(folder, L"rs3", 0, path));
    HANDLE file =
        CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, NULL);
    assert(file != INVALID_HANDLE_VALUE);
    DWORD bytes_written;
    assert(WriteFile(file, data, (DWORD)size, &bytes_written, NULL) && bytes_written == size);
    CloseHandle(file);
}

static void validates_catalogs_and_reports_item_queue_gaps(void)
{
    fixture *test_fixture = fixture_create_inventory();
    item_catalog *catalog = &test_fixture->context->inventory->catalog;
    assert(fixture_decode(test_fixture));

    /*
     * A valid SHA-256 is integrity checking, not authentication. Every record
     * is validated too: a negative timer ID with a client source must never
     * index the timer arrays.
     */
    unsigned char *data = malloc(catalog->size);
    assert(data != NULL);
    memcpy(data, catalog->data, catalog->size);
    memcpy(data, CATALOG_MAGIC, CATALOG_MAGIC_BYTES);
    encode_u32(data + CATALOG_HEADER_FORMAT, CATALOG_FORMAT_VERSION);
    encode_u32(data + CATALOG_HEADER_ITEM_COUNT, catalog->item_count);
    encode_u32(data + CATALOG_HEADER_ABILITY_COUNT, catalog->ability_count);
    encode_u32(data + CATALOG_HEADER_STRINGS_SIZE, catalog->strings_size);
    write_digest(data + CATALOG_HEADER_CLIENT_SHA256, PROFILE_PRIMARY_SHA256);
    unsigned char *first_ability =
        data + CATALOG_HEADER_BYTES + catalog->item_count * CATALOG_ITEM_RECORD_BYTES;
    encode_u32(first_ability + CATALOG_ABILITY_START_VAR, UINT32_MAX);
    assert(catalog_sha256(data + CATALOG_HEADER_BYTES,
                          (uint32_t)(catalog->size - CATALOG_HEADER_BYTES),
                          data + CATALOG_HEADER_BODY_SHA256));

    wchar_t path[MAX_PATH];
    write_temporary_file(data, catalog->size, path);
    assert(catalog_load_file(catalog, path) == RS3_E_ABI);
    assert(catalog->ability_count == 2 && catalog->status == RS3_AVAILABLE);
    assert(DeleteFileW(path));
    free(data);

    /* Overflowing the item queue reports the overwritten sequences. */
    AcquireSRWLockExclusive(&test_fixture->context->event_queue_lock);
    for (unsigned index = 0; index < ITEM_EVENT_CAPACITY + 3; index++)
    {
        inventory_publish_event(test_fixture->context, test_fixture->snapshot, RS3_ITEM_MOVED,
                                test_fixture->snapshot->items, test_fixture->snapshot->items);
    }
    ReleaseSRWLockExclusive(&test_fixture->context->event_queue_lock);
    uint32_t written;
    uint64_t next;
    uint64_t lost;
    rs3_item_event event;
    assert(rs3_item_events_since(test_fixture->context, 0, &event, 1, &written, &next, &lost) ==
           RS3_OK);
    assert(written == 1 && lost == 3 && next == 4 && event.sequence == 4);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(tracks_moves_doses_replacements_and_quantities);
    RUN_TEST(sparse_slot_vectors_report_empty_slots);
    RUN_TEST(evaluates_cooldowns_and_weapon_requirements);
    RUN_TEST(torn_slots_and_duplicates_do_not_invent_identities);
    RUN_TEST(validates_catalogs_and_reports_item_queue_gaps);
    puts("PASS: inventory tests");
    return 0;
}
