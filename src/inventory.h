/*
 * Backpack and equipment slots, inferred item identities and item events.
 *
 * Slots are staged into the working frame; inventory_commit() matches them to
 * the previous observation and publishes changes only after the frame passed
 * every consistency check.
 */
#ifndef RS3_INVENTORY_H
#define RS3_INVENTORY_H

#include <stdint.h>

#include "catalog.h"
#include "context.h"

#define ITEM_EVENT_CAPACITY      1024u
#define BACKPACK_SLOT_COUNT      28u
#define EQUIPMENT_SLOT_COUNT     19u
#define EQUIPMENT_SLOT_MAIN_HAND 3u
#define EQUIPMENT_SLOT_OFF_HAND  5u
/** Upper bound on the inventory registry entries copied per sample. */
#define MAX_INVENTORY_REGISTRY_ENTRIES 4096u
#define INVENTORY_REGISTRY_ENTRY_BYTES 72u

/* Ability timer staging, indexed by 16-bit variable ID. */
#define VARIABLE_ID_COUNT            65536u
#define TIMER_PAGE_BYTES             4096u
#define TIMER_PAGE_CACHE_SLOTS       128u
#define MAX_SELECTED_TIMER_VARIABLES 4096u

/** Previous observation and event queue used to infer item identities. */
typedef struct item_tracker
{
    rs3_item previous_items[RS3_MAX_ITEMS];
    uint32_t has_previous_items;
    /** World state and inventory manager that previous_items belong to. */
    uint64_t world_state;
    uint64_t manager_address;
    /** Manager read in the frame being decoded. */
    uint64_t staged_manager_address;
    uint64_t last_token;
    uint64_t event_sequence;
    rs3_item_event events[ITEM_EVENT_CAPACITY];
} item_tracker;

/** Scratch storage for the ability timer table walk. */
typedef struct ability_timer_scratch
{
    unsigned char wanted[VARIABLE_ID_COUNT];  /**< Variables referenced by the catalog. */
    unsigned char present[VARIABLE_ID_COUNT]; /**< Variables read consistently. */
    int64_t values[VARIABLE_ID_COUNT];
    /** Small direct-mapped cache of 4 KiB pages that contain variable nodes. */
    uint64_t page_bases[TIMER_PAGE_CACHE_SLOTS];
    unsigned char pages[TIMER_PAGE_CACHE_SLOTS][TIMER_PAGE_BYTES];
    /** Selected nodes, re-read in a second pass to reject torn or reused entries. */
    uint64_t selected_addresses[MAX_SELECTED_TIMER_VARIABLES];
    uint32_t selected_ids[MAX_SELECTED_TIMER_VARIABLES];
    unsigned char selected_types[MAX_SELECTED_TIMER_VARIABLES];
} ability_timer_scratch;

struct inventory_state
{
    item_catalog catalog;
    item_tracker items;
    /** Registry copy and its consistency re-read. */
    unsigned char registry_entries[MAX_INVENTORY_REGISTRY_ENTRIES * INVENTORY_REGISTRY_ENTRY_BYTES];
    unsigned char
        registry_entries_check[MAX_INVENTORY_REGISTRY_ENTRIES * INVENTORY_REGISTRY_ENTRY_BYTES];
    ability_timer_scratch timers;
};

/** Allocates an empty inventory state; returns NULL on allocation failure. */
inventory_state *inventory_create(void);

/** Releases the state and its catalog. NULL is safe. */
void inventory_destroy(inventory_state *inventory);

/** Forgets the previous observation so the next commit establishes a new baseline. */
void inventory_reset_tracking(inventory_state *inventory);

/** Stages slots, Bone Shield tier and abilities into the snapshot. */
void inventory_stage(rs3_context *context, rs3_snapshot *snapshot, uint64_t player_address,
                     uint64_t client_address, uint64_t world_state);

/** Assigns tracking tokens and publishes item events. */
void inventory_commit(rs3_context *context, rs3_snapshot *snapshot, uint64_t world_state);

/**
 * Returns nonzero when after can be the same physical item as before. With
 * allow_dose_change, a potion that lost exactly one dose also matches.
 */
int inventory_same_item(const rs3_item *before, const rs3_item *after, int allow_dose_change);

/** Appends one item event. Called with the event queue lock held. */
void inventory_publish_event(rs3_context *context, const rs3_snapshot *snapshot, uint32_t kind,
                             const rs3_item *before, const rs3_item *after);

#endif /* RS3_INVENTORY_H */
