/*
 * Internal definitions of the sampling context and the snapshot frame.
 *
 * One sampler at a time (rs3_sample() or the worker thread) owns the process,
 * decoder and identity state of a context. Three locks separate the shared
 * state:
 *
 *   lifecycle_lock     serializes create/start/stop/sample/load_catalog calls;
 *   latest_frame_lock  protects the published frame copied by consumers;
 *   event_queue_lock   protects the bounded observation queues.
 *
 * Consumers never touch the working frame. Remote addresses are dereferenced
 * only through memory_read(), which bounds and accounts every read.
 */
#ifndef RS3_CONTEXT_H
#define RS3_CONTEXT_H

#include <windows.h>

#include <stddef.h>
#include <stdint.h>

#include "rs3/rs3.h"
#include "rs3/combat.h"
#include "rs3/interface.h"
#include "rs3/spatial.h"
#include "rs3/hazards.h"
#include "rs3/encounter.h"
#include "rs3/effects.h"
#include "profile.h"

/** Capacity of the hit observation queue. */
#define HIT_EVENT_CAPACITY 4096u
/** Capacity of the NPC action event queue. */
#define NPC_ACTION_EVENT_CAPACITY 4096u
/** Capacity of the local-player hit event queue. */
#define PLAYER_HIT_EVENT_CAPACITY 4096u
/** Exclusive upper bound of valid tile coordinates. */
#define MAX_TILE_COORDINATE 12800
/** Capacity of one actor's hit mark ring. */
#define MAX_HITS_PER_RING 64u
/** Number of distinct NPC runtime IDs (16-bit). */
#define NPC_RUNTIME_ID_COUNT 65536u
/** Capacity of failure messages. */
#define FAILURE_MESSAGE_CAPACITY 192u
/** Player values whose last change time is tracked: health, prayer, adrenaline. */
#define TRACKED_PLAYER_VALUE_COUNT 3u

/**
 * Copies size bytes at a remote address into output.
 * Returns nonzero only when every byte was copied. The decoder uses the same
 * callback for live process memory and for deterministic test fixtures.
 */
typedef int (*memory_reader)(void *user_data, uint64_t address, void *output, size_t size);

typedef struct inventory_state inventory_state;

/** One published frame. Array prefixes are defined by the counts in info and inventory_info. */
struct rs3_snapshot
{
    rs3_hazard_info hazard_info;
    rs3_hazard hazards[RS3_MAX_HAZARDS];
    rs3_info info;
    const client_profile *profile;
    rs3_player player;
    rs3_interface_info interface_info;
    rs3_interface_observation interfaces[RS3_REQUIRED_INTERFACES];
    rs3_action_bar_slot action_bar[RS3_MAX_ACTION_BAR_SLOTS];
    rs3_effects_info effects_info;
    rs3_effect effects[RS3_EFFECT_COUNT];
    rs3_position position;
    rs3_combat_state combat;
    rs3_spatial_info spatial;
    rs3_actor_spatial npc_spatial[RS3_MAX_NPCS];
    rs3_encounter_state encounter;
    rs3_player_hit_info player_hit_info;
    rs3_hit player_hits[MAX_HITS_PER_RING];         /**< Prefix of player_hit_info.hit_count. */
    uint64_t player_actor_address;                  /**< Verified local actor, or 0. */
    rs3_npc_animation npc_animations[RS3_MAX_NPCS]; /**< Parallel to npcs. */
    rs3_npc npcs[RS3_MAX_NPCS];
    rs3_hit hits[RS3_MAX_HITS];
    rs3_inventory_info inventory_info;
    rs3_item items[RS3_MAX_ITEMS];
    rs3_ability abilities[RS3_MAX_ABILITIES];
};

/** Local identity of one NPC actor across samples. */
typedef struct npc_identity
{
    uint64_t actor_address;
    uint64_t instance_id;
    uint32_t runtime_id;
    uint32_t in_use;
    /** Bit i validates last_reported_hits[i]. */
    uint64_t reported_hit_slots;
    /** Last hit record reported for each ring slot, used for deduplication. */
    rs3_hit last_reported_hits[MAX_HITS_PER_RING];
    /** Sample, scene and action state of the previous valid animation sample. */
    uint64_t action_sample_sequence;
    uint64_t action_scene_epoch;
    int32_t action_sequence;
    uint32_t action_stamp;
} npc_identity;

/** Tracks when health, prayer and adrenaline last changed. */
typedef struct player_change_tracker
{
    uint64_t clock_world_state;
    uint32_t last_logic_tick;
    uint64_t clock_changed_ns;
    uint32_t valid_mask; /**< Bit i validates values[i]. */
    int64_t values[TRACKED_PLAYER_VALUE_COUNT];
    uint64_t changed_ns[TRACKED_PLAYER_VALUE_COUNT];
} player_change_tracker;

/** Identity and deduplication state of the local player's hit mark ring. */
typedef struct player_hit_tracker
{
    /** Actor, scene and process that instance_id belongs to. */
    uint64_t actor_address;
    uint64_t scene_epoch;
    uint64_t process_creation_time;
    uint32_t player_index;
    uint64_t instance_id;
    /** Last committed sample; 0 means there is no adjacent baseline. */
    uint64_t sample_sequence;
    /** Bit i validates last_reported_hits[i]. */
    uint64_t reported_hit_slots;
    rs3_hit last_reported_hits[MAX_HITS_PER_RING];
} player_hit_tracker;

/** NPC identities and per-frame staging that must survive until the frame commits. */
typedef struct npc_tracker
{
    npc_identity *identities; /**< RS3_MAX_NPCS slots. */
    /** One-based slot index of each runtime ID; zero means untracked. */
    uint16_t identity_slot_by_runtime_id[NPC_RUNTIME_ID_COUNT];
    uint64_t next_instance_id;
    /** Manager and world state that the identities belong to. */
    uint64_t registry_manager;
    uint64_t registry_world_state;
    /** Per-frame values indexed like rs3_snapshot.npcs. */
    uint64_t staged_actor_addresses[RS3_MAX_NPCS];
    uint32_t staged_action_stamps[RS3_MAX_NPCS];
} npc_tracker;

/** Identity and verification state of the attached client. */
typedef struct process_target
{
    uint32_t requested_pid; /**< 0 discovers a client automatically. */
    uint32_t pid;
    HANDLE process_handle;
    uint32_t attached;
    uint64_t module_base;
    uint32_t module_size;
    uint64_t process_creation_time;
    char executable_sha256[72];
    const client_profile *profile;
} process_target;

/** Back-off after a failed attachment. */
typedef struct retry_state
{
    uint32_t interval_ms;
    uint64_t retry_after_ns;
    uint32_t reason;
    char message[FAILURE_MESSAGE_CAPACITY];
} retry_state;

/** Native worker thread. */
typedef struct worker_state
{
    HANDLE thread;
    HANDLE stop_event;
    HANDLE timer;
    uint32_t hz;
} worker_state;

/** Scene epoch bookkeeping for the combat state. */
typedef struct combat_tracker
{
    uint64_t world_state;
    uint64_t process_creation_time;
    uint64_t scene_epoch;
} combat_tracker;

struct rs3_context
{
    SRWLOCK lifecycle_lock;
    SRWLOCK latest_frame_lock;
    SRWLOCK event_queue_lock;

    worker_state worker;
    process_target target;
    retry_state retry;

    /* Diagnostics of the sample in progress. */
    uint32_t failure_reason;
    char failure_message[FAILURE_MESSAGE_CAPACITY];
    uint64_t read_call_count;
    uint64_t read_byte_count;

    /* Remote memory access; replaced by fixtures in the tests. */
    memory_reader read_memory;
    void *read_memory_user_data;

    /* Frames. */
    uint64_t sample_sequence;
    rs3_snapshot *working_frame;
    rs3_snapshot *latest_frame;

    /* Scratch buffer for hash-table bucket arrays read in bulk. */
    uint64_t *bucket_buffer;
    size_t bucket_buffer_capacity;

    player_change_tracker player_changes;
    npc_tracker npcs;
    combat_tracker combat;

    /* Bounded observation queues, protected by event_queue_lock. */
    rs3_event *hit_events;
    uint64_t hit_event_sequence;
    rs3_npc_action_event *npc_action_events;
    uint64_t npc_action_event_sequence;
    player_hit_tracker player_hits;
    rs3_player_hit_event *player_hit_events;
    uint64_t player_hit_event_sequence;

    inventory_state *inventory;
};

/**
 * Records why the current sample failed and returns 0.
 * Callers use it as "return context_fail(...)" so failures propagate as false.
 */
int context_fail(rs3_context *context, uint32_t reason, const char *message);

/** Ensures the scratch bucket buffer holds at least bucket_count entries. */
int context_reserve_buckets(rs3_context *context, uint64_t bucket_count);

#endif /* RS3_CONTEXT_H */
