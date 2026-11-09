#ifndef RS3_INTERNAL_H
#define RS3_INTERNAL_H

#include <windows.h>
#include <stddef.h>
#include <string.h>
#include "rs3/rs3.h"

#define EVENT_CAPACITY 4096u
#define RS3_TILE_LIMIT 12800

typedef struct {
    uint32_t rva, length;
    unsigned char bytes[64];
} profile_signature;

typedef struct {
    const char *sha256, *version, *short_version;
    const profile_signature *signatures;
    size_t signature_count;
    uint32_t registry_rva, player_handler_rva, client_handler_rva, client_vtable_rva;
    uint32_t npc_manager_vtable_rva, npc_entity_vtable_rva;
    int has_position;
} client_profile;

struct rs3_snapshot {
    rs3_info info;
    const client_profile *profile;
    rs3_player player;
    rs3_position position;
    rs3_npc npcs[RS3_MAX_NPCS];
    rs3_hit hits[RS3_MAX_HITS];
    rs3_inventory_info inventory_info;
    rs3_item items[RS3_MAX_ITEMS];
    rs3_ability abilities[RS3_MAX_ABILITIES];
};

typedef struct inventory_state inventory_state;
typedef struct npc_identity {
    uint64_t actor, instance;
    uint32_t uid, used;
    uint64_t valid_hits;
    rs3_hit previous[64];
} npc_identity;

/* The decoder uses the same bounded read path for live memory and test fixtures. */
typedef int (*read_callback)(void *user, uint64_t address, void *out, size_t size);

struct rs3_context {
    SRWLOCK control, latest_lock, events_lock;
    HANDLE process, worker, stop_event, timer;
    uint32_t requested_pid, pid, retry_ms, hz, attached, module_size;
    const client_profile *profile;
    uint64_t module_base, creation_time, retry_after, sequence, event_sequence;
    uint64_t calls, bytes, next_instance, clock_changed, clock_context;
    uint32_t last_clock, previous_valid;
    int64_t previous_values[3];
    uint64_t changed_at[3];
    char actual_sha256[72], error[192], retry_error[192];
    uint32_t reason, retry_reason;
    read_callback read;
    void *read_user;
    rs3_snapshot *working, *latest;
    rs3_event *events;
    npc_identity *identities;
    uint16_t identity_index[65536];
    uint64_t *buckets;
    size_t bucket_capacity;
    uint64_t npc_context, npc_state, actors[RS3_MAX_NPCS];
    inventory_state *inventory;
};

/* Client fields may be unaligned inside the bulk-read byte buffers. */
static inline uint32_t u32(const void *p) { uint32_t v; memcpy(&v,p,4); return v; }
static inline int32_t i32(const void *p) { int32_t v; memcpy(&v,p,4); return v; }
static inline uint64_t u64(const void *p) { uint64_t v; memcpy(&v,p,8); return v; }
/* Subtract before interpreting the sign so nearby ticks can span a 32-bit wrap. */
static inline int32_t tick_delta(uint32_t a,uint32_t b) { return (int32_t)(a-b); }
static inline int pointer_valid(uint64_t p) { return p >= 0x10000 && p < 0x7fffffffffffULL; }
static inline int valid_utf8(const char *s,uint32_t n) {
    return !n || MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s,(int)n,NULL,0)>0;
}

uint64_t monotonic_ns(void);
uint64_t unix_ns(void);
uint64_t process_creation_time(HANDLE process);
int fail(rs3_context *c, uint32_t reason, const char *message);
int windows_read(void *user, uint64_t address, void *out, size_t size);
int rd(rs3_context *c, uint64_t address, void *out, size_t size);
int ptr(rs3_context *c, uint64_t address, uint64_t *out);
int signatures(rs3_context *c, const char *hash);
int attach(rs3_context *c);
void detach(rs3_context *c);
void schedule_retry(rs3_context *c, uint64_t now);

int decode(rs3_context *c, rs3_snapshot *s);
int player_scene(rs3_context *c, uint64_t *client);
int decode_position(rs3_context *c, rs3_position *position);
int32_t project_minimap_tile(const rs3_position *, const rs3_minimap *,
    int32_t x, int32_t y, int32_t plane, POINT *out);
int capture(rs3_context *c, int client, uint64_t *out);
int variable(rs3_context *c, uint64_t table, uint64_t count, uint64_t sentinel,
             uint32_t id, int64_t *value, int *present);
void reset_identities(rs3_context *c);
int decode_hit(const unsigned char *bytes, uint32_t tick, uint32_t slot, rs3_hit *hit);
int npcs_once(rs3_context *c, rs3_snapshot *s, uint64_t state, uint32_t *ids,
              uint32_t *out_count, uint64_t *out_manager);
void commit_npcs(rs3_context *c, rs3_snapshot *s, const uint32_t *ids,
                 uint32_t count, uint64_t manager, uint64_t world_state);

inventory_state *inventory_create(void);
void inventory_free(inventory_state *inventory);
void inventory_reset(inventory_state *inventory);
void inventory_default_catalog(rs3_context *c);
void inventory_stage(rs3_context *c, rs3_snapshot *s, uint64_t client, uint64_t state);
void inventory_commit(rs3_context *c, rs3_snapshot *s, uint64_t world);

#endif
