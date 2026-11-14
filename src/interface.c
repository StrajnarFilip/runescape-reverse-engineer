/* Action-bar slot and binding tables. No game calls or memory writes. */
#include "interface.h"

#include <stdio.h>
#include <string.h>

#include "decode_utils.h"
#include "inventory.h"
#include "memory.h"
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

/* Original 951-3 only. These are attachment records, not the loaded-interface cache. */
#define UI_MANAGER_OFFSET           0x19910u
#define UI_MANAGER_VTABLE_RVA       0x15382c8u
#define UI_LOOKUP_VTABLE_OFFSET     0x10u
#define UI_LOOKUP_VTABLE_RVA        0x15382e8u
#define UI_CACHE_HEADER_OFFSET      0x50u
#define UI_ROOT_OFFSET              0xf8u
#define UI_ATTACHMENT_HEADER_OFFSET 0x118u
#define UI_ATTACHMENT_NODE_BYTES    32u
#define UI_ATTACHMENT_VALUE_BYTES   16u
#define UI_ATTACHMENT_VALUE_OFFSET  16u
#define UI_ATTACHMENT_NEXT_OFFSET   24u
#define UI_CHILD_ID_OFFSET          12u
#define UI_MAX_BUCKETS              1024u
#define UI_MAX_ATTACHMENTS          512u
#define UI_MAX_CACHED_INTERFACES    8192u
#define UI_DESKTOP_ROOT             1477u
#define UI_BANK_INTERFACE           517u
#define UI_BANK_PARENT_COMPONENT    706u
#define UI_ENRAGE_INTERFACE         720u
#define UI_ENRAGE_PARENT_COMPONENT  748u
#define UI_ENRAGE_HEADING_COMPONENT 3u
#define UI_TEXT_VTABLE_RVA          0x1530678u
#define UI_TEXT_OFFSET              0x190u
#define UI_COMPONENT_ID_OFFSET      0x18u
#define UI_HEADING_MAX_BYTES        64u
#define UI_MAX_COMPONENTS           4096u

static const char enrage_heading[] = "Choose initial enrage.";

typedef struct ui_code_guard
{
    uint32_t rva;
    uint32_t size;
    unsigned char bytes[32];
} ui_code_guard;

/* GetInterface thunk/body, attachment bucket lookup and child-ID load. */
static const ui_code_guard ui_guards[] = {
    {0x3c7f90, 9, {0x48, 0x83, 0xc1, 0x28, 0xe9, 0xc7, 0x90, 0x8a, 0x00}},
    {0xc71060, 32, {0x48, 0x83, 0xec, 0x28, 0x4c, 0x8b, 0x41, 0x18, 0x48, 0x8b, 0x41,
                    0x20, 0x49, 0x2b, 0xc0, 0x48, 0xc1, 0xf8, 0x04, 0x3b, 0xd0, 0x7d,
                    0x11, 0x48, 0x63, 0xc2, 0x48, 0xc1, 0xe0, 0x04, 0x49, 0x03}},
    {0x3ca949,
     14,
     {0x41, 0x8b, 0x8e, 0x10, 0x01, 0x00, 0x00, 0x4d, 0x8b, 0x8e, 0x08, 0x01, 0x00, 0x00}},
    {0x3ca9a7, 3, {0x8b, 0x50, 0x0c}}};

typedef struct ui_attachment_read
{
    uint64_t address;
    uint64_t value_address;
    unsigned char node[UI_ATTACHMENT_NODE_BYTES];
    unsigned char value[UI_ATTACHMENT_VALUE_BYTES];
} ui_attachment_read;

/** Check loaded instructions as well as the executable profile checked at attachment. */
static int ui_code_matches(rs3_context *context)
{
    unsigned char bytes[32];
    for (unsigned index = 0; index < sizeof ui_guards / sizeof *ui_guards; index++)
    {
        const ui_code_guard *guard = &ui_guards[index];
        if (!memory_read(context, context->target.module_base + guard->rva, bytes, guard->size) ||
            memcmp(bytes, guard->bytes, guard->size) != 0)
        {
            return 0;
        }
    }
    return 1;
}

/** Validate an interface cache record without mistaking cached presence for open state. */
static int ui_cache_has_id(rs3_context *context, const unsigned char *cache_header, uint32_t id)
{
    uint64_t begin = decode_u64(cache_header);
    uint64_t end = decode_u64(cache_header + 8);
    if (!is_valid_remote_pointer(begin) || end < begin || (end - begin) % 16 != 0 ||
        (end - begin) / 16 > UI_MAX_CACHED_INTERFACES || id >= (end - begin) / 16)
    {
        return 0;
    }
    unsigned char pair[16];
    unsigned char pair_after[16];
    uint32_t observed_id;
    return memory_read(context, begin + id * 16ull, pair, sizeof pair) &&
           is_valid_remote_pointer(decode_u64(pair + 8)) &&
           memory_read(context, decode_u64(pair + 8), &observed_id, sizeof observed_id) &&
           observed_id == id &&
           memory_read(context, begin + id * 16ull, pair_after, sizeof pair_after) &&
           memcmp(pair, pair_after, sizeof pair) == 0;
}

/**
 * Read the labelled heading of generic interface 720. Return 1 for the exact
 * enrage heading, 0 for another heading, -1 for an unreadable/torn record.
 * No game string or component getter is called.
 */
static int enrage_heading_matches(rs3_context *context, const unsigned char *cache_header)
{
    uint64_t cache = decode_u64(cache_header);
    unsigned char pair[16];
    unsigned char vector[16];
    unsigned char entry[24];
    unsigned char component[32];
    unsigned char text_header[24];
    if (!memory_read(context, cache + UI_ENRAGE_INTERFACE * 16ull, pair, sizeof pair))
    {
        return -1;
    }
    uint64_t interface_record = decode_u64(pair + 8);
    if (!memory_read(context, interface_record + 8, vector, sizeof vector))
    {
        return -1;
    }
    uint64_t begin = decode_u64(vector);
    uint64_t end = decode_u64(vector + 8);
    if (!is_valid_remote_pointer(begin) || end < begin || (end - begin) % 24 != 0 ||
        (end - begin) / 24 > UI_MAX_COMPONENTS ||
        UI_ENRAGE_HEADING_COMPONENT >= (end - begin) / 24 ||
        !memory_read(context, begin + UI_ENRAGE_HEADING_COMPONENT * 24ull, entry, sizeof entry))
    {
        return -1;
    }
    uint64_t object = decode_u64(entry + 16);
    if (!memory_read(context, object, component, sizeof component) ||
        decode_u64(component) != context->target.module_base + UI_TEXT_VTABLE_RVA ||
        decode_u32(component + UI_COMPONENT_ID_OFFSET) !=
            (UI_ENRAGE_HEADING_COMPONENT << 16 | UI_ENRAGE_INTERFACE) ||
        !memory_read(context, object + UI_TEXT_OFFSET, text_header, sizeof text_header))
    {
        return -1;
    }
    unsigned char text[UI_HEADING_MAX_BYTES + 1];
    uint64_t length;
    int heap = (text_header[23] & 128) != 0;
    if (heap)
    {
        length = decode_u64(text_header + 8);
        if (length > UI_HEADING_MAX_BYTES ||
            !memory_read(context, decode_u64(text_header), text, (size_t)length))
        {
            return -1;
        }
    }
    else
    {
        if (text_header[23] > 23)
        {
            return -1;
        }
        length = 23 - text_header[23];
        memcpy(text, text_header, (size_t)length);
    }
    unsigned char after[32];
    unsigned char text_after[UI_HEADING_MAX_BYTES + 1];
    if (!memory_read(context, object + UI_TEXT_OFFSET, after, sizeof text_header) ||
        memcmp(after, text_header, sizeof text_header) != 0 ||
        (heap && (!memory_read(context, decode_u64(text_header), text_after, (size_t)length) ||
                  memcmp(text_after, text, (size_t)length) != 0)) ||
        !memory_read(context, object, after, sizeof component) ||
        memcmp(after, component, sizeof component) != 0 ||
        !memory_read(context, begin + UI_ENRAGE_HEADING_COMPONENT * 24ull, after, sizeof entry) ||
        memcmp(after, entry, sizeof entry) != 0 ||
        !memory_read(context, interface_record + 8, after, sizeof vector) ||
        memcmp(after, vector, sizeof vector) != 0 ||
        !memory_read(context, cache + UI_ENRAGE_INTERFACE * 16ull, after, sizeof pair) ||
        memcmp(after, pair, sizeof pair) != 0 || !is_valid_utf8(text, (uint32_t)length))
    {
        return -1;
    }
    return entry[0] == 0 && length == sizeof enrage_heading - 1 &&
           memcmp(text, enrage_heading, (size_t)length) == 0;
}

/**
 * Read every attachment, then re-read all nodes, payloads, buckets and identities.
 * Bank root and the initial-enrage chooser were labelled live. Attachment
 * does not prove a subpanel/button is visible, unobstructed or clickable.
 */
static void stage_ui_roots(rs3_context *context, rs3_snapshot *snapshot, uint64_t client_address)
{
    rs3_interface_observation *bank = &snapshot->interfaces[RS3_UI_BANK_PRESET - 1];
    rs3_interface_observation *enrage = &snapshot->interfaces[RS3_UI_ENRAGE - 1];
    bank->reason = RS3_UI_READ_FAILED;
    enrage->reason = RS3_UI_READ_FAILED;
    if (!ui_code_matches(context))
    {
        bank->reason = RS3_UI_UNSUPPORTED_PROFILE;
        enrage->reason = RS3_UI_UNSUPPORTED_PROFILE;
        return;
    }
    uint64_t world;
    uint64_t manager;
    uint64_t vtable;
    uint64_t lookup_vtable;
    uint32_t scene_state;
    uint32_t root;
    if (!memory_read_pointer(context, client_address + LAYOUT_STATE_POINTER_OFFSET, &world) ||
        !memory_read(context, world + LAYOUT_GAME_STATE_OFFSET, &scene_state, sizeof scene_state) ||
        scene_state != LAYOUT_IN_GAME_STATE ||
        !memory_read_pointer(context, world + UI_MANAGER_OFFSET, &manager) || manager == 0 ||
        !memory_read_pointer(context, manager, &vtable) ||
        vtable != context->target.module_base + UI_MANAGER_VTABLE_RVA ||
        !memory_read_pointer(context, manager + UI_LOOKUP_VTABLE_OFFSET, &lookup_vtable) ||
        lookup_vtable != context->target.module_base + UI_LOOKUP_VTABLE_RVA ||
        !memory_read(context, manager + UI_ROOT_OFFSET, &root, sizeof root))
    {
        return;
    }
    if (root != UI_DESKTOP_ROOT)
    {
        bank->reason = RS3_UI_UNMAPPED;
        enrage->reason = RS3_UI_UNMAPPED;
        return;
    }
    unsigned char cache_header[16];
    unsigned char header[24];
    if (!memory_read(context, manager + UI_CACHE_HEADER_OFFSET, cache_header,
                     sizeof cache_header) ||
        !ui_cache_has_id(context, cache_header, root) ||
        !memory_read(context, manager + UI_ATTACHMENT_HEADER_OFFSET, header, sizeof header))
    {
        return;
    }
    uint64_t bucket_address = decode_u64(header);
    uint64_t bucket_count = decode_u64(header + 8);
    uint64_t expected_count = decode_u64(header + 16);
    if (!is_valid_remote_pointer(bucket_address) || bucket_count == 0 ||
        bucket_count > UI_MAX_BUCKETS || expected_count > UI_MAX_ATTACHMENTS)
    {
        return;
    }
    uint64_t buckets[UI_MAX_BUCKETS + 1];
    uint64_t buckets_after[UI_MAX_BUCKETS + 1];
    size_t bucket_bytes = (size_t)(bucket_count + 1) * sizeof(uint64_t);
    if (!memory_read(context, bucket_address, buckets, bucket_bytes))
    {
        return;
    }
    uint64_t sentinel = buckets[bucket_count];
    ui_attachment_read reads[UI_MAX_ATTACHMENTS];
    unsigned read_count = 0;
    int bank_attached = 0;
    int other_bank_parent = 0;
    int enrage_attached = 0;
    int other_enrage_parent = 0;
    for (uint64_t bucket_index = 0; bucket_index < bucket_count; bucket_index++)
    {
        uint64_t node = buckets[bucket_index];
        while (node != 0 && node != sentinel)
        {
            if (read_count == UI_MAX_ATTACHMENTS)
            {
                return;
            }
            for (unsigned index = 0; index < read_count; index++)
            {
                if (reads[index].address == node)
                {
                    return;
                }
            }
            ui_attachment_read *read = &reads[read_count++];
            read->address = node;
            if (!memory_read(context, node, read->node, sizeof read->node))
            {
                return;
            }
            uint32_t key = decode_u32(read->node);
            for (unsigned index = 0; index + 1 < read_count; index++)
            {
                if (decode_u32(reads[index].node) == key)
                {
                    return;
                }
            }
            read->value_address = decode_u64(read->node + UI_ATTACHMENT_VALUE_OFFSET);
            if (key % bucket_count != bucket_index ||
                !memory_read(context, read->value_address, read->value, sizeof read->value))
            {
                return;
            }
            int32_t child = decode_i32(read->value + UI_CHILD_ID_OFFSET);
            if (child < 0 || child >= (int32_t)UI_MAX_CACHED_INTERFACES)
            {
                return;
            }
            if (child == (int32_t)UI_BANK_INTERFACE)
            {
                if (key == (UI_DESKTOP_ROOT << 16 | UI_BANK_PARENT_COMPONENT))
                {
                    bank_attached = 1;
                }
                else
                {
                    other_bank_parent = 1;
                }
            }
            if (child == (int32_t)UI_ENRAGE_INTERFACE)
            {
                if (key == (UI_DESKTOP_ROOT << 16 | UI_ENRAGE_PARENT_COMPONENT))
                {
                    enrage_attached = 1;
                }
                else
                {
                    other_enrage_parent = 1;
                }
            }
            node = decode_u64(read->node + UI_ATTACHMENT_NEXT_OFFSET);
        }
    }
    if (read_count != expected_count ||
        (bank_attached && !ui_cache_has_id(context, cache_header, UI_BANK_INTERFACE)) ||
        (enrage_attached && !ui_cache_has_id(context, cache_header, UI_ENRAGE_INTERFACE)))
    {
        return;
    }
    unsigned char bytes[UI_ATTACHMENT_NODE_BYTES];
    for (unsigned index = 0; index < read_count; index++)
    {
        const ui_attachment_read *read = &reads[index];
        if (!memory_read(context, read->address, bytes, sizeof read->node) ||
            memcmp(bytes, read->node, sizeof read->node) != 0 ||
            !memory_read(context, read->value_address, bytes, sizeof read->value) ||
            memcmp(bytes, read->value, sizeof read->value) != 0)
        {
            return;
        }
    }
    int heading = enrage_attached ? enrage_heading_matches(context, cache_header) : 1;
    if (!memory_read(context, bucket_address, buckets_after, bucket_bytes) ||
        memcmp(buckets, buckets_after, bucket_bytes) != 0 ||
        !memory_read(context, manager + UI_ATTACHMENT_HEADER_OFFSET, bytes, sizeof header) ||
        memcmp(bytes, header, sizeof header) != 0 ||
        !memory_read(context, manager + UI_CACHE_HEADER_OFFSET, bytes, sizeof cache_header) ||
        memcmp(bytes, cache_header, sizeof cache_header) != 0)
    {
        return;
    }
    uint64_t world_after;
    uint64_t manager_after;
    uint32_t scene_after;
    uint32_t root_after;
    uint64_t vtable_after;
    uint64_t lookup_after;
    if (!memory_read_pointer(context, client_address + LAYOUT_STATE_POINTER_OFFSET, &world_after) ||
        world_after != world ||
        !memory_read_pointer(context, world + UI_MANAGER_OFFSET, &manager_after) ||
        manager_after != manager ||
        !memory_read(context, world + LAYOUT_GAME_STATE_OFFSET, &scene_after, sizeof scene_after) ||
        scene_after != scene_state ||
        !memory_read(context, manager + UI_ROOT_OFFSET, &root_after, sizeof root_after) ||
        root_after != root || !memory_read_pointer(context, manager, &vtable_after) ||
        vtable_after != vtable ||
        !memory_read_pointer(context, manager + UI_LOOKUP_VTABLE_OFFSET, &lookup_after) ||
        lookup_after != lookup_vtable)
    {
        return;
    }
    if (other_bank_parent)
    {
        bank->reason = RS3_UI_UNMAPPED;
    }
    else
    {
        bank->interface_id = UI_BANK_INTERFACE;
        bank->open = bank_attached;
        bank->reason = RS3_UI_CONFIRMED;
    }
    if (other_enrage_parent || heading == 0)
    {
        enrage->reason = RS3_UI_UNMAPPED;
    }
    else if (heading > 0)
    {
        enrage->interface_id = UI_ENRAGE_INTERFACE;
        enrage->open = enrage_attached;
        enrage->reason = RS3_UI_CONFIRMED;
    }
}

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
    int primary_profile = context->target.profile == &profile_primary;
    if (!primary_profile && context->target.profile != &profile_compatibility)
    {
        return;
    }
    info->status = RS3_PARTIAL;
    info->reason =
        RS3_UI_UNMAPPED; /* Other dialogs, preset visibility and options remain unmapped. */
    /* UI roots use primary-client code addresses. Action bars below are decoded only from the
     * server-defined player and client variables, whose IDs are the same in both clients. */
    if (primary_profile)
    {
        stage_ui_roots(context, snapshot, client_address);
    }
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
