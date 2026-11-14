/* Optional interface and action-bar observations, copied into caller storage. */
#ifndef RS3_PUBLIC_INTERFACE_H
#define RS3_PUBLIC_INTERFACE_H

#include "rs3.h"

#ifdef __cplusplus
extern "C"
{
#endif

#define RS3_DISPLAYED_ACTION_BARS 5u
#define RS3_ACTION_BAR_SLOTS      14u
#define RS3_MAX_ACTION_BAR_SLOTS  (RS3_DISPLAYED_ACTION_BARS * RS3_ACTION_BAR_SLOTS)
#define RS3_REQUIRED_INTERFACES   5u
#define RS3_INTERFACE_NAME_BYTES  128u
#define RS3_POINTER_TEXT_CAPACITY 128u

enum rs3_interface_reason
{
    RS3_UI_CONFIRMED = 0,
    RS3_UI_UNMAPPED = 1,
    RS3_UI_NOT_SET = 2,
    RS3_UI_READ_FAILED = 3,
    RS3_UI_UNSUPPORTED_PROFILE = 4,
    RS3_UI_CONTENT_UNMAPPED = 5,
    RS3_UI_KEY_UNMAPPED = 6,
    RS3_UI_FRAME_UNAVAILABLE = 7,
    RS3_UI_UNVALIDATED_SOURCE = 8
};

/** Raw menu-record read flags only; these never authorize a click or identify an NPC. */
enum rs3_pointer_candidate_flags
{
    RS3_POINTER_RAW_MENU = 1u,
    RS3_POINTER_RAW_RECORD = 2u,
    RS3_POINTER_RAW_TEXT = 4u,
    RS3_POINTER_RAW_TYPE = 8u
};

/**
 * Uncalibrated engine menu candidate. Strings may contain client markup.
 * raw_reference/raw_arg1/raw_arg2 are overloaded fields, not typed entity IDs
 * or coordinates. raw_type_rva identifies module data, not a callable API.
 * A cleared record pointer does not prove that no entity is under the cursor.
 * Successful raw reads remain PARTIAL/UNVALIDATED_SOURCE until live calibration.
 */
typedef struct rs3_pointer_candidate
{
    uint64_t observed_ns;
    uint32_t status;
    uint32_t reason;
    uint32_t flags;
    uint32_t raw_type_rva;
    int32_t raw_reference;
    int32_t raw_item_id;
    int32_t raw_arg1;
    int32_t raw_arg2;
    int32_t raw_menu_state;
    uint32_t reserved;
    char name[RS3_POINTER_TEXT_CAPACITY];
    char verb[RS3_POINTER_TEXT_CAPACITY];
} rs3_pointer_candidate;

enum rs3_interface_kind
{
    RS3_UI_INSTANCE = 1,
    RS3_UI_ENRAGE, /**< Initial-enrage choice page only; custom input remains unmapped. */
    RS3_UI_LOOT,
    RS3_UI_BANK_PRESET, /**< Bank root only; preset subpanel visibility/options remain unknown. */
    RS3_UI_DEATH
};

/** Validity flags, not input permissions. Physical click points remain unmapped. */
enum rs3_action_bar_flags
{
    RS3_BAR_PRESET_KNOWN = 1,
    RS3_BAR_PACKED_KNOWN = 2,
    RS3_BAR_OBJECT_KNOWN = 4,
    RS3_BAR_ITEM_KNOWN = 8,
    RS3_BAR_ABILITY_KNOWN = 16,
    RS3_BAR_PRAYER_KNOWN = 32,
    RS3_BAR_KEY_KNOWN = 64,
    RS3_BAR_MODIFIER_KNOWN = 128,
    RS3_BAR_CHORD_KNOWN = 256
};

typedef struct rs3_interface_info
{
    uint32_t status;          /**< Action-bar acquisition status, enum rs3_status. */
    uint32_t reason;          /**< enum rs3_interface_reason. */
    uint32_t interface_count; /**< Required-interface observations, not an open-list count. */
    uint32_t action_bar_count;
    uint32_t open_complete;       /**< 0 until the open-interface registry is mapped. */
    uint32_t action_bar_complete; /**< All slot sources read; not complete key coverage. */
    uint32_t reserved[2];
} rs3_interface_info;

/**
 * Unknown open state is -1. Unknown interface ID is -1, not interface zero.
 * BANK_PRESET currently reports the labelled bank-root attachment (517), not
 * preset-button visibility, enabled state, option text or a click position.
 * ENRAGE reports interface 720 only with its exact initial-enrage heading;
 * this generic interface must not be classified from ID alone. A zero means
 * the mapped page is not attached, not that every setup page is closed.
 */
typedef struct rs3_interface_observation
{
    uint32_t kind;
    uint32_t reason;
    int32_t interface_id;
    int32_t open;
} rs3_interface_observation;

/**
 * One physical display row's slot (all numbers one-based). preset_bar is the
 * assigned stored preset, not the keybinding row. content_type/content_id are
 * the native packed fields; ability_struct_id is set only for an unambiguous
 * compact metadata match. key_code/modifier_code are game codes, not Win32 VK.
 * Only CHORD_KNOWN permits interpreting key_modifiers (2 = Ctrl); at present
 * only F1..F12 with explicit no modifier or Ctrl are translated. Other keys
 * remain observable raw codes. Caller must check freshness and identity too.
 * Every raw field requires its corresponding flag. No addresses escape.
 */
typedef struct rs3_action_bar_slot
{
    uint32_t display_bar;
    uint32_t preset_bar;
    uint32_t slot;
    uint32_t flags;
    uint32_t reason;
    uint32_t source_var;
    uint32_t object_var;
    uint32_t key_var;
    uint32_t modifier_var;
    uint32_t content_type;
    int32_t content_id;
    int32_t item_id;
    int32_t ability_struct_id;
    uint32_t key_code;
    uint32_t modifier_code;
    uint32_t key_modifiers;
    uint32_t binding_reason;
    uint32_t reserved;
    int64_t raw_packed;
    int64_t raw_object;
    int64_t raw_key_packed;
    int64_t raw_modifier_packed;
    char name[RS3_INTERFACE_NAME_BYTES]; /**< UTF-8, always terminated. */
} rs3_action_bar_slot;

/** 1=info, 2=interface observation, 3=slot, 4=raw pointer candidate; unknown IDs return 0. */
RS3_API uint32_t rs3_interface_sizeof(uint32_t structure);
/** Copy raw unvalidated menu data from one owned snapshot; it is never a click hit-test. */
RS3_API int32_t rs3_snapshot_pointer_candidate(const rs3_snapshot *snapshot,
                                               rs3_pointer_candidate *out, uint32_t out_size);
RS3_API int32_t rs3_snapshot_interface_info(const rs3_snapshot *snapshot, rs3_interface_info *out,
                                            uint32_t out_size);
/** Copy required-interface observations; unknown entries do not mean closed. */
RS3_API int32_t rs3_snapshot_interfaces(const rs3_snapshot *snapshot,
                                        rs3_interface_observation *out, uint32_t capacity,
                                        uint32_t *required);
/** Copy display slots; insufficient capacity returns RS3_E_BUFFER without a partial copy. */
RS3_API int32_t rs3_snapshot_action_bar(const rs3_snapshot *snapshot, rs3_action_bar_slot *out,
                                        uint32_t capacity, uint32_t *required);

#ifdef __cplusplus
}
#endif
#endif
