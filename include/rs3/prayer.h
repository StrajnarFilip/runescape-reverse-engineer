/**
 * @file prayer.h
 * @brief Deterministic prayer timing planner (additive ABI v1).
 *
 * The planner never accesses a process, starts threads, sleeps or sends input.
 * Every time is caller-supplied monotonic nanoseconds from ONE clock domain.
 * Calls on one planner must be serialized. Structures use default alignment.
 *
 * A proposal (RS3_PRAYER_SET) is distinct from accepted input, which the caller
 * reports with rs3_prayer_note_sent(), and from an observed in-game prayer.
 */
#ifndef RS3_PRAYER_H
#define RS3_PRAYER_H

#include "rs3.h"

#ifdef __cplusplus
extern "C"
{
#endif

/** Opaque planner state. */
typedef struct rs3_prayer_planner rs3_prayer_planner;

/** Prayer identifiers shared by the planner and the combat snapshot. */
enum rs3_prayer_id
{
    RS3_PRAYER_UNKNOWN = 0,    /**< The active overhead is not established. */
    RS3_PRAYER_NONE = 1,       /**< No overhead is active, or an unmitigable attack. */
    RS3_PRAYER_MELEE = 2,      /**< Protect from Melee or Deflect Melee. */
    RS3_PRAYER_MAGIC = 3,      /**< Protect from Magic or Deflect Magic. */
    RS3_PRAYER_RANGED = 4,     /**< Protect from Ranged or Deflect Ranged. */
    RS3_PRAYER_SOUL_SPLIT = 5, /**< Soul Split. */
    RS3_PRAYER_NECROMANCY = 6  /**< Protect from Necromancy or Deflect Necromancy. */
};

/** Bit for a prayer identifier in masks such as rs3_prayer_frame.available_mask. */
#define RS3_PRAYER_BIT(id) (1u << (id))
/** Mask of every overhead prayer from melee through necromancy. */
#define RS3_PRAYER_OVERHEADS 0x7cu
/** Maximum number of pending threats accepted by rs3_prayer_step(). */
#define RS3_PRAYER_MAX_THREATS 128u

/** Bits in rs3_prayer_frame.flags. */
enum rs3_prayer_frame_flags
{
    RS3_PRAYER_CONNECTED = 1,     /**< The frame comes from a verified, advancing client. */
    RS3_PRAYER_ACTIVE_KNOWN = 2,  /**< active_prayer was observed. */
    RS3_PRAYER_POINTS_KNOWN = 4,  /**< prayer_points was observed. */
    RS3_PRAYER_COVERAGE_KNOWN = 8 /**< coverage_until_ns is meaningful. */
};

/** Bits in rs3_prayer_threat.flags. */
enum rs3_prayer_threat_flags
{
    RS3_PRAYER_TIMING_VALIDATED = 1 /**< The check window was calibrated live. */
};

/** Values of rs3_prayer_decision.action. */
enum rs3_prayer_action
{
    RS3_PRAYER_BLOCKED = 0,  /**< No proposal is safe; see the reason. */
    RS3_PRAYER_HOLD = 1,     /**< Keep the current prayer. */
    RS3_PRAYER_SET = 2,      /**< Toggle the proposed prayer, then call note_sent(). */
    RS3_PRAYER_WAIT_ACK = 3, /**< Input was sent; wait for a newer observation. */
    RS3_PRAYER_FAULT = 4     /**< A latched fault; reset after re-observing the game. */
};

/** Values of rs3_prayer_decision.reason. Describe them with rs3_prayer_reason_string(). */
enum rs3_prayer_reason
{
    RS3_PRAYER_READY = 0,
    RS3_PRAYER_BAD_FRAME = 1,
    RS3_PRAYER_STALE = 2,
    RS3_PRAYER_DISCONNECTED = 3,
    RS3_PRAYER_ACTIVE_UNMAPPED = 4,
    RS3_PRAYER_NO_POINTS = 5,
    RS3_PRAYER_UNKNOWN_ATTACK = 6,
    RS3_PRAYER_CONFLICT = 7,
    RS3_PRAYER_UNAVAILABLE = 8,
    RS3_PRAYER_INCOMPLETE_COVERAGE = 9,
    RS3_PRAYER_ACK_PENDING = 10,
    RS3_PRAYER_ACK_TIMEOUT = 11,
    RS3_PRAYER_LATE = 12
};

/** Bits in rs3_prayer_decision.flags. */
enum rs3_prayer_decision_flags
{
    RS3_PRAYER_DEADLINE_MISSED = 1 /**< A protection check window is already open. */
};

/** Planner configuration. Every duration is in milliseconds, at most 60000. */
typedef struct rs3_prayer_config
{
    uint32_t struct_size;    /**< Must equal sizeof(rs3_prayer_config). */
    uint32_t idle_prayer;    /**< RS3_PRAYER_NONE (hold) or RS3_PRAYER_SOUL_SPLIT. */
    uint32_t lead_ms;        /**< Switch this long before a check window opens; nonzero. */
    uint32_t hold_ms;        /**< Keep protection this long after a window closes. */
    uint32_t min_idle_ms;    /**< Minimum useful idle-prayer interval. */
    uint32_t ack_timeout_ms; /**< Time allowed for an observed acknowledgement; nonzero. */
    uint32_t max_age_ms;     /**< Maximum observation age; nonzero. */
    uint32_t reserved;       /**< Must be zero. */
} rs3_prayer_config;

/** One observation of the game, supplied to every rs3_prayer_step() call. */
typedef struct rs3_prayer_frame
{
    uint64_t now_ns;      /**< Current time. */
    uint64_t observed_ns; /**< Time the observation was sampled; at most now_ns. */
    uint64_t session_id;  /**< Nonzero; change it on logout, world or process changes. */
    uint64_t sequence;    /**< Nonzero, non-decreasing observation sequence. */
    /**
     * Complete incoming-attack coverage through this time; not snapshot age.
     * Only meaningful with RS3_PRAYER_COVERAGE_KNOWN. Missing threats must
     * never imply a safe Soul Split window.
     */
    uint64_t coverage_until_ns;
    uint32_t flags;          /**< enum rs3_prayer_frame_flags. */
    uint32_t active_prayer;  /**< enum rs3_prayer_id. */
    uint32_t available_mask; /**< RS3_PRAYER_BIT() of each usable overhead. */
    uint32_t prayer_points;  /**< Raw prayer points; zero blocks every proposal. */
} rs3_prayer_frame;

/** One pending incoming attack. Supply the full pending list on every step. */
typedef struct rs3_prayer_threat
{
    uint64_t event_id;        /**< Nonzero and unique within one frame. */
    uint64_t source_instance; /**< Attacker instance, for diagnostics. */
    /** Earliest and latest time prayer might be CHECKED, not the hit mark display time. */
    uint64_t check_earliest_ns;
    uint64_t check_latest_ns;
    /** Protection that mitigates the attack; RS3_PRAYER_NONE when none can. */
    uint32_t prayer;
    uint32_t flags; /**< Must be RS3_PRAYER_TIMING_VALIDATED. */
} rs3_prayer_threat;

/** Planner output. */
typedef struct rs3_prayer_decision
{
    uint64_t command_id;        /**< Proposal ID for rs3_prayer_note_sent(); never reused. */
    uint64_t threat_id;         /**< Threat that selected the prayer, or 0. */
    uint64_t next_wakeup_ns;    /**< Earliest time a step can change the decision, or 0. */
    uint64_t check_earliest_ns; /**< Window of the selected threat. */
    uint64_t check_latest_ns;   /**< Window of the selected threat. */
    uint32_t action;            /**< enum rs3_prayer_action. */
    uint32_t prayer;            /**< enum rs3_prayer_id. */
    uint32_t reason;            /**< enum rs3_prayer_reason. */
    uint32_t flags;             /**< enum rs3_prayer_decision_flags. */
} rs3_prayer_decision;

/** Structure identifiers accepted by rs3_prayer_sizeof(). */
enum rs3_prayer_structure
{
    RS3_PRAYER_SIZE_CONFIG = 1,
    RS3_PRAYER_SIZE_FRAME,
    RS3_PRAYER_SIZE_THREAT,
    RS3_PRAYER_SIZE_DECISION
};

/** Returns the size of the structure identified by enum rs3_prayer_structure, or 0. */
RS3_API uint32_t rs3_prayer_sizeof(uint32_t structure);

/** Returns a borrowed snake_case description of an enum rs3_prayer_reason value. */
RS3_API const char *rs3_prayer_reason_string(uint32_t reason);

/** Creates a planner from a validated configuration. */
RS3_API int32_t rs3_prayer_create(const rs3_prayer_config *config, rs3_prayer_planner **out);

/** Releases a planner. NULL is safe. */
RS3_API void rs3_prayer_destroy(rs3_prayer_planner *planner);

/**
 * Clears pending input and latched faults. Use only after re-observing the game.
 * Command IDs are never reused, so older outstanding proposals remain invalid.
 */
RS3_API int32_t rs3_prayer_reset(rs3_prayer_planner *planner);

/** Computes the next decision for a frame and its complete pending-threat list. */
RS3_API int32_t rs3_prayer_step(rs3_prayer_planner *planner, const rs3_prayer_frame *frame,
                                const rs3_prayer_threat *threats, uint32_t threat_count,
                                rs3_prayer_decision *out, uint32_t out_size);

/**
 * Records that the input layer accepted the proposed key chord.
 *
 * This is not an in-game acknowledgement: a newer observed prayer must confirm
 * it. Until then no second toggle is proposed. A timeout latches a fault.
 */
RS3_API int32_t rs3_prayer_note_sent(rs3_prayer_planner *planner, uint64_t command_id,
                                     uint64_t sent_at_ns);

#ifdef __cplusplus
}
#endif

#endif /* RS3_PRAYER_H */
