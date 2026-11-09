#ifndef RS3_PRAYER_H
#define RS3_PRAYER_H
#include "rs3.h"
#ifdef __cplusplus
extern "C" {
#endif

/** Deterministic prayer planner. No process access, threads, sleeps or input.
 * All times are caller-supplied monotonic nanoseconds in ONE clock domain.
 * Calls on one planner must be serialized. Structures use default alignment.
 */
typedef struct rs3_prayer_planner rs3_prayer_planner;
enum rs3_prayer_id { RS3_PRAYER_UNKNOWN=0, RS3_PRAYER_NONE=1,
    RS3_PRAYER_MELEE=2, RS3_PRAYER_MAGIC=3, RS3_PRAYER_RANGED=4,
    RS3_PRAYER_SOUL_SPLIT=5, RS3_PRAYER_NECROMANCY=6 };
#define RS3_PRAYER_BIT(id) (1u << (id))
#define RS3_PRAYER_OVERHEADS 0x7cu
#define RS3_PRAYER_MAX_THREATS 128u

enum rs3_prayer_frame_flags { RS3_PRAYER_CONNECTED=1, RS3_PRAYER_ACTIVE_KNOWN=2,
    RS3_PRAYER_POINTS_KNOWN=4, RS3_PRAYER_COVERAGE_KNOWN=8 };
enum rs3_prayer_threat_flags { RS3_PRAYER_TIMING_VALIDATED=1 };
enum rs3_prayer_action { RS3_PRAYER_BLOCKED=0, RS3_PRAYER_HOLD=1,
    RS3_PRAYER_SET=2, RS3_PRAYER_WAIT_ACK=3, RS3_PRAYER_FAULT=4 };
enum rs3_prayer_reason { RS3_PRAYER_READY=0, RS3_PRAYER_BAD_FRAME=1,
    RS3_PRAYER_STALE=2, RS3_PRAYER_DISCONNECTED=3, RS3_PRAYER_ACTIVE_UNMAPPED=4,
    RS3_PRAYER_NO_POINTS=5, RS3_PRAYER_UNKNOWN_ATTACK=6,
    RS3_PRAYER_CONFLICT=7, RS3_PRAYER_UNAVAILABLE=8,
    RS3_PRAYER_INCOMPLETE_COVERAGE=9, RS3_PRAYER_ACK_PENDING=10,
    RS3_PRAYER_ACK_TIMEOUT=11, RS3_PRAYER_LATE=12 };
enum rs3_prayer_decision_flags { RS3_PRAYER_DEADLINE_MISSED=1 };

typedef struct rs3_prayer_config {
    uint32_t struct_size, idle_prayer; /**< NONE or SOUL_SPLIT. NONE means hold. */
    uint32_t lead_ms, hold_ms, min_idle_ms, ack_timeout_ms, max_age_ms, reserved;
} rs3_prayer_config;

typedef struct rs3_prayer_frame {
    uint64_t now_ns, observed_ns, session_id, sequence;
    /** Complete incoming-attack coverage through this time; not snapshot age.
     * Only meaningful with COVERAGE_KNOWN. Missing threats must never imply
     * a safe Soul Split window. Change session_id on logout/world/process changes.
     */
    uint64_t coverage_until_ns;
    uint32_t flags, active_prayer, available_mask, prayer_points;
} rs3_prayer_frame;

typedef struct rs3_prayer_threat {
    uint64_t event_id, source_instance;
    /** Earliest/latest time prayer might be CHECKED, not hitsplat display time.
     * Supply the full pending list each step. IDs must be unique within a frame.
     * NONE represents a known attack that protection prayers cannot mitigate.
     */
    uint64_t check_earliest_ns, check_latest_ns;
    uint32_t prayer, flags;
} rs3_prayer_threat;

typedef struct rs3_prayer_decision {
    uint64_t command_id, threat_id, next_wakeup_ns, check_earliest_ns, check_latest_ns;
    uint32_t action, prayer, reason, flags;
} rs3_prayer_decision;

enum rs3_prayer_structure { RS3_PRAYER_SIZE_CONFIG=1, RS3_PRAYER_SIZE_FRAME,
    RS3_PRAYER_SIZE_THREAT, RS3_PRAYER_SIZE_DECISION };
RS3_API uint32_t rs3_prayer_sizeof(uint32_t structure);
RS3_API const char *rs3_prayer_reason_string(uint32_t reason);
RS3_API int32_t rs3_prayer_create(const rs3_prayer_config *, rs3_prayer_planner **out);
RS3_API void rs3_prayer_destroy(rs3_prayer_planner *);
/** Clears pending input/latched faults. Use only after re-observing game state.
 * Command IDs are not reused, so old outstanding proposals remain invalid.
 */
RS3_API int32_t rs3_prayer_reset(rs3_prayer_planner *);
RS3_API int32_t rs3_prayer_step(rs3_prayer_planner *, const rs3_prayer_frame *,
    const rs3_prayer_threat *, uint32_t count, rs3_prayer_decision *, uint32_t out_size);
/** Call ONLY after the proposed key chord was accepted by the input layer.
 * This is not an in-game acknowledgement. A newer observed prayer must confirm
 * it. Until then no second toggle is proposed. Timeout latches a fault.
 */
RS3_API int32_t rs3_prayer_note_sent(rs3_prayer_planner *, uint64_t command_id,
    uint64_t sent_at_ns);
#ifdef __cplusplus
}
#endif
#endif
