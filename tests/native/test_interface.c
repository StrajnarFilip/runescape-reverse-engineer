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
        assert(snapshot->interfaces[index].reason == RS3_UI_UNMAPPED);
    }
    interface_clear(test_fixture->snapshot);
    assert(snapshot->interface_info.action_bar_count == 0);
    assert(snapshot->interface_info.status == RS3_UNAVAILABLE);
    assert(snapshot->action_bar[FIRST_ADDITIONAL_SLOT].flags == 0);
    fixture_destroy(test_fixture);
    test_fixture = fixture_create_for_profile(&profile_compatibility);
    interface_stage(test_fixture->context, test_fixture->snapshot, FIXTURE_PLAYER, FIXTURE_CLIENT);
    assert(test_fixture->snapshot->interface_info.reason == RS3_UI_UNSUPPORTED_PROFILE);
    assert(test_fixture->snapshot->interface_info.action_bar_count == 0);
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

int main(void)
{
    RUN_TEST(four_curse_bindings);
    RUN_TEST(items_moves_and_preset_switches);
    RUN_TEST(changing_domains_clear_only_their_own_validity);
    RUN_TEST(unsupported_codes_remain_raw);
    RUN_TEST(oversized_packed_values_are_rejected);
    RUN_TEST(ui_unknown_and_frame_reset);
    RUN_TEST(caller_owned_copy_contract);
    return 0;
}
