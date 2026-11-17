/* Native action-bar decoding, domain consistency and explicit unknown UI state. */
#include "fixture.h"

#include <string.h>

#include "interface.h"

#define INTERFACE_NODES       0xb00000ull
#define INTERFACE_NODE_COUNT  40u
#define PRESET_NODE           INTERFACE_NODES
#define PACKED_NODES          (INTERFACE_NODES + FIXTURE_VARIABLE_NODE_BYTES)
#define OBJECT_NODES          (PACKED_NODES + RS3_ACTION_BAR_SLOTS * FIXTURE_VARIABLE_NODE_BYTES)
#define KEY_NODE              (OBJECT_NODES + RS3_ACTION_BAR_SLOTS * FIXTURE_VARIABLE_NODE_BYTES)
#define MODIFIER_NODE         (KEY_NODE + FIXTURE_VARIABLE_NODE_BYTES)
#define FIRST_ADDITIONAL_SLOT RS3_ACTION_BAR_SLOTS

#define UI_MANAGER                 0xb10000ull
#define UI_CACHE                   0xb20000ull
#define UI_RECORDS                 0xb30000ull
#define UI_NODE                    0xb40000ull
#define UI_VALUE                   0xb40100ull
#define UI_BUCKETS                 0xb50000ull
#define UI_CACHE_RECORD_COUNT      1478u
#define UI_BANK_KEY                ((1477u << 16) | 706u)
#define UI_ENRAGE_KEY              ((1477u << 16) | 748u)
#define UI_COMPONENTS              0xb60000ull
#define UI_HEADING                 0xb70000ull
#define UI_TEXT_HEAP               0xb80000ull
#define POINTER_MANAGER            0xb90000ull
#define POINTER_RECORD             0xba0000ull
#define POINTER_TEXT_HEAP          0xbb0000ull
#define POINTER_ROOT_OFFSET        0x19938u
#define POINTER_SLOT_OFFSET        0x1400u
#define POINTER_MENU_MODE_OFFSET   0x1428u
#define POINTER_MODERN_MENU_OFFSET 0x17b0u
#define POINTER_LEGACY_MENU_OFFSET 0x68u
#define POINTER_MANAGER_BYTES      0x1800u
#define POINTER_NAME_BYTES         24u
#define POINTER_TAG_OFFSET         23u
#define POINTER_VERB_OFFSET        0x18u
#define POINTER_TYPE_OFFSET        0x38u
#define POINTER_REF_OFFSET         0x48u
#define POINTER_DESCRIPTOR         0x1721ce0u
#define POINTER_OWNER_OFFSET       8u
#define POINTER_RECORD_BYTES       0x60u
#define POINTER_ITEM_OFFSET        0x44u
#define POINTER_MANAGER_GUARD_RVA  0x320c2fu
#define POINTER_RECORD_GUARD_RVA   0x391305u
#define POINTER_HEAP_FLAG          0x80u
#define POINTER_MODULE_SIZE        0x2000000u

#define MINIMAP_COMPONENT_DATA 0xbc0000ull
#define MINIMAP_SPRITE_DATA    0xbd0000ull
#define MINIMAP_MANAGER_DATA   0xbe0000ull
#define MINIMAP_GUARD_BASE     0x320c00u
#define MINIMAP_GUARD_BYTES    0x500u

static fixture *prayer_bar_fixture(void);

/** Add independently pinned instruction fixtures and an attached bank root. */
static fixture *bank_fixture(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    static const unsigned char thunk[] = {0x48, 0x83, 0xc1, 0x28, 0xe9, 0xc7, 0x90, 0x8a, 0};
    static const unsigned char getter[] = {0x48, 0x83, 0xec, 0x28, 0x4c, 0x8b, 0x41, 0x18,
                                           0x48, 0x8b, 0x41, 0x20, 0x49, 0x2b, 0xc0, 0x48,
                                           0xc1, 0xf8, 0x04, 0x3b, 0xd0, 0x7d, 0x11, 0x48,
                                           0x63, 0xc2, 0x48, 0xc1, 0xe0, 0x04, 0x49, 0x03};
    static const unsigned char buckets[] = {0x41, 0x8b, 0x8e, 0x10, 0x01, 0, 0,
                                            0x4d, 0x8b, 0x8e, 0x08, 0x01, 0, 0};
    static const unsigned char child[] = {0x8b, 0x50, 0x0c};
    const unsigned char *code[] = {thunk, getter, buckets, child};
    const uint32_t addresses[] = {0x3c7f90, 0xc71060, 0x3ca949, 0x3ca9a7};
    const size_t sizes[] = {sizeof thunk, sizeof getter, sizeof buckets, sizeof child};
    for (unsigned index = 0; index < 4; index++)
    {
        uint64_t address = FIXTURE_MODULE_BASE + addresses[index];
        fixture_add_block(test_fixture, address, sizes[index]);
        memcpy(fixture_bytes(test_fixture, address, sizes[index]), code[index], sizes[index]);
    }
    fixture_add_block(test_fixture, UI_MANAGER, 0x160);
    fixture_add_block(test_fixture, UI_CACHE, UI_CACHE_RECORD_COUNT * 16);
    fixture_add_block(test_fixture, UI_RECORDS, 0x300);
    fixture_add_block(test_fixture, UI_NODE, 0x100);
    fixture_add_block(test_fixture, UI_VALUE, 0x100);
    fixture_add_block(test_fixture, UI_BUCKETS, 4 * 8);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + 0x19910, UI_MANAGER);
    fixture_put_u64(test_fixture, UI_MANAGER, FIXTURE_MODULE_BASE + 0x15382c8);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x10, FIXTURE_MODULE_BASE + 0x15382e8);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x50, UI_CACHE);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x58, UI_CACHE + UI_CACHE_RECORD_COUNT * 16);
    fixture_put_u64(test_fixture, UI_CACHE + 1477 * 16 + 8, UI_RECORDS);
    fixture_put_u32(test_fixture, UI_RECORDS, 1477);
    fixture_put_u64(test_fixture, UI_CACHE + 517 * 16 + 8, UI_RECORDS + 0x100);
    fixture_put_u32(test_fixture, UI_RECORDS + 0x100, 517);
    fixture_put_u32(test_fixture, UI_MANAGER + 0xf8, 1477);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x118, UI_BUCKETS);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x120, 3);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 1);
    fixture_put_u64(test_fixture, UI_BUCKETS + (UI_BANK_KEY % 3) * 8, UI_NODE);
    fixture_put_u64(test_fixture, UI_BUCKETS + 3 * 8, UINT64_MAX);
    fixture_put_u32(test_fixture, UI_NODE, UI_BANK_KEY);
    fixture_put_u64(test_fixture, UI_NODE + 16, UI_VALUE);
    fixture_put_u32(test_fixture, UI_VALUE + 12, 517);
    return test_fixture;
}

/** Write an observed small-string representation without a game call. */
static void set_heading(fixture *test_fixture, const char *text)
{
    size_t size = strlen(text);
    assert(size <= 23);
    unsigned char *field = fixture_bytes(test_fixture, UI_HEADING + 0x190, 24);
    memset(field, 0, 24);
    memcpy(field, text, size);
    field[23] = (unsigned char)(23 - size);
}

static fixture *enrage_fixture(void)
{
    fixture *test_fixture = bank_fixture();
    fixture_add_block(test_fixture, UI_COMPONENTS, 46 * 24);
    fixture_add_block(test_fixture, UI_HEADING, 0x1a8);
    fixture_add_block(test_fixture, UI_TEXT_HEAP, 65);
    fixture_put_u64(test_fixture, UI_CACHE + 720 * 16 + 8, UI_RECORDS + 0x200);
    fixture_put_u32(test_fixture, UI_RECORDS + 0x200, 720);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x208, UI_COMPONENTS);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x210, UI_COMPONENTS + 46 * 24);
    fixture_put_u64(test_fixture, UI_COMPONENTS + 3 * 24 + 16, UI_HEADING);
    fixture_put_u64(test_fixture, UI_HEADING, FIXTURE_MODULE_BASE + 0x1530678);
    fixture_put_u32(test_fixture, UI_HEADING + 0x18, 3u << 16 | 720u);
    set_heading(test_fixture, "Choose initial enrage.");
    assert(UI_BANK_KEY % 3 == UI_ENRAGE_KEY % 3);
    fixture_put_u64(test_fixture, UI_NODE + 24, UI_NODE + 0x80);
    fixture_put_u32(test_fixture, UI_NODE + 0x80, UI_ENRAGE_KEY);
    fixture_put_u64(test_fixture, UI_NODE + 0x80 + 16, UI_VALUE + 0x80);
    fixture_put_u32(test_fixture, UI_VALUE + 0x80 + 12, 720);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 2);
    return test_fixture;
}

static fixture *prayer_bar_fixture(void)
{
    fixture *test_fixture = fixture_create_inventory();
    fixture_add_block(test_fixture, INTERFACE_NODES,
                      INTERFACE_NODE_COUNT * FIXTURE_VARIABLE_NODE_BYTES);
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, PRESET_NODE, 10160, 6u << 5);
    static const uint32_t prayer_ids[4] = {38, 39, 49, 40};
    for (unsigned index = 0; index < RS3_ACTION_BAR_SLOTS; index++)
    {
        uint32_t value = index < 4 ? (7u << 17) | (prayer_ids[index] << 4) : 0;
        fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE,
                             PACKED_NODES + index * FIXTURE_VARIABLE_NODE_BYTES, 5265 + index,
                             value);
        fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE,
                             OBJECT_NODES + index * FIXTURE_VARIABLE_NODE_BYTES, 5335 + index,
                             UINT32_MAX);
    }
    fixture_put_variable(test_fixture, FIXTURE_CLIENT_TABLE, KEY_NODE, 4997, 0x04030201);
    fixture_put_variable(test_fixture, FIXTURE_CLIENT_TABLE, MODIFIER_NODE, 5011, 0x000000aa);
    return test_fixture;
}

static void four_curse_bindings(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    assert(fixture_decode(test_fixture));
    const rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(snapshot->interface_info.action_bar_count == RS3_MAX_ACTION_BAR_SLOTS);
    static const uint32_t prayer_structs[4] = {14594, 14595, 14607, 14596};
    for (unsigned index = 0; index < 4; index++)
    {
        const rs3_action_bar_slot *slot = &snapshot->action_bar[FIRST_ADDITIONAL_SLOT + index];
        assert(slot->display_bar == 2 && slot->preset_bar == 6 && slot->slot == index + 1);
        assert(slot->flags & RS3_BAR_PRAYER_KNOWN);
        assert(slot->flags & RS3_BAR_CHORD_KNOWN);
        assert(slot->ability_struct_id == (int32_t)prayer_structs[index]);
        assert(slot->key_code == index + 1 && slot->key_modifiers == 2);
        assert(slot->key_var == 4997 && slot->modifier_var == 5011);
    }
    /* Missing modifier entries on other rows never become explicit no-modifier chords. */
    assert(!(snapshot->action_bar[0].flags & RS3_BAR_MODIFIER_KNOWN));
    assert(!(snapshot->action_bar[0].flags & RS3_BAR_CHORD_KNOWN));
    fixture_destroy(test_fixture);
}

static void items_moves_and_preset_switches(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    fixture_put_u32(test_fixture, PACKED_NODES + 8, 10u << 17);
    fixture_put_u32(test_fixture, OBJECT_NODES + 8, FIXTURE_ITEM_BREW_6);
    assert(fixture_decode(test_fixture));
    rs3_action_bar_slot *slot = &test_fixture->snapshot->action_bar[FIRST_ADDITIONAL_SLOT];
    assert(slot->flags & RS3_BAR_ITEM_KNOWN);
    assert(slot->item_id == FIXTURE_ITEM_BREW_6);
    assert(strcmp(slot->name, "Test brew (6)") == 0);
    /* Content changes in the same display slot keep that row's binding. */
    fixture_put_u32(test_fixture, OBJECT_NODES + 8, FIXTURE_ITEM_BREW_5);
    assert(fixture_decode(test_fixture));
    assert(slot->item_id == FIXTURE_ITEM_BREW_5 && slot->key_code == 1);
    fixture_put_u32(test_fixture, PRESET_NODE + 8, 31u << 5);
    assert(fixture_decode(test_fixture));
    assert(!(slot->flags & RS3_BAR_PRESET_KNOWN));
    assert(!(slot->flags & RS3_BAR_ITEM_KNOWN));
    assert(slot->item_id == -1 && slot->name[0] == 0);
    fixture_destroy(test_fixture);
}

static void changing_domains_clear_only_their_own_validity(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    fixture_mutate_on_read(test_fixture, PACKED_NODES, 2, PACKED_NODES + 8, 0);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    rs3_action_bar_slot *slot = &test_fixture->snapshot->action_bar[FIRST_ADDITIONAL_SLOT];
    assert(!(slot->flags & RS3_BAR_PRAYER_KNOWN));
    assert(slot->flags & RS3_BAR_CHORD_KNOWN);
    assert(slot->reason == RS3_UI_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    fixture_mutate_on_read(test_fixture, KEY_NODE, 2, KEY_NODE + 8, 0x04030205);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(!(slot->flags & RS3_BAR_CHORD_KNOWN));
    assert(slot->binding_reason == RS3_UI_READ_FAILED);
    fixture_destroy(test_fixture);
}

static void unsupported_codes_remain_raw(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    fixture_put_u32(test_fixture, KEY_NODE + 8, 0x040302ff);
    assert(fixture_decode(test_fixture));
    const rs3_action_bar_slot *slot = &test_fixture->snapshot->action_bar[FIRST_ADDITIONAL_SLOT];
    assert(slot->flags & RS3_BAR_KEY_KNOWN);
    assert(slot->key_code == 255);
    assert(!(slot->flags & RS3_BAR_CHORD_KNOWN));
    assert(slot->binding_reason == RS3_UI_KEY_UNMAPPED);
    fixture_destroy(test_fixture);
}

static void oversized_packed_values_are_rejected(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    fixture_put_u32(test_fixture, KEY_NODE + 32, 1); /* Numeric int64 node tag. */
    fixture_put_u64(test_fixture, KEY_NODE + 8, 0x100000001ull);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    const rs3_action_bar_slot *slot = &test_fixture->snapshot->action_bar[FIRST_ADDITIONAL_SLOT];
    assert(!(slot->flags & RS3_BAR_KEY_KNOWN));
    assert(slot->binding_reason == RS3_UI_READ_FAILED);
    assert(slot->flags & RS3_BAR_PRAYER_KNOWN);
    fixture_destroy(test_fixture);
}

static void ui_unknown_and_frame_reset(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    assert(fixture_decode(test_fixture));
    const rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(snapshot->interface_info.interface_count == RS3_REQUIRED_INTERFACES);
    assert(snapshot->interface_info.open_complete == 0);
    for (unsigned index = 0; index < RS3_REQUIRED_INTERFACES; index++)
    {
        assert(snapshot->interfaces[index].open == -1);
        assert(snapshot->interfaces[index].interface_id == -1);
        assert(snapshot->interfaces[index].reason ==
               (index == RS3_UI_BANK_PRESET - 1 || index == RS3_UI_ENRAGE - 1
                    ? RS3_UI_UNSUPPORTED_PROFILE
                    : RS3_UI_UNMAPPED));
    }
    interface_clear(test_fixture->snapshot);
    assert(snapshot->interface_info.action_bar_count == 0);
    assert(snapshot->interface_info.status == RS3_UNAVAILABLE);
    assert(snapshot->action_bar[FIRST_ADDITIONAL_SLOT].flags == 0);
    fixture_destroy(test_fixture);
    /* Compatibility Mode decodes action bars from the same server variables, but leaves the
     * primary-only UI roots unmapped. */
    test_fixture = prayer_bar_fixture();
    test_fixture->context->target.profile = &profile_compatibility;
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    snapshot = test_fixture->snapshot;
    assert(snapshot->interface_info.status == RS3_PARTIAL);
    assert(snapshot->interface_info.action_bar_count == RS3_MAX_ACTION_BAR_SLOTS);
    for (unsigned index = 0; index < RS3_REQUIRED_INTERFACES; index++)
    {
        assert(snapshot->interfaces[index].reason == RS3_UI_UNMAPPED);
        assert(snapshot->interfaces[index].interface_id == -1);
    }
    int prayer_slots = 0;
    for (unsigned index = 0; index < snapshot->interface_info.action_bar_count; index++)
    {
        if (snapshot->action_bar[index].flags & RS3_BAR_PACKED_KNOWN &&
            snapshot->action_bar[index].content_type == 7)
        {
            prayer_slots++;
        }
    }
    assert(prayer_slots == 4);
    fixture_destroy(test_fixture);
}

static void bank_attachment_is_distinct_from_cached_presence(void)
{
    fixture *test_fixture = bank_fixture();
    assert(fixture_decode(test_fixture));
    const rs3_interface_observation *bank =
        &test_fixture->snapshot->interfaces[RS3_UI_BANK_PRESET - 1];
    assert(bank->interface_id == 517 && bank->open == 1 && bank->reason == RS3_UI_CONFIRMED);
    assert(test_fixture->snapshot->interface_info.open_complete == 0);
    /* Keep the bank loaded in the cache, but remove its actual attachment. */
    fixture_put_u64(test_fixture, UI_BUCKETS + (UI_BANK_KEY % 3) * 8, 0);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 0);
    assert(fixture_decode(test_fixture));
    assert(bank->interface_id == 517 && bank->open == 0 && bank->reason == RS3_UI_CONFIRMED);
    fixture_destroy(test_fixture);
}

static void bank_rejects_torn_cycles_and_reused_manager(void)
{
    fixture *test_fixture = bank_fixture();
    fixture_mutate_on_read(test_fixture, UI_VALUE, 2, UI_VALUE + 12, 518);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    const rs3_interface_observation *bank =
        &test_fixture->snapshot->interfaces[RS3_UI_BANK_PRESET - 1];
    assert(bank->open == -1 && bank->reason == RS3_UI_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    fixture_put_u32(test_fixture, UI_VALUE + 12, 517);
    fixture_put_u64(test_fixture, UI_NODE + 24, UI_NODE);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(bank->open == -1 && bank->reason == RS3_UI_READ_FAILED);
    fixture_put_u64(test_fixture, UI_NODE + 24, 0);
    fixture_mutate_on_read(test_fixture, FIXTURE_WORLD + 0x19910, 2, FIXTURE_WORLD + 0x19910, 0);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(bank->open == -1 && bank->reason == RS3_UI_READ_FAILED);
    fixture_destroy(test_fixture);
}

static void bank_requires_guards_root_and_bounded_complete_graph(void)
{
    fixture *test_fixture = bank_fixture();
    const rs3_interface_observation *bank =
        &test_fixture->snapshot->interfaces[RS3_UI_BANK_PRESET - 1];
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 513);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(bank->open == -1 && bank->reason == RS3_UI_READ_FAILED);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 2); /* Count mismatch, not absence. */
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(bank->open == -1 && bank->reason == RS3_UI_READ_FAILED);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 1);
    fixture_put_u32(test_fixture, UI_MANAGER + 0xf8, 999);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(bank->open == -1 && bank->reason == RS3_UI_UNMAPPED);
    fixture_put_u32(test_fixture, UI_MANAGER + 0xf8, 1477);
    fixture_put_u32(test_fixture, FIXTURE_MODULE_BASE + 0x3c7f90, 0);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(bank->open == -1 && bank->reason == RS3_UI_UNSUPPORTED_PROFILE);
    fixture_destroy(test_fixture);
}

static void enrage_requires_heading_not_just_generic_dialog_id(void)
{
    fixture *test_fixture = enrage_fixture();
    assert(fixture_decode(test_fixture));
    const rs3_interface_observation *enrage =
        &test_fixture->snapshot->interfaces[RS3_UI_ENRAGE - 1];
    assert(enrage->open == 1 && enrage->interface_id == 720 && enrage->reason == RS3_UI_CONFIRMED);
    set_heading(test_fixture, "Another choice menu");
    assert(fixture_decode(test_fixture));
    assert(enrage->open == -1 && enrage->reason == RS3_UI_UNMAPPED);
    /* A cached dialog does not remain open after its attachment is removed. */
    set_heading(test_fixture, "Choose initial enrage.");
    fixture_put_u64(test_fixture, UI_NODE + 24, 0);
    fixture_put_u64(test_fixture, UI_MANAGER + 0x128, 1);
    assert(fixture_decode(test_fixture));
    assert(enrage->open == 0 && enrage->reason == RS3_UI_CONFIRMED);
    fixture_destroy(test_fixture);
}

static void enrage_rejects_torn_and_oversized_heading(void)
{
    fixture *test_fixture = enrage_fixture();
    fixture_mutate_on_read(test_fixture, UI_HEADING + 0x190, 2, UI_HEADING + 0x190, 0);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    const rs3_interface_observation *enrage =
        &test_fixture->snapshot->interfaces[RS3_UI_ENRAGE - 1];
    assert(enrage->open == -1 && enrage->reason == RS3_UI_READ_FAILED);
    fixture_clear_mutation(test_fixture);
    const char *heading = "Choose initial enrage.";
    memcpy(fixture_bytes(test_fixture, UI_TEXT_HEAP, strlen(heading)), heading, strlen(heading));
    fixture_put_u64(test_fixture, UI_HEADING + 0x190, UI_TEXT_HEAP);
    fixture_put_u64(test_fixture, UI_HEADING + 0x198, strlen(heading));
    fixture_put_u64(test_fixture, UI_HEADING + 0x1a0, 0x8000000000000040ull);
    assert(fixture_decode(test_fixture));
    assert(enrage->open == 1 && enrage->reason == RS3_UI_CONFIRMED);
    fixture_put_u64(test_fixture, UI_HEADING + 0x198, 65);
    assert(fixture_decode(test_fixture));
    assert(enrage->open == -1 && enrage->reason == RS3_UI_READ_FAILED);
    fixture_destroy(test_fixture);
}

static void ui_rejects_duplicate_keys_and_changing_vtable(void)
{
    fixture *test_fixture = enrage_fixture();
    fixture_put_u32(test_fixture, UI_NODE + 0x80, UI_BANK_KEY);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(test_fixture->snapshot->interfaces[RS3_UI_BANK_PRESET - 1].open == -1);
    fixture_put_u32(test_fixture, UI_NODE + 0x80, UI_ENRAGE_KEY);
    fixture_mutate_on_read(test_fixture, UI_MANAGER, 2, UI_MANAGER, 0);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(test_fixture->snapshot->interfaces[RS3_UI_BANK_PRESET - 1].open == -1);
    assert(test_fixture->snapshot->interfaces[RS3_UI_ENRAGE - 1].open == -1);
    fixture_destroy(test_fixture);
}

static void caller_owned_copy_contract(void)
{
    fixture *test_fixture = prayer_bar_fixture();
    assert(fixture_decode(test_fixture));
    rs3_action_bar_slot output[RS3_MAX_ACTION_BAR_SLOTS];
    memset(output, 0x5a, sizeof output);
    uint32_t required = 0;
    assert(rs3_snapshot_action_bar(test_fixture->snapshot, output, 1, &required) == RS3_E_BUFFER);
    assert(required == RS3_MAX_ACTION_BAR_SLOTS && output[0].flags == 0x5a5a5a5a);
    assert(rs3_snapshot_action_bar(test_fixture->snapshot, output, RS3_MAX_ACTION_BAR_SLOTS,
                                   &required) == RS3_OK);
    output[FIRST_ADDITIONAL_SLOT].key_code = 255;
    assert(test_fixture->snapshot->action_bar[FIRST_ADDITIONAL_SLOT].key_code == 1);
    rs3_interface_info info;
    assert(rs3_snapshot_interface_info(test_fixture->snapshot, &info, sizeof info - 1) ==
           RS3_E_ABI);
    assert(rs3_interface_sizeof(1) == sizeof info);
    assert(rs3_interface_sizeof(2) == sizeof(rs3_interface_observation));
    assert(rs3_interface_sizeof(3) == sizeof output[0]);
    assert(rs3_interface_sizeof(0) == 0);
    fixture_destroy(test_fixture);
}

/** This fixture validates raw byte decoding, never an entity hit-test or input permission. */
static fixture *pointer_fixture(void)
{
    fixture *test_fixture = fixture_create();
    test_fixture->context->target.module_size = POINTER_MODULE_SIZE;
    fixture_add_block(test_fixture, POINTER_MANAGER, POINTER_MANAGER_BYTES);
    fixture_add_block(test_fixture, POINTER_RECORD, POINTER_RECORD_BYTES);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + POINTER_ROOT_OFFSET, POINTER_MANAGER);
    fixture_put_u64(test_fixture, POINTER_MANAGER + POINTER_OWNER_OFFSET, FIXTURE_WORLD);
    fixture_put_u64(test_fixture, POINTER_MANAGER + POINTER_SLOT_OFFSET, POINTER_RECORD);
    fixture_put_u64(test_fixture, POINTER_RECORD + POINTER_TYPE_OFFSET,
                    FIXTURE_MODULE_BASE + POINTER_DESCRIPTOR);
    fixture_put_u32(test_fixture, POINTER_RECORD + POINTER_ITEM_OFFSET, UINT32_MAX);
    fixture_put_u32(test_fixture, POINTER_RECORD + POINTER_REF_OFFSET, FIXTURE_NPC_RUNTIME_ID);
    const char *name = "Training dummy";
    const char *verb = "Attack";
    memcpy(fixture_bytes(test_fixture, POINTER_RECORD, strlen(name)), name, strlen(name));
    memcpy(fixture_bytes(test_fixture, POINTER_RECORD + POINTER_VERB_OFFSET, strlen(verb)), verb,
           strlen(verb));
    fixture_bytes(test_fixture, POINTER_RECORD + POINTER_TAG_OFFSET, 1)[0] =
        (unsigned char)(POINTER_TAG_OFFSET - strlen(name));
    fixture_bytes(test_fixture, POINTER_RECORD + POINTER_VERB_OFFSET + POINTER_TAG_OFFSET, 1)[0] =
        (unsigned char)(POINTER_TAG_OFFSET - strlen(verb));
    const unsigned char manager_guard[] = {0x48, 0x8b, 0x81, 0x38, 0x99, 1, 0};
    const unsigned char record_guard[] = {0x48, 0x8b, 0x86, 0, 0x14, 0, 0};
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + POINTER_MANAGER_GUARD_RVA,
                      sizeof manager_guard);
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + POINTER_RECORD_GUARD_RVA,
                      sizeof record_guard);
    memcpy(fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + POINTER_MANAGER_GUARD_RVA,
                         sizeof manager_guard),
           manager_guard, sizeof manager_guard);
    memcpy(fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + POINTER_RECORD_GUARD_RVA,
                         sizeof record_guard),
           record_guard, sizeof record_guard);
    const uint32_t backend_addresses[] = {0x391d3f, 0x391d52, 0x392689};
    const unsigned char backend_code[][7] = {
        {0x80, 0xb9, 0x28, 0x14, 0x00, 0x00, 0x00},
        {0xc6, 0x81, 0xb0, 0x17, 0x00, 0x00, 0x01},
        {0x44, 0x88, 0xb3, 0xb0, 0x17, 0x00, 0x00},
    };
    for (unsigned index = 0; index < sizeof backend_addresses / sizeof *backend_addresses; index++)
    {
        uint64_t address = FIXTURE_MODULE_BASE + backend_addresses[index];
        fixture_add_block(test_fixture, address, sizeof backend_code[index]);
        memcpy(fixture_bytes(test_fixture, address, sizeof backend_code[index]),
               backend_code[index], sizeof backend_code[index]);
    }
    return test_fixture;
}

static void pointer_selects_modern_or_legacy_menu_without_changing_abi(void)
{
    fixture *test_fixture = pointer_fixture();
    rs3_pointer_candidate candidate;
    fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_MENU_MODE_OFFSET, 1);
    fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_LEGACY_MENU_OFFSET, 1);
    for (unsigned state = 0; state <= 1; state++)
    {
        fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_MODERN_MENU_OFFSET, state);
        interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
        assert(candidate.status == RS3_PARTIAL && candidate.reason == RS3_UI_UNVALIDATED_SOURCE);
        assert(candidate.flags & RS3_POINTER_MODERN_MENU);
        assert(candidate.raw_menu_state == (int32_t)state);
        assert(candidate.reserved == 0 && sizeof candidate == 304);
    }
    fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_MENU_MODE_OFFSET, 0);
    fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_MODERN_MENU_OFFSET, 0);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(!(candidate.flags & RS3_POINTER_MODERN_MENU) && candidate.raw_menu_state == 1);
    fixture_destroy(test_fixture);
}

static void pointer_rejects_changing_or_invalid_menu_backend_and_state(void)
{
    const uint32_t fields[] = {POINTER_MENU_MODE_OFFSET, POINTER_MODERN_MENU_OFFSET};
    for (unsigned index = 0; index < sizeof fields / sizeof *fields; index++)
    {
        fixture *test_fixture = pointer_fixture();
        rs3_pointer_candidate candidate;
        fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_MENU_MODE_OFFSET, 1);
        fixture_mutate_on_read(test_fixture, POINTER_MANAGER + fields[index], 2,
                               POINTER_MANAGER + fields[index], index == 0 ? 0 : 1);
        interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
        assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
        fixture_clear_mutation(test_fixture);
        fixture_put_u32(test_fixture, POINTER_MANAGER + POINTER_MENU_MODE_OFFSET, 1);
        fixture_put_u32(test_fixture, POINTER_MANAGER + fields[index], 2);
        interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
        assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
        fixture_destroy(test_fixture);
    }
}

static void pointer_backend_instruction_guards_are_required(void)
{
    const uint32_t addresses[] = {0x391d3f, 0x391d52, 0x392689};
    for (unsigned index = 0; index < sizeof addresses / sizeof *addresses; index++)
    {
        fixture *test_fixture = pointer_fixture();
        rs3_pointer_candidate candidate;
        fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + addresses[index], 1)[0] = 0;
        interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
        assert(candidate.reason == RS3_UI_UNSUPPORTED_PROFILE && candidate.flags == 0);
        fixture_destroy(test_fixture);
    }
}

static void pointer_raw_fields_never_become_semantic_hit_tests(void)
{
    fixture *test_fixture = pointer_fixture();
    rs3_pointer_candidate candidate;
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_PARTIAL && candidate.reason == RS3_UI_UNVALIDATED_SOURCE);
    assert(candidate.flags == (RS3_POINTER_RAW_MENU | RS3_POINTER_RAW_RECORD |
                               RS3_POINTER_RAW_TEXT | RS3_POINTER_RAW_TYPE));
    assert(candidate.raw_reference == FIXTURE_NPC_RUNTIME_ID);
    assert(candidate.raw_type_rva == POINTER_DESCRIPTOR);
    assert(strcmp(candidate.name, "Training dummy") == 0);
    assert(strcmp(candidate.verb, "Attack") == 0);
    assert(candidate.observed_ns && candidate.raw_item_id == -1);
    assert(rs3_interface_sizeof(4) == sizeof candidate);
    fixture_destroy(test_fixture);
}

static void pointer_empty_owner_and_profile_states_remain_explicit(void)
{
    fixture *test_fixture = pointer_fixture();
    rs3_pointer_candidate candidate;
    fixture_put_u64(test_fixture, POINTER_MANAGER + POINTER_SLOT_OFFSET, 0);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_PARTIAL && candidate.flags == RS3_POINTER_RAW_MENU);
    assert(candidate.reason == RS3_UI_UNVALIDATED_SOURCE && candidate.raw_reference == -1);
    fixture_put_u64(test_fixture, POINTER_MANAGER + POINTER_OWNER_OFFSET, FIXTURE_WORLD + 8);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    test_fixture->context->target.profile = &profile_compatibility;
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.reason == RS3_UI_UNSUPPORTED_PROFILE && candidate.flags == 0);
    interface_pointer_clear(&candidate);
    assert(candidate.reason == RS3_UI_FRAME_UNAVAILABLE && candidate.observed_ns == 0);
    fixture_destroy(test_fixture);
}

static void pointer_rejects_torn_record_root_and_changed_code(void)
{
    fixture *test_fixture = pointer_fixture();
    rs3_pointer_candidate candidate;
    fixture_mutate_on_read(test_fixture, POINTER_RECORD, 2, POINTER_RECORD + POINTER_REF_OFFSET,
                           FIXTURE_NPC_RUNTIME_ID + 1);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_clear_mutation(test_fixture);
    fixture_mutate_on_read(test_fixture, FIXTURE_WORLD + POINTER_ROOT_OFFSET, 2,
                           FIXTURE_WORLD + POINTER_ROOT_OFFSET,
                           (uint32_t)POINTER_MANAGER + POINTER_OWNER_OFFSET);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_clear_mutation(test_fixture);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + POINTER_ROOT_OFFSET, POINTER_MANAGER);
    fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + POINTER_RECORD_GUARD_RVA, 1)[0] = 0;
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.reason == RS3_UI_UNSUPPORTED_PROFILE && candidate.flags == 0);
    fixture_destroy(test_fixture);
}

static void pointer_heap_text_is_bounded_and_double_read(void)
{
    fixture *test_fixture = pointer_fixture();
    const char *name = "<col=ff0000>Training dummy";
    size_t length = strlen(name);
    fixture_add_block(test_fixture, POINTER_TEXT_HEAP, RS3_POINTER_TEXT_CAPACITY);
    memcpy(fixture_bytes(test_fixture, POINTER_TEXT_HEAP, length), name, length);
    memset(fixture_bytes(test_fixture, POINTER_RECORD, POINTER_NAME_BYTES), 0, POINTER_NAME_BYTES);
    fixture_put_u64(test_fixture, POINTER_RECORD, POINTER_TEXT_HEAP);
    fixture_put_u64(test_fixture, POINTER_RECORD + sizeof(uint64_t), length);
    fixture_bytes(test_fixture, POINTER_RECORD + POINTER_TAG_OFFSET, 1)[0] = POINTER_HEAP_FLAG;
    rs3_pointer_candidate candidate;
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_PARTIAL && strcmp(candidate.name, name) == 0);
    fixture_mutate_on_read(test_fixture, POINTER_TEXT_HEAP, 2, POINTER_TEXT_HEAP, 0x41414141);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_clear_mutation(test_fixture);
    fixture_put_u64(test_fixture, POINTER_RECORD + sizeof(uint64_t), RS3_POINTER_TEXT_CAPACITY);
    interface_pointer_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_destroy(test_fixture);
}

static void pointer_snapshot_wiring_copy_and_unavailable_reset(void)
{
    fixture *test_fixture = pointer_fixture();
    assert(fixture_decode(test_fixture));
    rs3_pointer_candidate candidate;
    rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(rs3_snapshot_pointer_candidate(snapshot, &candidate, sizeof candidate) == RS3_OK);
    assert(candidate.flags & RS3_POINTER_RAW_RECORD);
    assert(candidate.reason == RS3_UI_UNVALIDATED_SOURCE);
    candidate.raw_reference++;
    assert(snapshot->pointer_candidate.raw_reference == FIXTURE_NPC_RUNTIME_ID);
    assert(rs3_snapshot_pointer_candidate(snapshot, &candidate, sizeof candidate - 1) == RS3_E_ABI);
    assert(rs3_snapshot_pointer_candidate(NULL, &candidate, sizeof candidate) == RS3_E_ARGUMENT);
    rs3_snapshot *consumer = NULL;
    assert(rs3_snapshot_create(&consumer) == RS3_OK);
    assert(rs3_snapshot_copy(snapshot, test_fixture->context->latest_frame) == RS3_OK);
    assert(rs3_copy_latest(test_fixture->context, consumer) == RS3_OK);
    assert(consumer->pointer_candidate.raw_reference == FIXTURE_NPC_RUNTIME_ID);
    assert(consumer->pointer_candidate.observed_ns == snapshot->pointer_candidate.observed_ns);
    interface_clear(test_fixture->context->latest_frame);
    assert(rs3_copy_latest(test_fixture->context, consumer) == RS3_OK);
    assert(consumer->pointer_candidate.status == RS3_UNAVAILABLE);
    assert(consumer->pointer_candidate.reason == RS3_UI_FRAME_UNAVAILABLE);
    assert(consumer->pointer_candidate.flags == 0 && consumer->pointer_candidate.name[0] == '\0');
    rs3_snapshot_destroy(consumer);
    fixture_destroy(test_fixture);
}

/** Synthetic cached minimap only: no attachment or physical geometry is asserted. */
static fixture *minimap_fixture(void)
{
    fixture *test_fixture = bank_fixture();
    static const struct
    {
        uint32_t rva;
        uint32_t size;
        unsigned char bytes[8];
    } guards[] = {{0x320c04, 7, {0x48, 0x8b, 0x91, 0x28, 0x02, 0, 0}},
                  {0x320c17, 7, {0x48, 0x8b, 0x89, 0x20, 0x02, 0, 0}},
                  {0x320c1e, 7, {0x48, 0x8b, 0x81, 0x98, 0x98, 0x01, 0}},
                  {0x320c50, 6, {0x8b, 0x8a, 0x78, 0x05, 0, 0}},
                  {0x320c9d, 7, {0x41, 0x8b, 0x83, 0x80, 0, 0, 0}},
                  {0x320cb2, 7, {0x41, 0x8b, 0x83, 0x84, 0, 0, 0}},
                  {0x320d9e, 5, {0xf3, 0x0f, 0x10, 0x48, 0x3c}},
                  {0x320f75, 8, {0xf3, 0x0f, 0x10, 0xa9, 0x48, 0x05, 0, 0}}};
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + MINIMAP_GUARD_BASE, MINIMAP_GUARD_BYTES);
    for (unsigned index = 0; index < sizeof guards / sizeof *guards; index++)
    {
        memcpy(fixture_bytes(test_fixture, FIXTURE_MODULE_BASE + guards[index].rva,
                             guards[index].size),
               guards[index].bytes, guards[index].size);
    }
    fixture_add_block(test_fixture, FIXTURE_MODULE_BASE + 0x1531238, sizeof(uint64_t));
    fixture_put_u64(test_fixture, FIXTURE_MODULE_BASE + 0x1531238, FIXTURE_MODULE_BASE + 0x320c00);
    fixture_add_block(test_fixture, UI_COMPONENTS, 24);
    fixture_add_block(test_fixture, MINIMAP_COMPONENT_DATA, 0x230);
    fixture_add_block(test_fixture, MINIMAP_SPRITE_DATA, 0x580);
    fixture_add_block(test_fixture, MINIMAP_MANAGER_DATA, 0x48);
    fixture_put_u64(test_fixture, UI_CACHE + 1465 * 16 + 8, UI_RECORDS + 0x200);
    fixture_put_u32(test_fixture, UI_RECORDS + 0x200, 1465);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x208, UI_COMPONENTS);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x210, UI_COMPONENTS + 24);
    fixture_put_u64(test_fixture, UI_COMPONENTS + 16, MINIMAP_COMPONENT_DATA);
    fixture_put_u64(test_fixture, MINIMAP_COMPONENT_DATA, FIXTURE_MODULE_BASE + 0x1531208);
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x18, 1465);
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x80, 283);
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x84, 245);
    fixture_put_u64(test_fixture, MINIMAP_COMPONENT_DATA + 0x220, FIXTURE_WORLD);
    fixture_put_u64(test_fixture, MINIMAP_COMPONENT_DATA + 0x228, MINIMAP_SPRITE_DATA);
    fixture_put_u64(test_fixture, MINIMAP_SPRITE_DATA, FIXTURE_MODULE_BASE + 0x15de040);
    float scale = 0.5f, zoom = 1.0f;
    memcpy(fixture_bytes(test_fixture, MINIMAP_SPRITE_DATA + 0x548, sizeof scale), &scale,
           sizeof scale);
    memcpy(fixture_bytes(test_fixture, MINIMAP_MANAGER_DATA + 0x3c, sizeof zoom), &zoom,
           sizeof zoom);
    fixture_put_u64(test_fixture, FIXTURE_WORLD + 0x19898, MINIMAP_MANAGER_DATA);
    return test_fixture;
}

static void minimap_cached_sources_remain_unvalidated(void)
{
    fixture *test_fixture = minimap_fixture();
    rs3_minimap_candidate candidate;
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(sizeof candidate == 64 && rs3_interface_sizeof(5) == sizeof candidate);
    assert(candidate.status == RS3_PARTIAL && candidate.reason == RS3_UI_UNVALIDATED_SOURCE);
    assert(candidate.flags ==
           (RS3_MINIMAP_RAW_COMPONENT | RS3_MINIMAP_RAW_SCALE | RS3_MINIMAP_RAW_ZOOM));
    assert(candidate.interface_id == 1465 && candidate.component_id == 0);
    assert(candidate.local_x == 0 && candidate.local_y == 0);
    assert(candidate.width == 283 && candidate.height == 245);
    assert(candidate.raw_shape == 0 && candidate.raw_mode == 0);
    assert(candidate.raw_zoom == 1.0f && candidate.raw_sprite_scale == 0.5f);
    interface_minimap_clear(&candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.reason == RS3_UI_FRAME_UNAVAILABLE);
    assert(candidate.flags == 0 && candidate.observed_ns == 0 && candidate.width == 0);
    fixture_destroy(test_fixture);
}

static void minimap_rejects_torn_component_scale_and_roots(void)
{
    fixture *test_fixture = minimap_fixture();
    rs3_minimap_candidate candidate;
    fixture_mutate_on_read(test_fixture, MINIMAP_COMPONENT_DATA, 2, MINIMAP_COMPONENT_DATA + 0x80,
                           284);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_clear_mutation(test_fixture);
    fixture_mutate_on_read(test_fixture, MINIMAP_SPRITE_DATA + 0x548, 2,
                           MINIMAP_SPRITE_DATA + 0x548, 0x3f800000);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_clear_mutation(test_fixture);
    fixture_mutate_on_read(test_fixture, FIXTURE_WORLD + 0x19898, 2, FIXTURE_WORLD + 0x19898, 0);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.status == RS3_UNAVAILABLE && candidate.flags == 0);
    fixture_destroy(test_fixture);
}

static void minimap_rejects_bad_identity_bounds_values_and_guards(void)
{
    fixture *test_fixture = minimap_fixture();
    rs3_minimap_candidate candidate;
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x18, 1466);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.flags == 0 && candidate.status == RS3_UNAVAILABLE);
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x18, 1465);
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x80, 0);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.flags == 0);
    fixture_put_u32(test_fixture, MINIMAP_COMPONENT_DATA + 0x80, 283);
    fixture_put_u32(test_fixture, MINIMAP_MANAGER_DATA + 0x3c, 0x7fc00000);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.flags == 0);
    fixture_put_u32(test_fixture, MINIMAP_MANAGER_DATA + 0x3c, 0x3f800000);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x210, UI_COMPONENTS + 4097ull * 24);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.flags == 0);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x210, UI_COMPONENTS + 24);
    fixture_put_u64(test_fixture, MINIMAP_COMPONENT_DATA + 0x220, FIXTURE_WORLD + 8);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.flags == 0);
    fixture_put_u64(test_fixture, MINIMAP_COMPONENT_DATA + 0x220, FIXTURE_WORLD);
    fixture_put_u32(test_fixture, FIXTURE_MODULE_BASE + 0x320d9e, 0);
    interface_minimap_stage(test_fixture->context, FIXTURE_WORLD, &candidate);
    assert(candidate.flags == 0 && candidate.reason == RS3_UI_UNSUPPORTED_PROFILE);
    fixture_destroy(test_fixture);
}

static void minimap_snapshot_copy_and_unavailable_reset(void)
{
    fixture *test_fixture = minimap_fixture();
    assert(fixture_decode(test_fixture));
    rs3_minimap_candidate candidate;
    rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(rs3_snapshot_minimap_candidate(snapshot, &candidate, sizeof candidate) == RS3_OK);
    assert(candidate.flags == 7 && candidate.width == 283);
    candidate.width++;
    assert(snapshot->minimap_candidate.width == 283);
    assert(rs3_snapshot_minimap_candidate(snapshot, &candidate, sizeof candidate - 1) == RS3_E_ABI);
    assert(rs3_snapshot_minimap_candidate(NULL, &candidate, sizeof candidate) == RS3_E_ARGUMENT);
    assert(rs3_snapshot_minimap_candidate(snapshot, NULL, sizeof candidate) == RS3_E_ARGUMENT);
    rs3_snapshot *consumer = NULL;
    assert(rs3_snapshot_create(&consumer) == RS3_OK);
    assert(rs3_snapshot_copy(snapshot, test_fixture->context->latest_frame) == RS3_OK);
    assert(rs3_copy_latest(test_fixture->context, consumer) == RS3_OK);
    assert(consumer->minimap_candidate.width == 283);
    assert(consumer->minimap_candidate.observed_ns == snapshot->minimap_candidate.observed_ns);
    interface_clear(test_fixture->context->latest_frame);
    assert(rs3_copy_latest(test_fixture->context, consumer) == RS3_OK);
    assert(consumer->minimap_candidate.status == RS3_UNAVAILABLE);
    assert(consumer->minimap_candidate.reason == RS3_UI_FRAME_UNAVAILABLE);
    assert(consumer->minimap_candidate.flags == 0 && consumer->minimap_candidate.width == 0);
    rs3_snapshot_destroy(consumer);
    fixture_destroy(test_fixture);
}

#define LABEL_TEST_NODES 0xbf0000ull
#define LABEL_TEST_NAME  0xbf1000ull

/** Build a selected main-bar slot with native text and graphic components. */
static fixture *action_label_fixture(void)
{
    fixture *test_fixture = bank_fixture();
    fixture_put_variable(test_fixture, FIXTURE_PLAYER_TABLE, PRESET_NODE, 10160, 1u | (6u << 5));
    fixture_add_block(test_fixture, UI_COMPONENTS, 266 * 24);
    fixture_add_block(test_fixture, LABEL_TEST_NODES, 3 * 0x200);
    fixture_add_block(test_fixture, LABEL_TEST_NAME, 128);
    fixture_put_u64(test_fixture, UI_CACHE + 1430 * 16 + 8, UI_RECORDS + 0x200);
    fixture_put_u32(test_fixture, UI_RECORDS + 0x200, 1430);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x208, UI_COMPONENTS);
    fixture_put_u64(test_fixture, UI_RECORDS + 0x210, UI_COMPONENTS + 266 * 24);
    const uint32_t ids[] = {265, 216, 209};
    const char *labels[] = {"1", "PgDn"};
    for (unsigned index = 0; index < 3; index++)
    {
        uint64_t node = LABEL_TEST_NODES + index * 0x200;
        fixture_put_u64(test_fixture, UI_COMPONENTS + ids[index] * 24 + 16, node);
        fixture_put_u64(test_fixture, node,
                        FIXTURE_MODULE_BASE + (index == 2 ? 0x1530520 : 0x1530678));
        fixture_put_u32(test_fixture, node + 0x18, ids[index] << 16 | 1430);
        if (index < 2)
        {
            unsigned char *header = fixture_bytes(test_fixture, node + 0x190, 24);
            size_t length = strlen(labels[index]);
            memcpy(header, labels[index], length);
            header[23] = (unsigned char)(23 - length);
        }
    }
    fixture_put_u64(test_fixture, LABEL_TEST_NODES + 0x400 + 0x98, LABEL_TEST_NAME);
    fixture_put_u32(test_fixture, LABEL_TEST_NODES + 0x400 + 0x1b8, UINT32_MAX);
    fixture_put_u32(test_fixture, LABEL_TEST_NODES + 0x400 + 0x188, 14264);
    strcpy(fixture_bytes(test_fixture, LABEL_TEST_NAME, 128), "<col=00ff00>Berserk</col>");
    assert(fixture_decode(test_fixture));
    test_fixture->snapshot->info.pid = FIXTURE_PID;
    test_fixture->snapshot->info.process_creation_time = FIXTURE_PROCESS_CREATION_TIME;
    assert(rs3_snapshot_copy(test_fixture->snapshot, test_fixture->context->latest_frame) ==
           RS3_OK);
    return test_fixture;
}

static void action_labels_have_checked_identity_chord_and_copy_contract(void)
{
    fixture *test_fixture = action_label_fixture();
    rs3_action_label label;
    uint64_t sequence = test_fixture->context->sample_sequence;
    assert(rs3_interface_sizeof(6) == sizeof label);
    assert(rs3_read_action_label(test_fixture->context, sequence, 1, 12, &label, sizeof label) ==
           RS3_OK);
    assert(label.flags == 15 && label.virtual_key == VK_NEXT && label.modifiers == 0);
    assert(!strcmp(label.key_label, "PgDn") && !strcmp(label.name, "Berserk"));
    assert(label.pid == FIXTURE_PID && label.sample_sequence == sequence);
    assert(label.preset_bar == 1 && label.slot == 12 && label.item_id == -1);
    assert(rs3_read_action_label(test_fixture->context, sequence + 1, 1, 12, &label,
                                 sizeof label) == RS3_E_STATE);
    assert(label.flags == 0 && label.observed_ns == 0);
    assert(rs3_read_action_label(test_fixture->context, sequence, 1, 12, &label,
                                 sizeof label - 1) == RS3_E_ABI);
    assert(rs3_read_action_label(test_fixture->context, sequence, 0, 12, &label, sizeof label) ==
           RS3_E_ARGUMENT);
    fixture_destroy(test_fixture);
}

static void action_labels_reject_preset_and_concurrent_text_changes(void)
{
    fixture *test_fixture = action_label_fixture();
    rs3_action_label label;
    uint64_t sequence = test_fixture->context->sample_sequence;
    fixture_mutate_on_read(test_fixture, LABEL_TEST_NODES + 0x200 + 0x190, 1,
                           LABEL_TEST_NODES + 0x200 + 0x190, 0);
    assert(rs3_read_action_label(test_fixture->context, sequence, 1, 12, &label, sizeof label) ==
           RS3_E_STATE);
    assert(label.flags == 0);
    fixture_destroy(test_fixture);
    test_fixture = action_label_fixture();
    sequence = test_fixture->context->sample_sequence;
    fixture_put_u32(test_fixture, LABEL_TEST_NODES + 0x190, '2');
    assert(rs3_read_action_label(test_fixture->context, sequence, 1, 12, &label, sizeof label) ==
           RS3_E_STATE);
    fixture_destroy(test_fixture);
    test_fixture = action_label_fixture();
    sequence = test_fixture->context->sample_sequence;
    fixture_mutate_on_read(test_fixture, LABEL_TEST_NODES + 0x400, 2, UI_COMPONENTS + 209 * 24 + 16,
                           0);
    assert(rs3_read_action_label(test_fixture->context, sequence, 1, 12, &label, sizeof label) ==
           RS3_E_STATE);
    assert(label.flags == 0);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(four_curse_bindings);
    RUN_TEST(items_moves_and_preset_switches);
    RUN_TEST(changing_domains_clear_only_their_own_validity);
    RUN_TEST(unsupported_codes_remain_raw);
    RUN_TEST(oversized_packed_values_are_rejected);
    RUN_TEST(ui_unknown_and_frame_reset);
    RUN_TEST(caller_owned_copy_contract);
    RUN_TEST(bank_attachment_is_distinct_from_cached_presence);
    RUN_TEST(bank_rejects_torn_cycles_and_reused_manager);
    RUN_TEST(bank_requires_guards_root_and_bounded_complete_graph);
    RUN_TEST(enrage_requires_heading_not_just_generic_dialog_id);
    RUN_TEST(enrage_rejects_torn_and_oversized_heading);
    RUN_TEST(ui_rejects_duplicate_keys_and_changing_vtable);
    RUN_TEST(pointer_raw_fields_never_become_semantic_hit_tests);
    RUN_TEST(pointer_empty_owner_and_profile_states_remain_explicit);
    RUN_TEST(pointer_rejects_torn_record_root_and_changed_code);
    RUN_TEST(pointer_heap_text_is_bounded_and_double_read);
    RUN_TEST(pointer_snapshot_wiring_copy_and_unavailable_reset);
    RUN_TEST(pointer_selects_modern_or_legacy_menu_without_changing_abi);
    RUN_TEST(pointer_rejects_changing_or_invalid_menu_backend_and_state);
    RUN_TEST(pointer_backend_instruction_guards_are_required);
    RUN_TEST(minimap_cached_sources_remain_unvalidated);
    RUN_TEST(minimap_rejects_torn_component_scale_and_roots);
    RUN_TEST(minimap_rejects_bad_identity_bounds_values_and_guards);
    RUN_TEST(minimap_snapshot_copy_and_unavailable_reset);
    RUN_TEST(action_labels_have_checked_identity_chord_and_copy_contract);
    RUN_TEST(action_labels_reject_preset_and_concurrent_text_changes);
    return 0;
}
