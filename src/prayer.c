/*
 * Deterministic prayer timing planner.
 *
 * A proposal (RS3_PRAYER_SET) is distinct from input accepted by Windows,
 * which the caller reports with rs3_prayer_note_sent(), and from the prayer
 * observed in game, which must confirm that input in a newer frame.
 */
#include "rs3/prayer.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define NANOSECONDS_PER_MILLISECOND 1000000ull
#define MAX_CONFIGURED_MS           60000u
#define VALID_FRAME_FLAGS                                                                          \
    ((uint32_t)(RS3_PRAYER_CONNECTED | RS3_PRAYER_ACTIVE_KNOWN | RS3_PRAYER_POINTS_KNOWN |         \
                RS3_PRAYER_COVERAGE_KNOWN))

struct rs3_prayer_planner
{
    rs3_prayer_config config;
    uint64_t session_id;
    uint64_t last_now_ns;
    uint64_t last_sequence;
    uint64_t last_command_id;     /**< Never reset, so command IDs are never reused. */
    uint64_t proposed_command_id; /**< Outstanding proposal, or 0. */
    uint64_t proposed_at_ns;
    uint32_t proposed_prayer;
    uint32_t input_pending; /**< Nonzero after note_sent() until acknowledged. */
    uint64_t sent_at_ns;
    uint64_t sent_sequence;
    uint32_t latched_fault; /**< enum rs3_prayer_reason, or RS3_PRAYER_READY. */
};

static uint64_t milliseconds_to_ns(uint32_t milliseconds)
{
    return (uint64_t)milliseconds * NANOSECONDS_PER_MILLISECOND;
}

static uint64_t saturating_add(uint64_t left, uint64_t right)
{
    return UINT64_MAX - left < right ? UINT64_MAX : left + right;
}

static uint64_t saturating_subtract(uint64_t left, uint64_t right)
{
    return left > right ? left - right : 0;
}

static int is_valid_prayer(uint32_t prayer)
{
    return prayer >= RS3_PRAYER_NONE && prayer <= RS3_PRAYER_NECROMANCY;
}

/** Returns nonzero for the overheads that mitigate an attack style. */
static int is_protection_prayer(uint32_t prayer)
{
    return prayer >= RS3_PRAYER_MELEE && prayer <= RS3_PRAYER_NECROMANCY &&
           prayer != RS3_PRAYER_SOUL_SPLIT;
}

/** Returns RS3_PRAYER_READY or the reason the threat list cannot be trusted. */
static uint32_t validate_threats(const rs3_prayer_threat *threats, uint32_t threat_count)
{
    for (uint32_t index = 0; index < threat_count; index++)
    {
        const rs3_prayer_threat *threat = &threats[index];
        if (threat->event_id == 0 || threat->check_earliest_ns > threat->check_latest_ns ||
            threat->flags != RS3_PRAYER_TIMING_VALIDATED || !is_valid_prayer(threat->prayer) ||
            threat->prayer == RS3_PRAYER_SOUL_SPLIT)
        {
            return RS3_PRAYER_UNKNOWN_ATTACK;
        }
        for (uint32_t earlier = 0; earlier < index; earlier++)
        {
            if (threats[earlier].event_id == threat->event_id)
            {
                return RS3_PRAYER_BAD_FRAME;
            }
        }
    }
    return RS3_PRAYER_READY;
}

/** Withdraws any proposal; latches a fault while input is still unacknowledged. */
static int32_t block(rs3_prayer_planner *planner, rs3_prayer_decision *decision, uint32_t reason)
{
    if (planner->input_pending)
    {
        planner->latched_fault = reason;
    }
    planner->proposed_command_id = 0;
    decision->action = planner->latched_fault ? RS3_PRAYER_FAULT : RS3_PRAYER_BLOCKED;
    decision->reason = reason;
    return RS3_OK;
}

/** Returns the end of a threat's protection window, including the hold time. */
static uint64_t protection_end_ns(const rs3_prayer_planner *planner,
                                  const rs3_prayer_threat *threat)
{
    return saturating_add(threat->check_latest_ns, milliseconds_to_ns(planner->config.hold_ms));
}

uint32_t rs3_prayer_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_PRAYER_SIZE_CONFIG:
            return sizeof(rs3_prayer_config);
        case RS3_PRAYER_SIZE_FRAME:
            return sizeof(rs3_prayer_frame);
        case RS3_PRAYER_SIZE_THREAT:
            return sizeof(rs3_prayer_threat);
        case RS3_PRAYER_SIZE_DECISION:
            return sizeof(rs3_prayer_decision);
        default:
            return 0;
    }
}

const char *rs3_prayer_reason_string(uint32_t reason)
{
    static const char *const reason_names[] = {
        "ready",
        "bad_frame",
        "stale_observation",
        "disconnected",
        "active_prayer_unknown",
        "prayer_points_unavailable_or_empty",
        "unvalidated_attack",
        "incompatible_protection_windows",
        "prayer_unavailable",
        "incomplete_attack_coverage",
        "awaiting_game_acknowledgement",
        "acknowledgement_timeout",
        "protection_deadline_missed",
    };
    if (reason < sizeof reason_names / sizeof reason_names[0])
    {
        return reason_names[reason];
    }
    return "unknown_reason";
}

int32_t rs3_prayer_create(const rs3_prayer_config *config, rs3_prayer_planner **out)
{
    if (out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    *out = NULL;
    if (config == NULL || config->struct_size != sizeof *config || config->reserved != 0 ||
        (config->idle_prayer != RS3_PRAYER_NONE && config->idle_prayer != RS3_PRAYER_SOUL_SPLIT) ||
        config->lead_ms == 0 || config->ack_timeout_ms == 0 || config->max_age_ms == 0 ||
        config->lead_ms > MAX_CONFIGURED_MS || config->hold_ms > MAX_CONFIGURED_MS ||
        config->min_idle_ms > MAX_CONFIGURED_MS || config->ack_timeout_ms > MAX_CONFIGURED_MS ||
        config->max_age_ms > MAX_CONFIGURED_MS)
    {
        return RS3_E_ARGUMENT;
    }
    rs3_prayer_planner *planner = calloc(1, sizeof *planner);
    if (planner == NULL)
    {
        return RS3_E_MEMORY;
    }
    planner->config = *config;
    *out = planner;
    return RS3_OK;
}

void rs3_prayer_destroy(rs3_prayer_planner *planner)
{
    free(planner);
}

int32_t rs3_prayer_reset(rs3_prayer_planner *planner)
{
    if (planner == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    rs3_prayer_config config = planner->config;
    uint64_t last_command_id = planner->last_command_id;
    memset(planner, 0, sizeof *planner);
    planner->config = config;
    planner->last_command_id = last_command_id;
    return RS3_OK;
}

int32_t rs3_prayer_note_sent(rs3_prayer_planner *planner, uint64_t command_id, uint64_t sent_at_ns)
{
    if (planner == NULL || command_id == 0)
    {
        return RS3_E_ARGUMENT;
    }
    if (planner->latched_fault || planner->input_pending ||
        planner->proposed_command_id != command_id || sent_at_ns < planner->proposed_at_ns ||
        sent_at_ns - planner->proposed_at_ns > milliseconds_to_ns(planner->config.max_age_ms))
    {
        return RS3_E_STATE;
    }
    planner->input_pending = 1;
    planner->sent_at_ns = sent_at_ns;
    planner->sent_sequence = planner->last_sequence;
    return RS3_OK;
}

/** Returns nonzero when the frame is structurally valid. */
static int frame_is_well_formed(const rs3_prayer_frame *frame)
{
    return frame->session_id != 0 && frame->sequence != 0 && frame->observed_ns <= frame->now_ns &&
           (frame->flags & ~VALID_FRAME_FLAGS) == 0 &&
           (frame->available_mask & ~RS3_PRAYER_OVERHEADS) == 0;
}

/**
 * Handles a proposal whose input was sent but not yet observed.
 * Returns nonzero when decision is final; zero once the input was acknowledged.
 */
static int handle_pending_input(rs3_prayer_planner *planner, const rs3_prayer_frame *frame,
                                const rs3_prayer_threat *threats, uint32_t threat_count,
                                rs3_prayer_decision *decision)
{
    uint64_t ack_timeout_ns = milliseconds_to_ns(planner->config.ack_timeout_ms);
    int acknowledged = frame->sequence > planner->sent_sequence &&
                       frame->observed_ns >= planner->sent_at_ns &&
                       frame->active_prayer == planner->proposed_prayer;
    if (acknowledged)
    {
        planner->input_pending = 0;
        planner->proposed_command_id = 0;
        return 0;
    }
    if (frame->now_ns - planner->sent_at_ns >= ack_timeout_ns)
    {
        planner->latched_fault = RS3_PRAYER_ACK_TIMEOUT;
        planner->proposed_command_id = 0;
        decision->action = RS3_PRAYER_FAULT;
        decision->reason = RS3_PRAYER_ACK_TIMEOUT;
        return 1;
    }

    decision->action = RS3_PRAYER_WAIT_ACK;
    decision->prayer = planner->proposed_prayer;
    decision->reason = RS3_PRAYER_ACK_PENDING;
    decision->command_id = planner->proposed_command_id;
    decision->next_wakeup_ns = saturating_add(planner->sent_at_ns, ack_timeout_ns);
    /* Report a protection window that opened while the switch is still unconfirmed. */
    for (uint32_t index = 0; index < threat_count; index++)
    {
        const rs3_prayer_threat *threat = &threats[index];
        if (is_protection_prayer(threat->prayer) && frame->active_prayer != threat->prayer &&
            frame->now_ns >= threat->check_earliest_ns &&
            frame->now_ns <= protection_end_ns(planner, threat))
        {
            decision->flags |= RS3_PRAYER_DEADLINE_MISSED;
            decision->threat_id = threat->event_id;
            decision->check_earliest_ns = threat->check_earliest_ns;
            decision->check_latest_ns = threat->check_latest_ns;
        }
    }
    return 1;
}

int32_t rs3_prayer_step(rs3_prayer_planner *planner, const rs3_prayer_frame *frame,
                        const rs3_prayer_threat *threats, uint32_t threat_count,
                        rs3_prayer_decision *decision, uint32_t out_size)
{
    if (planner == NULL || frame == NULL || decision == NULL ||
        (threat_count != 0 && threats == NULL))
    {
        return RS3_E_ARGUMENT;
    }
    if (out_size < sizeof *decision)
    {
        return RS3_E_ABI;
    }
    memset(decision, 0, sizeof *decision);
    if (threat_count > RS3_PRAYER_MAX_THREATS)
    {
        block(planner, decision, RS3_PRAYER_BAD_FRAME);
        return RS3_E_BUFFER;
    }

    /* An invalid frame cannot leave an older sendable proposal behind. */
    if (!frame_is_well_formed(frame))
    {
        return block(planner, decision, RS3_PRAYER_BAD_FRAME);
    }
    if (planner->session_id != 0 && planner->session_id != frame->session_id)
    {
        rs3_prayer_reset(planner);
    }
    if ((planner->last_now_ns != 0 && frame->now_ns < planner->last_now_ns) ||
        frame->sequence < planner->last_sequence)
    {
        return block(planner, decision, RS3_PRAYER_BAD_FRAME);
    }
    planner->session_id = frame->session_id;
    planner->last_now_ns = frame->now_ns;
    planner->last_sequence = frame->sequence;

    if (planner->latched_fault)
    {
        decision->action = RS3_PRAYER_FAULT;
        decision->reason = planner->latched_fault;
        return RS3_OK;
    }
    if (!(frame->flags & RS3_PRAYER_CONNECTED))
    {
        return block(planner, decision, RS3_PRAYER_DISCONNECTED);
    }
    if (frame->now_ns - frame->observed_ns > milliseconds_to_ns(planner->config.max_age_ms))
    {
        return block(planner, decision, RS3_PRAYER_STALE);
    }
    if (!(frame->flags & RS3_PRAYER_ACTIVE_KNOWN) || !is_valid_prayer(frame->active_prayer))
    {
        return block(planner, decision, RS3_PRAYER_ACTIVE_UNMAPPED);
    }
    if (!(frame->flags & RS3_PRAYER_POINTS_KNOWN) || frame->prayer_points == 0)
    {
        return block(planner, decision, RS3_PRAYER_NO_POINTS);
    }
    uint32_t threat_problem = validate_threats(threats, threat_count);
    if (threat_problem != RS3_PRAYER_READY)
    {
        return block(planner, decision, threat_problem);
    }

    if (planner->input_pending)
    {
        if (frame->now_ns < planner->sent_at_ns)
        {
            return block(planner, decision, RS3_PRAYER_BAD_FRAME);
        }
        if (handle_pending_input(planner, frame, threats, threat_count, decision))
        {
            return RS3_OK;
        }
    }

    /* Select the protection required now, and the next protection window after it. */
    uint32_t desired_prayer = RS3_PRAYER_UNKNOWN;
    const rs3_prayer_threat *selected_threat = NULL;
    const rs3_prayer_threat *next_threat = NULL;
    uint64_t next_switch_ns = UINT64_MAX;
    uint64_t lead_ns = milliseconds_to_ns(planner->config.lead_ms);
    for (uint32_t index = 0; index < threat_count; index++)
    {
        const rs3_prayer_threat *threat = &threats[index];
        uint64_t window_end_ns = protection_end_ns(planner, threat);
        if (window_end_ns < frame->now_ns || !is_protection_prayer(threat->prayer))
        {
            continue;
        }
        if (!(frame->available_mask & RS3_PRAYER_BIT(threat->prayer)))
        {
            return block(planner, decision, RS3_PRAYER_UNAVAILABLE);
        }
        uint64_t switch_at_ns = saturating_subtract(threat->check_earliest_ns, lead_ns);
        if (switch_at_ns <= frame->now_ns)
        {
            if (desired_prayer != RS3_PRAYER_UNKNOWN && desired_prayer != threat->prayer)
            {
                return block(planner, decision, RS3_PRAYER_CONFLICT);
            }
            desired_prayer = threat->prayer;
            if (selected_threat == NULL ||
                threat->check_earliest_ns < selected_threat->check_earliest_ns)
            {
                selected_threat = threat;
            }
            uint64_t wakeup_ns = saturating_add(window_end_ns, 1);
            if (decision->next_wakeup_ns == 0 || wakeup_ns < decision->next_wakeup_ns)
            {
                decision->next_wakeup_ns = wakeup_ns;
            }
        }
        else if (switch_at_ns < next_switch_ns)
        {
            next_switch_ns = switch_at_ns;
            next_threat = threat;
        }
    }

    if (desired_prayer == RS3_PRAYER_UNKNOWN)
    {
        /* No protection is due: consider the idle prayer within complete coverage. */
        uint64_t safe_until_ns = saturating_subtract(frame->coverage_until_ns, lead_ns);
        if (next_switch_ns < safe_until_ns)
        {
            safe_until_ns = next_switch_ns;
        }
        uint64_t required_gap_ns = milliseconds_to_ns(planner->config.min_idle_ms);
        if (frame->active_prayer != RS3_PRAYER_SOUL_SPLIT)
        {
            required_gap_ns =
                saturating_add(required_gap_ns, milliseconds_to_ns(planner->config.ack_timeout_ms));
        }
        int coverage_known = (frame->flags & RS3_PRAYER_COVERAGE_KNOWN) != 0;
        int soul_split_fits = planner->config.idle_prayer == RS3_PRAYER_SOUL_SPLIT &&
                              (frame->available_mask & RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT)) &&
                              coverage_known && safe_until_ns > frame->now_ns &&
                              safe_until_ns - frame->now_ns >= required_gap_ns;
        if (soul_split_fits)
        {
            desired_prayer = RS3_PRAYER_SOUL_SPLIT;
            decision->next_wakeup_ns = safe_until_ns;
        }
        else if (next_threat != NULL && coverage_known &&
                 frame->coverage_until_ns >= next_threat->check_earliest_ns)
        {
            /* An insufficient healing gap favours early protection. */
            desired_prayer = next_threat->prayer;
            selected_threat = next_threat;
            decision->next_wakeup_ns = next_switch_ns;
        }
        else
        {
            planner->proposed_command_id = 0;
            decision->action = RS3_PRAYER_HOLD;
            decision->prayer = frame->active_prayer;
            decision->reason = RS3_PRAYER_INCOMPLETE_COVERAGE;
            return RS3_OK;
        }
    }

    decision->prayer = desired_prayer;
    if (selected_threat != NULL)
    {
        decision->threat_id = selected_threat->event_id;
        decision->check_earliest_ns = selected_threat->check_earliest_ns;
        decision->check_latest_ns = selected_threat->check_latest_ns;
        if (frame->now_ns >= selected_threat->check_earliest_ns &&
            frame->active_prayer != desired_prayer)
        {
            decision->flags |= RS3_PRAYER_DEADLINE_MISSED;
            decision->reason = RS3_PRAYER_LATE;
        }
    }
    if (desired_prayer == frame->active_prayer)
    {
        planner->proposed_command_id = 0;
        decision->action = RS3_PRAYER_HOLD;
        return RS3_OK;
    }
    if (planner->proposed_command_id == 0 || planner->proposed_prayer != desired_prayer)
    {
        if (planner->last_command_id == UINT64_MAX)
        {
            return block(planner, decision, RS3_PRAYER_BAD_FRAME);
        }
        planner->proposed_command_id = ++planner->last_command_id;
        planner->proposed_prayer = desired_prayer;
    }
    planner->proposed_at_ns = frame->now_ns;
    decision->command_id = planner->proposed_command_id;
    decision->action = RS3_PRAYER_SET;
    return RS3_OK;
}
