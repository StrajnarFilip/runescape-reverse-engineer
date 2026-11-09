#ifndef RS3_INVENTORY_INTERNAL_H
#define RS3_INVENTORY_INTERNAL_H

#include "internal.h"

#define ITEM_QUEUE_CAPACITY 1024u

struct inventory_state {
    unsigned char *catalog;
    size_t catalog_size;
    uint32_t item_count, ability_count, strings_size, catalog_status;
    // Views into the validated catalog backing buffer.
    const unsigned char *item_defs, *ability_defs;
    const char *strings;
    char catalog_error[128];
    rs3_item previous[RS3_MAX_ITEMS];
    uint32_t previous_valid;
    uint64_t world, manager, staged_manager, token, sequence;
    rs3_item_event events[ITEM_QUEUE_CAPACITY];
    // Scratch storage for the registry's initial read and consistency check.
    unsigned char entries[4096 * 72], checked_entries[4096 * 72];
    // Timer staging indexed by variable ID, followed by the page cache and
    // selected nodes needed for the second pass.
    unsigned char wanted[65536], present[65536];
    int64_t values[65536];
    uint64_t page_bases[128], selected_addresses[4096];
    uint32_t selected_ids[4096];
    unsigned char selected_types[4096], pages[128][4096];
};

/* Also exercised by the deterministic fixtures. */
int digest_bytes(const unsigned char *data, uint32_t size, unsigned char digest[32]);
int catalog_load_wide(inventory_state *inventory, const wchar_t *path);
int same_item(const rs3_item *before, const rs3_item *after, int dose_change);
void inventory_event(rs3_context *c, rs3_snapshot *s, uint32_t kind,
                     const rs3_item *before, const rs3_item *after);

#endif
