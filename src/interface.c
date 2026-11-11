/* Action-bar slot and binding tables. No game calls or memory writes. */
#include "interface.h"

#include <stdio.h>
#include <string.h>

#include "decode_utils.h"
#include "inventory.h"
#include "variables.h"

_Static_assert(sizeof(rs3_interface_info) == 32, "ABI interface info layout");
_Static_assert(sizeof(rs3_interface_observation) == 16, "ABI interface observation layout");
_Static_assert(sizeof(rs3_action_bar_slot) == 232, "ABI action bar layout");
_Static_assert(offsetof(rs3_action_bar_slot, raw_packed) == 72, "ABI action bar alignment");

#define STORED_BAR_COUNT            18u
#define DISPLAY_PRESETS_VAR         10160u
#define PACKED_TYPE_SHIFT           17u
#define PACKED_TYPE_MASK            127u
#define PACKED_ID_SHIFT             4u
#define PACKED_ID_MASK              8191u
#define MAX_INTERFACE_VARIABLES     192u
#define MAIN_EXTRA_SLOT_FIRST_VAR   4415u
#define MAIN_EXTRA_OBJECT_FIRST_VAR 4429u
#define FIRST_EXTENDED_SLOT         12u
#define GAME_MODIFIER_CONTROL       2u
#define FIRST_FUNCTION_KEY          1u
#define LAST_FUNCTION_KEY           12u

/* Bases are from the compact gamevals names, checked against local VarBit definitions. */
static const uint32_t packed_bases[STORED_BAR_COUNT] = {739,  751,   763,   775,   787,   5265,
                                                        5279, 5293,  5307,  5321,  8759,  8773,
                                                        8787, 10100, 10114, 11219, 11233, 11247};
static const uint32_t object_bases[STORED_BAR_COUNT] = {823,  835,   847,   859,   871,   5335,
                                                        5349, 5363,  5377,  5391,  8801,  8815,
                                                        8829, 10128, 10142, 11261, 11275, 11289};

/*
 * Groups 5..8 and modifier groups 2..3 hold other main-bar controls. They do
 * not belong to Additional Bar 1. Ignoring this gap misassigns every prayer.
 * 2026-11-11: displayed preset 6, slots 1..4, matched all four user chords.
 */
static const uint32_t key_groups[RS3_DISPLAYED_ACTION_BARS][4] = {{2852, 2853, 2854, 2855},
                                                                  {4997, 4998, 4999, 5000},
                                                                  {5001, 5002, 5003, 5004},
                                                                  {5005, 5006, 5007, 5008},
                                                                  {5009, 5010, 6302, 6620}};
static const uint32_t modifier_groups[RS3_DISPLAYED_ACTION_BARS] = {2859, 5011, 5012, 5013, 6303};

typedef struct interface_variable
{
    uint32_t id;
    int64_t value;
    int present;
    int readable;
} interface_variable;

typedef struct interface_variables
{
    variable_table table;
    interface_variable entries[MAX_INTERFACE_VARIABLES];
    unsigned count;
    int available;
} interface_variables;

typedef struct slot_definition
{
    uint32_t type;
    uint32_t id;
    uint32_t struct_id;
} slot_definition;

/* Compact ability bindings, not an extracted cache catalog. Filled below. */
static const slot_definition ability_definitions[] = {
    {1, 29, 1488},   /* Bladed Dive */
    {4, 14, 1491},   /* Salt the Wound */
    {4, 1, 14663},   /* Piercing Shot */
    {4, 2, 14664},   /* Binding Shot */
    {4, 4, 14666},   /* Snipe */
    {4, 20, 14667},  /* Fragmentation Shot */
    {4, 5, 14668},   /* Ricochet */
    {4, 6, 14669},   /* Snap Shot */
    {4, 7, 14670},   /* Rapid Fire */
    {4, 8, 14671},   /* Bombardment */
    {4, 21, 14672},  /* Incendiary Shot */
    {4, 22, 14673},  /* Unload */
    {4, 9, 14674},   /* Deadshot */
    {1, 16, 14677},  /* Slice */
    {1, 1, 14678},   /* Barge */
    {1, 2, 14679},   /* Adaptive Strike */
    {1, 18, 14681},  /* Smash */
    {1, 3, 14682},   /* Backhand */
    {1, 19, 14683},  /* Slaughter */
    {1, 4, 14684},   /* Flurry */
    {1, 12, 14685},  /* Hurricane */
    {1, 5, 14686},   /* Overpower */
    {1, 21, 14687},  /* Massacre */
    {1, 6, 14688},   /* Meteor Strike */
    {1, 9, 14700},   /* Punish */
    {1, 10, 14701},  /* Fury */
    {1, 25, 14702},  /* Decimate */
    {1, 27, 14703},  /* Cleave */
    {1, 11, 14704},  /* Assault */
    {1, 28, 14705},  /* Quake */
    {1, 20, 14706},  /* Destroy */
    {1, 13, 14707},  /* Berserk */
    {1, 28, 14708},  /* Frenzy */
    {1, 14, 14709},  /* Pulverise */
    {5, 1, 14710},   /* Anticipation */
    {5, 2, 14711},   /* Freedom */
    {5, 3, 14712},   /* Provoke */
    {5, 4, 14713},   /* Resonance */
    {5, 5, 14714},   /* Preparation */
    {5, 6, 14715},   /* Bash */
    {5, 7, 14716},   /* Reflect */
    {5, 8, 14717},   /* Debilitate */
    {5, 9, 14718},   /* Revenge */
    {5, 10, 14719},  /* Barricade */
    {5, 11, 14720},  /* Rejuvenate */
    {5, 12, 14721},  /* Immortality */
    {3, 1, 14725},   /* Runic Charge */
    {3, 3, 14727},   /* Impact */
    {3, 4, 14728},   /* Chain */
    {3, 5, 14729},   /* Combust */
    {3, 6, 14730},   /* Dragon Breath */
    {3, 7, 14731},   /* Asphyxiate */
    {3, 8, 14732},   /* Detonate */
    {3, 9, 14733},   /* Wild Magic */
    {3, 10, 14734},  /* Metamorphosis */
    {3, 10, 14735},  /* Tsunami */
    {3, 11, 14736},  /* Omnipower */
    {4, 10, 19251},  /* Death's Swiftness */
    {5, 13, 19252},  /* Natural Instinct */
    {3, 164, 19254}, /* Sunshine */
    {1, 22, 19255},  /* Balanced Strike */
    {4, 23, 19340},  /* Dazing Shot */
    {3, 165, 19342}, /* Sonic Wave */
    {3, 166, 19343}, /* Concentrated Blast */
    {5, 14, 25028},  /* Devotion */
    {4, 12, 28177},  /* Shadow Tendrils */
    {1, 23, 28178},  /* Blood Tendrils */
    {3, 169, 28180}, /* Smoke Tendrils */
    {3, 171, 28431}, /* Deep Impact */
    {3, 217, 28927}, /* Magma Tempest */
    {3, 172, 31985}, /* Corruption Blast */
    {4, 13, 31986},  /* Corruption Shot */
    {5, 15, 37206},  /* Unsullied */
    {5, 16, 37207},  /* Aggression */
    {3, 204, 37220}, /* Golden Touch */
    {3, 192, 39530}, /* Shock */
    {3, 193, 39531}, /* Horror */
    {4, 15, 39532},  /* Demoralise */
    {4, 16, 39533},  /* Rout */
    {1, 8, 44244},   /* Dismember */
    {5, 17, 45045},  /* Divert */
    {5, 18, 45340},  /* Cease */
    {3, 216, 45800}, /* Magma Tempest (Targeted) */
    {4, 17, 45801},  /* Quiver ammo slot 1 */
    {4, 18, 45802},  /* Quiver ammo slot 2 */
    {1, 15, 46279},  /* Chaos Roar */
    {1, 100, 49531}, /* Basic<nbsp>Attack */
    {3, 258, 52777}, /* Basic<nbsp>Attack */
    {1, 16, 52781},  /* Rend */
    {1, 8, 52788},   /* Slaughter */
    {1, 8, 52789},   /* Massacre */
    {4, 100, 52795}, /* Basic<nbsp>Attack */
    {4, 19, 52796},  /* Imbue: Shadows */
    {4, 11, 52799},  /* Galeshot */
    {3, 265, 53444}, /* Sanguine offering */
};

uint32_t rs3_interface_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case 1:
            return sizeof(rs3_interface_info);
        case 2:
            return sizeof(rs3_interface_observation);
        case 3:
            return sizeof(rs3_action_bar_slot);
        default:
            return 0;
    }
}

void interface_clear(rs3_snapshot *snapshot)
{
    memset(&snapshot->interface_info, 0, sizeof snapshot->interface_info);
    memset(snapshot->interfaces, 0, sizeof snapshot->interfaces);
    memset(snapshot->action_bar, 0, sizeof snapshot->action_bar);
    snapshot->interface_info.status = RS3_UNAVAILABLE;
    snapshot->interface_info.reason = RS3_UI_FRAME_UNAVAILABLE;
}

/** Cache each selected variable once, with a second full pass before publication. */
static const interface_variable *get_variable(rs3_context *context, interface_variables *variables,
                                              uint32_t id)
{
    for (unsigned index = 0; index < variables->count; index++)
    {
        if (variables->entries[index].id == id)
        {
            return &variables->entries[index];
        }
    }
    if (variables->count >= MAX_INTERFACE_VARIABLES)
    {
        return NULL;
    }
    interface_variable *entry = &variables->entries[variables->count++];
    entry->id = id;
    entry->readable = variables->available && variable_table_find(context, &variables->table, id,
                                                                  &entry->value, &entry->present);
    if (entry->readable && entry->present &&
        (entry->value < INT32_MIN || entry->value > UINT32_MAX))
    {
        entry->readable = 0;
    }
    return entry;
}

/** Verify values as well as keys/header, including absent-to-present transitions. */
static int variables_unchanged(rs3_context *context, const interface_variables *variables,
                               uint64_t header)
{
    if (!variables->available || !variable_table_unchanged(context, header, &variables->table))
    {
        return 0;
    }
    for (unsigned index = 0; index < variables->count; index++)
    {
        const interface_variable *entry = &variables->entries[index];
        int present = 0;
        int64_t value = 0;
        if (!entry->readable ||
            !variable_table_find(context, &variables->table, entry->id, &value, &present) ||
            present != entry->present || (present && value != entry->value))
        {
            return 0;
        }
    }
    return variable_table_unchanged(context, header, &variables->table);
}

static uint32_t variable_reason(const interface_variable *entry)
{
    return entry == NULL || !entry->readable ? RS3_UI_READ_FAILED
           : !entry->present                 ? RS3_UI_NOT_SET
                                             : RS3_UI_CONFIRMED;
}

/** Resolve only unique compact metadata matches; upgrades sharing an ID stay unknown. */
static void decode_definition(const rs3_context *context, rs3_action_bar_slot *slot)
{
    if (slot->content_type == 7)
    {
        const char *name = NULL;
        switch (slot->content_id)
        {
            case 38:
                slot->ability_struct_id = 14594;
                name = "Deflect Magic";
                break;
            case 39:
                slot->ability_struct_id = 14595;
                name = "Deflect Ranged";
                break;
            case 40:
                slot->ability_struct_id = 14596;
                name = "Deflect Melee";
                break;
            case 49:
                slot->ability_struct_id = 14607;
                name = "Soul Split";
                break;
        }
        if (name != NULL)
        {
            snprintf(slot->name, sizeof slot->name, "%s", name);
            slot->flags |= RS3_BAR_PRAYER_KNOWN;
            slot->reason = RS3_UI_CONFIRMED;
        }
        return;
    }
    unsigned matches = 0;
    uint32_t struct_id = 0;
    for (unsigned index = 0; index < sizeof ability_definitions / sizeof *ability_definitions;
         index++)
    {
        const slot_definition *definition = &ability_definitions[index];
        if (slot->content_type == definition->type && slot->content_id == (int32_t)definition->id)
        {
            matches++;
            struct_id = definition->struct_id;
        }
    }
    if (matches != 1)
    {
        return;
    }
    slot->ability_struct_id = (int32_t)struct_id;
    slot->flags |= RS3_BAR_ABILITY_KNOWN;
    slot->reason = RS3_UI_CONFIRMED;
    const item_catalog *catalog = &context->inventory->catalog;
    for (uint32_t index = 0; index < catalog->ability_count; index++)
    {
        catalog_ability_definition definition;
        catalog_get_ability(catalog, index, &definition);
        if (definition.struct_id == struct_id)
        {
            catalog_copy_name(catalog, definition.name_offset, slot->name);
            break;
        }
    }
}

static void decode_slot(rs3_context *context, interface_variables *variables,
                        rs3_action_bar_slot *slot)
{
    unsigned preset_index = slot->preset_bar - 1;
    unsigned slot_index = slot->slot - 1;
    slot->source_var = packed_bases[preset_index] + slot_index;
    slot->object_var = object_bases[preset_index] + slot_index;
    if (preset_index < 5 && slot_index >= FIRST_EXTENDED_SLOT)
    {
        slot->source_var =
            MAIN_EXTRA_SLOT_FIRST_VAR + preset_index * 2 + slot_index - FIRST_EXTENDED_SLOT;
        slot->object_var =
            MAIN_EXTRA_OBJECT_FIRST_VAR + preset_index * 2 + slot_index - FIRST_EXTENDED_SLOT;
    }
    const interface_variable *packed = get_variable(context, variables, slot->source_var);
    const interface_variable *object = get_variable(context, variables, slot->object_var);
    slot->reason = variable_reason(packed);
    if (slot->reason != RS3_UI_CONFIRMED)
    {
        return;
    }
    slot->raw_packed = packed->value;
    slot->flags |= RS3_BAR_PACKED_KNOWN;
    slot->content_type = ((uint32_t)packed->value >> PACKED_TYPE_SHIFT) & PACKED_TYPE_MASK;
    slot->content_id = ((uint32_t)packed->value >> PACKED_ID_SHIFT) & PACKED_ID_MASK;
    slot->reason = RS3_UI_CONTENT_UNMAPPED;
    if (variable_reason(object) == RS3_UI_CONFIRMED)
    {
        slot->raw_object = object->value;
        slot->flags |= RS3_BAR_OBJECT_KNOWN;
        if (slot->content_type == 10 && object->value >= 0 && object->value <= INT32_MAX)
        {
            slot->item_id = (int32_t)object->value;
            slot->flags |= RS3_BAR_ITEM_KNOWN;
            slot->reason = RS3_UI_CONFIRMED;
            catalog_item_definition definition;
            if (catalog_find_item(&context->inventory->catalog, slot->item_id, &definition))
            {
                catalog_copy_name(&context->inventory->catalog, definition.name_offset, slot->name);
            }
            return;
        }
    }
    decode_definition(context, slot);
}

static void decode_binding(rs3_context *context, interface_variables *variables,
                           rs3_action_bar_slot *slot)
{
    unsigned display_index = slot->display_bar - 1;
    unsigned slot_index = slot->slot - 1;
    slot->key_var = key_groups[display_index][slot_index / 4];
    slot->modifier_var = modifier_groups[display_index];
    const interface_variable *keys = get_variable(context, variables, slot->key_var);
    const interface_variable *modifiers = get_variable(context, variables, slot->modifier_var);
    slot->binding_reason = variable_reason(keys);
    if (slot->binding_reason != RS3_UI_CONFIRMED)
    {
        return;
    }
    slot->raw_key_packed = keys->value;
    slot->key_code = ((uint32_t)keys->value >> ((slot_index % 4) * 8)) & 255u;
    slot->flags |= RS3_BAR_KEY_KNOWN;
    slot->binding_reason = variable_reason(modifiers);
    if (slot->binding_reason != RS3_UI_CONFIRMED)
    {
        return;
    }
    slot->raw_modifier_packed = modifiers->value;
    slot->modifier_code = ((uint32_t)modifiers->value >> (slot_index * 2)) & 3u;
    slot->flags |= RS3_BAR_MODIFIER_KNOWN;
    slot->binding_reason = RS3_UI_KEY_UNMAPPED;
    if (slot->key_code >= FIRST_FUNCTION_KEY && slot->key_code <= LAST_FUNCTION_KEY &&
        (slot->modifier_code == 0 || slot->modifier_code == GAME_MODIFIER_CONTROL))
    {
        slot->key_modifiers = slot->modifier_code == GAME_MODIFIER_CONTROL ? 2 : 0;
        slot->flags |= RS3_BAR_CHORD_KNOWN;
        slot->binding_reason = RS3_UI_CONFIRMED;
    }
}

void interface_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t client_address)
{
    interface_clear(snapshot);
    rs3_interface_info *info = &snapshot->interface_info;
    info->interface_count = RS3_REQUIRED_INTERFACES;
    for (unsigned index = 0; index < RS3_REQUIRED_INTERFACES; index++)
    {
        snapshot->interfaces[index] =
            (rs3_interface_observation){index + 1, RS3_UI_UNMAPPED, -1, -1};
    }
    info->reason = RS3_UI_UNSUPPORTED_PROFILE;
    if (context->target.profile != &profile_primary)
    {
        return;
    }
    info->status = RS3_PARTIAL;
    info->reason = RS3_UI_UNMAPPED; /* The open-interface registry is still unmapped. */
    uint64_t headers[2] = {player_address + LAYOUT_PLAYER_VARIABLE_TABLE_OFFSET,
                           client_address + LAYOUT_CLIENT_VARIABLE_TABLE_OFFSET};
    interface_variables variables[2] = {0};
    for (unsigned index = 0; index < 2; index++)
    {
        variables[index].available =
            variable_table_open(context, headers[index], &variables[index].table);
    }
    const interface_variable *presets = get_variable(context, &variables[0], DISPLAY_PRESETS_VAR);
    if (variable_reason(presets) != RS3_UI_CONFIRMED)
    {
        info->reason = variable_reason(presets);
        return;
    }
    for (unsigned display_index = 0; display_index < RS3_DISPLAYED_ACTION_BARS; display_index++)
    {
        uint32_t preset = ((uint32_t)presets->value >> (display_index * 5)) & 31u;
        for (unsigned slot_index = 0; slot_index < RS3_ACTION_BAR_SLOTS; slot_index++)
        {
            rs3_action_bar_slot *slot = &snapshot->action_bar[info->action_bar_count++];
            slot->display_bar = display_index + 1;
            slot->slot = slot_index + 1;
            slot->content_id = -1;
            slot->item_id = -1;
            slot->ability_struct_id = -1;
            slot->reason = RS3_UI_NOT_SET;
            if (preset > 0 && preset <= STORED_BAR_COUNT)
            {
                slot->preset_bar = preset;
                slot->flags |= RS3_BAR_PRESET_KNOWN;
                decode_slot(context, &variables[0], slot);
            }
            decode_binding(context, &variables[1], slot);
        }
    }
    int stable_player = variables_unchanged(context, &variables[0], headers[0]);
    int stable_client = variables_unchanged(context, &variables[1], headers[1]);
    int all_slots_read = 1;
    for (unsigned index = 0; index < info->action_bar_count; index++)
    {
        rs3_action_bar_slot *slot = &snapshot->action_bar[index];
        if (!stable_player)
        {
            slot->flags &= RS3_BAR_KEY_KNOWN | RS3_BAR_MODIFIER_KNOWN | RS3_BAR_CHORD_KNOWN;
            slot->reason = RS3_UI_READ_FAILED;
        }
        if (!stable_client)
        {
            slot->flags &= ~(RS3_BAR_KEY_KNOWN | RS3_BAR_MODIFIER_KNOWN | RS3_BAR_CHORD_KNOWN);
            slot->binding_reason = RS3_UI_READ_FAILED;
        }
        if (!(slot->flags & RS3_BAR_PACKED_KNOWN))
        {
            all_slots_read = 0;
        }
    }
    info->action_bar_complete = (uint32_t)all_slots_read;
}
