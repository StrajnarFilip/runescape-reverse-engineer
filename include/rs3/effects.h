/* Optional player-effect observations. All output is owned by the caller. */
#ifndef RS3_PUBLIC_EFFECTS_H
#define RS3_PUBLIC_EFFECTS_H

#include "rs3.h"

#ifdef __cplusplus
extern "C"
{
#endif

#define RS3_EFFECT_COUNT 14u

/** Stable identities; these are reader IDs, not game cache IDs. */
enum rs3_effect_id
{
    RS3_EFFECT_BERSERK = 1,
    RS3_EFFECT_BLOODLUST,
    RS3_EFFECT_OVERLOAD,
    RS3_EFFECT_ADRENALINE_RENEWAL,
    RS3_EFFECT_ANIMA_VIRUS,
    RS3_EFFECT_STUN,
    RS3_EFFECT_BIND,
    RS3_EFFECT_RESONANCE,
    RS3_EFFECT_ANTICIPATION,
    RS3_EFFECT_FREEDOM,
    RS3_EFFECT_DEVOTION,
    RS3_EFFECT_BARRICADE,
    RS3_EFFECT_IMMORTALITY,
    RS3_EFFECT_DISRUPTION_SHIELD
};

enum rs3_effect_source
{
    RS3_EFFECT_SOURCE_UNMAPPED = 0,
    RS3_EFFECT_SOURCE_VARPLAYER = 1,
    RS3_EFFECT_SOURCE_VARCLIENT = 2
};

/** Independent of enum rs3_reason: the candidates can be readable but unvalidated. */
enum rs3_effect_reason
{
    RS3_EFFECT_CONFIRMED = 0,
    RS3_EFFECT_UNMAPPED = 1,
    RS3_EFFECT_UNVALIDATED = 2,
    RS3_EFFECT_NOT_SET = 3,
    RS3_EFFECT_READ_FAILED = 4,
    RS3_EFFECT_UNSUPPORTED_PROFILE = 5,
    RS3_EFFECT_FRAME_UNAVAILABLE = 6,
    RS3_EFFECT_PRESENCE_UNMAPPED = 7
};

enum rs3_player_effect_flags
{
    RS3_EFFECT_ACTIVE_KNOWN = 1,
    RS3_EFFECT_STACKS_KNOWN = 2,
    RS3_EFFECT_REMAINING_KNOWN = 4
};

/**
 * One effect and its research candidates. Semantic values require their flags.
 * raw_valid_mask bits 0/1/2 validate raw_expiry/raw_stacks/raw_active only.
 * Candidate variable names do not establish units, active state or absence.
 * active is -1 unknown, 0 absent, 1 active. No bit implies a missing sparse
 * variable is zero. raw_active is the whole variable, not an extracted varbit.
 */
typedef struct rs3_effect
{
    uint32_t effect_id;
    uint32_t flags;
    uint32_t reason;
    uint32_t source;     /**< enum rs3_effect_source for all three candidates. */
    uint32_t expiry_var; /**< 0 means unmapped. */
    uint32_t stacks_var;
    uint32_t active_var;
    uint32_t raw_valid_mask;
    int64_t raw_expiry;
    int64_t raw_stacks;
    int64_t raw_active;
    int32_t active;
    int32_t stacks;
    int32_t remaining_ms;
    uint32_t reserved;
} rs3_effect;

typedef struct rs3_effects_info
{
    uint32_t status;   /**< enum rs3_status. */
    uint32_t reason;   /**< enum rs3_effect_reason. */
    uint32_t count;    /**< Prefix returned by rs3_snapshot_effects(). */
    uint32_t complete; /**< 0: this is a requested set, not the entire buff list. */
} rs3_effects_info;

/** Structure 1 is rs3_effects_info, 2 is rs3_effect; unknown IDs return 0. */
RS3_API uint32_t rs3_effects_sizeof(uint32_t structure);
/** Copy the current observation metadata; out_size must be at least sizeof(*out). */
RS3_API int32_t rs3_snapshot_effects_info(const rs3_snapshot *snapshot, rs3_effects_info *out,
                                          uint32_t out_size);
/**
 * Copy the count-entry prefix. Always sets required for valid arguments;
 * RS3_E_BUFFER leaves output untouched. No pointers into game memory escape.
 */
RS3_API int32_t rs3_snapshot_effects(const rs3_snapshot *snapshot, rs3_effect *out,
                                     uint32_t capacity, uint32_t *required);

#ifdef __cplusplus
}
#endif
#endif
