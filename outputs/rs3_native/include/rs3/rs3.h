#ifndef RS3_NATIVE_H
#define RS3_NATIVE_H

/** @file rs3.h
 * Read-only RS3 sampling ABI, version 1, Windows x64, C calling convention.
 * All strings are UTF-8. All public structs use the platform's default 8-byte
 * alignment. Call rs3_sizeof() to verify layouts in a foreign-language binding.
 * Never free library objects with a foreign allocator: use the matching destroy.
 */
#include <stdint.h>
#if defined(_WIN32) && defined(RS3_BUILD)
#define RS3_API __declspec(dllexport)
#elif defined(_WIN32) && !defined(RS3_STATIC)
#define RS3_API __declspec(dllimport)
#else
#define RS3_API
#endif
#ifdef __cplusplus
extern "C" {
#endif

#define RS3_ABI_VERSION 1u
#define RS3_VARIABLE_COUNT 10u
#define RS3_MAX_NPCS 1024u
#define RS3_MAX_HITS 65536u
#define RS3_NAME_CAPACITY 520u

typedef struct rs3_context rs3_context;
typedef struct rs3_snapshot rs3_snapshot;
enum rs3_result { RS3_OK=0, RS3_E_ARGUMENT=-1, RS3_E_MEMORY=-2,
    RS3_E_ABI=-3, RS3_E_BUFFER=-4, RS3_E_STATE=-5, RS3_E_OS=-6 };
enum rs3_status { RS3_NO_SAMPLE=0, RS3_AVAILABLE=1, RS3_PARTIAL=2, RS3_UNAVAILABLE=3 };
enum rs3_reason { RS3_REASON_NONE=0, RS3_NOT_SET=1, RS3_READ_FAILED=2,
    RS3_UNSUPPORTED_CLIENT=3, RS3_NO_CLIENT=4, RS3_MULTIPLE_CLIENTS=5,
    RS3_INCONSISTENT_CLOCK=6, RS3_NOT_TRANSMITTED=7, RS3_INVALID_STATS=8 };
enum rs3_variable { RS3_HEALTH=0, RS3_MAX_HEALTH, RS3_PRAYER_PACKED, RS3_ADRENALINE,
    RS3_GCD_START, RS3_GCD_END, RS3_GCD_DURATION, RS3_GCD_CLIENT_START,
    RS3_GCD_CLIENT_END, RS3_TELOS_PACKED };
enum rs3_npc_flags { RS3_NPC_NAME=1, RS3_NPC_CONFIG=2, RS3_NPC_HEALTH=4 };
enum rs3_hit_state { RS3_HIT_ACTIVE=1, RS3_HIT_SCHEDULED=2 };
enum rs3_structure { RS3_SIZE_OPTIONS=1, RS3_SIZE_INFO, RS3_SIZE_PLAYER,
    RS3_SIZE_NPC, RS3_SIZE_HIT, RS3_SIZE_EVENT };

typedef struct rs3_options {
    uint32_t struct_size, pid, retry_ms, reserved;
} rs3_options;

typedef struct rs3_info {
    uint32_t abi_version, status, reason, pid;
    uint64_t sequence, timestamp_ns, monotonic_ns, sample_duration_ns;
    uint64_t process_creation_time, event_sequence, read_calls, read_bytes;
    uint32_t logic_tick, loaded_npcs, npc_count, skipped_npcs, hit_count, reserved;
    char executable_sha256[72];
    char client_version[16];
    char message[192];
} rs3_info;

typedef struct rs3_player {
    uint64_t valid_variables; /**< Bit i means raw_variables[i] is available. */
    int64_t raw_variables[RS3_VARIABLE_COUNT];
    uint64_t hp_changed_ns, prayer_changed_ns, adrenaline_changed_ns, clock_stalled_ns;
    int32_t prayer_base_level, gcd_remaining_ms, gcd_duration_ms;
    uint32_t gcd_reason;
} rs3_player;

typedef struct rs3_npc {
    uint64_t instance_id;
    uint32_t runtime_id;
    int32_t config_id;
    uint32_t flags, health_reason;
    int32_t health_current, health_max;
    uint32_t hit_offset, hit_count, name_length, reserved;
    char name[RS3_NAME_CAPACITY];
} rs3_npc;

typedef struct rs3_hit {
    uint32_t slot;
    int32_t type_id, amount, secondary_type_id, secondary_amount;
    uint32_t start_tick, end_tick, duration_ticks, state, reserved;
} rs3_hit;

typedef struct rs3_event {
    uint64_t sequence, observed_at_ns, process_creation_time, instance_id;
    uint32_t pid, runtime_id;
    int32_t config_id;
    uint32_t name_length;
    rs3_hit hit;
    char name[RS3_NAME_CAPACITY];
} rs3_event;

RS3_API uint32_t rs3_abi_version(void);
/** Native QPC time base, nanoseconds. Use this for snapshot age; a foreign
 * runtime's monotonic clock may have a different origin. Not wall time. */
RS3_API uint64_t rs3_monotonic_ns(void);
RS3_API uint32_t rs3_sizeof(uint32_t structure);
RS3_API const char *rs3_profile_sha256(void); /**< Borrowed immutable string. */
RS3_API const char *rs3_result_string(int32_t result); /**< Borrowed immutable string. */
/** pid=0 discovers the unique rs2client.exe. No client is not a create error.
 * options=NULL selects defaults. reserved must be zero. retry_ms=0 means 1000.
 */
RS3_API int32_t rs3_create(const rs3_options *options, rs3_context **out);
/** Stops/joins a running worker and closes the read-only process handle.
 * Caller must stop other API calls before destroying the context. NULL is safe.
 */
RS3_API void rs3_destroy(rs3_context *context);
RS3_API int32_t rs3_snapshot_create(rs3_snapshot **out);
RS3_API void rs3_snapshot_destroy(rs3_snapshot *snapshot);
/** Synchronous sample. Returns E_STATE while the native worker is running.
 * OK means an observation was produced: inspect info.status for availability.
 */
RS3_API int32_t rs3_sample(rs3_context *context, rs3_snapshot *out);
/** Native worker: 1..1000 requested polls/sec. UI/FFI polling is independent.
 * Uses a waitable timer; requested rate is not a guarantee under system load.
 */
RS3_API int32_t rs3_start(rs3_context *context, uint32_t hz);
RS3_API int32_t rs3_stop(rs3_context *context); /**< Joins; idempotent. */
/** Copies the latest immutable sample; does not resample the game. */
RS3_API int32_t rs3_copy_latest(rs3_context *context, rs3_snapshot *out);
RS3_API int32_t rs3_snapshot_info(const rs3_snapshot *, rs3_info *out, uint32_t out_size);
RS3_API int32_t rs3_snapshot_player(const rs3_snapshot *, rs3_player *out, uint32_t out_size);
/** Arrays are caller-owned. BUFFER reports required count and writes no records. */
RS3_API int32_t rs3_snapshot_npcs(const rs3_snapshot *, rs3_npc *out, uint32_t capacity, uint32_t *required);
RS3_API int32_t rs3_snapshot_hits(const rs3_snapshot *, rs3_hit *out, uint32_t capacity, uint32_t *required);
/** Independent consumer cursor, initially zero. next is the last returned seq.
 * A bounded native event queue preserves observations between slow UI polls.
 * lost reports overwritten sequence numbers; caller must explicitly handle gaps.
 * This records first observation, including already-active hits on attachment.
 * Polling cannot recover game records overwritten between native samples.
 */
RS3_API int32_t rs3_events_since(rs3_context *, uint64_t after, rs3_event *out,
    uint32_t capacity, uint32_t *written, uint64_t *next, uint64_t *lost);

#ifdef __cplusplus
}
#endif
#endif
