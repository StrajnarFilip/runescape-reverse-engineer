/* Additive encounter observations; unknown semantics never become decoded values. */
#ifndef RS3_ENCOUNTER_H
#define RS3_ENCOUNTER_H

#include "rs3.h"

#ifdef __cplusplus
extern "C"
{
#endif

#define RS3_ENCOUNTER_RAW_CAPACITY 6u

enum rs3_encounter_reason
{
    RS3_ENCOUNTER_UNMAPPED = 100,
    RS3_ENCOUNTER_NOT_ACTIVE,
    RS3_ENCOUNTER_TICK_BASELINE,
    RS3_ENCOUNTER_TICK_STALLED,
    RS3_ENCOUNTER_TICK_RESET
};

/** A mapped field. value is meaningful only when valid is nonzero. */
typedef struct rs3_encounter_value
{
    int64_t value;
    uint64_t observed_ns;
    uint32_t valid;
    uint32_t reason;
    uint32_t source_varp;
    uint32_t source_varbit;
} rs3_encounter_value;

/** Raw research channel, separate from semantic fields. Missing sparse entries are unknown. */
typedef struct rs3_encounter_raw
{
    int64_t value;
    uint32_t varp_id;
    uint32_t valid;
    uint32_t reason;
    uint32_t reserved;
    uint64_t observed_ns;
} rs3_encounter_raw;

/**
 * Received server-tick counter, independent of the 20 ms client clock.
 * valid validates counter only. Non-finite phase_ms/uncertainty_ms mean that
 * server phase and its uncertainty are unknown; they must not drive deadlines.
 */
typedef struct rs3_game_tick
{
    uint64_t counter;
    uint64_t observed_ns;
    uint32_t valid;
    uint32_t reason;
    float phase_ms;
    float uncertainty_ms;
    uint32_t reserved[2];
} rs3_game_tick;

typedef struct rs3_encounter_state
{
    uint64_t sample_sequence;
    uint64_t observed_ns;
    uint64_t scene_epoch;
    uint64_t process_creation_time;
    uint32_t pid;
    uint32_t status;
    uint32_t reason;
    uint32_t raw_count;
    rs3_encounter_value phase;
    rs3_encounter_value enrage;
    rs3_encounter_value streak;
    rs3_encounter_value meter;
    rs3_encounter_value freedom;
    rs3_encounter_value stage_raw; /**< Known bit extraction, never a verified phase. */
    rs3_encounter_raw raw[RS3_ENCOUNTER_RAW_CAPACITY];
    rs3_game_tick game_tick;
} rs3_encounter_state;

enum rs3_encounter_structure
{
    RS3_ENCOUNTER_SIZE_VALUE = 1,
    RS3_ENCOUNTER_SIZE_RAW,
    RS3_ENCOUNTER_SIZE_GAME_TICK,
    RS3_ENCOUNTER_SIZE_STATE
};

/** Return an encounter structure's byte size, or zero. */
RS3_API uint32_t rs3_encounter_sizeof(uint32_t structure);
/**
 * Copy an owned snapshot's encounter state. Check validity, age and identity per field.
 * Current calibration covers the original profile, active Telos phases 1..4 and
 * the instance-enrage source observed at 300/500, plus phases 1..3 at 0% on Compatibility
 * Mode when transmitted HP corroborates the phase. The received tick counter is
 * independent of Telos presence. Server phase remains unknown.
 */
RS3_API int32_t rs3_snapshot_encounter(const rs3_snapshot *snapshot, rs3_encounter_state *out,
                                       uint32_t out_size);

#ifdef __cplusplus
}
#endif
#endif
