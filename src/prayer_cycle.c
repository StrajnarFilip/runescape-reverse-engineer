/* Cadence arithmetic shared by language consumers; estimates do not validate attacks. */
#include "rs3/prayer.h"

#include <limits.h>
#include <string.h>

#define NANOSECONDS_PER_MILLISECOND 1000000ull
#define MAX_CYCLE_DURATION_MS       60000u

_Static_assert(sizeof(rs3_prayer_cycle_frame) == 56, "ABI cycle frame layout");
_Static_assert(sizeof(rs3_prayer_cycle_prediction) == 48, "ABI cycle prediction layout");

static int protection_is_known(uint32_t prayer)
{
    return prayer >= RS3_PRAYER_MELEE && prayer <= RS3_PRAYER_NECROMANCY &&
           prayer != RS3_PRAYER_SOUL_SPLIT;
}

int32_t rs3_prayer_predict_cycle(const rs3_prayer_cycle_frame *frame,
                                 rs3_prayer_cycle_prediction *out, uint32_t out_size)
{
    if (frame == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    if (out_size < sizeof *out)
    {
        return RS3_E_ABI;
    }
    memset(out, 0, sizeof *out);
    if (frame->reserved != 0 || frame->period_ms == 0 || frame->period_ms > MAX_CYCLE_DURATION_MS ||
        frame->lead_before_cue_ms == 0 || frame->lead_before_cue_ms >= frame->period_ms ||
        frame->acknowledgement_budget_ms == 0 ||
        frame->acknowledgement_budget_ms > MAX_CYCLE_DURATION_MS ||
        frame->minimum_soul_split_ms > MAX_CYCLE_DURATION_MS ||
        frame->cue_tolerance_ms > frame->period_ms || frame->active_prayer < RS3_PRAYER_NONE ||
        frame->active_prayer > RS3_PRAYER_NECROMANCY ||
        !protection_is_known(frame->protection_prayer))
    {
        return RS3_E_ARGUMENT;
    }
    out->desired_prayer = frame->protection_prayer;
    if (frame->last_cue_ns == 0 || frame->last_outcome_ns == 0)
    {
        out->reason = RS3_PRAYER_CYCLE_NEEDS_ANCHOR;
        return RS3_OK;
    }
    uint64_t period_ns = frame->period_ms * NANOSECONDS_PER_MILLISECOND;
    uint64_t tolerance_ns = frame->cue_tolerance_ms * NANOSECONDS_PER_MILLISECOND;
    if (frame->last_outcome_ns < frame->last_cue_ns || frame->last_outcome_ns > frame->now_ns ||
        UINT64_MAX - frame->last_cue_ns < period_ns + tolerance_ns)
    {
        return RS3_E_ARGUMENT;
    }
    out->next_cue_ns = frame->last_cue_ns + period_ns;
    if (frame->last_outcome_ns >= out->next_cue_ns)
    {
        return RS3_E_ARGUMENT;
    }
    out->protection_send_ns =
        out->next_cue_ns - frame->lead_before_cue_ms * NANOSECONDS_PER_MILLISECOND;
    out->cue_deadline_ns = out->next_cue_ns + tolerance_ns;
    uint64_t required_window_ns =
        ((uint64_t)frame->acknowledgement_budget_ms + frame->minimum_soul_split_ms) *
        NANOSECONDS_PER_MILLISECOND;
    out->latest_soul_split_send_ns = out->protection_send_ns > required_window_ns
                                         ? out->protection_send_ns - required_window_ns
                                         : 0;
    if (frame->now_ns > out->cue_deadline_ns)
    {
        out->reason = RS3_PRAYER_CYCLE_EXPIRED;
    }
    else if (frame->now_ns < out->protection_send_ns)
    {
        if (frame->active_prayer == RS3_PRAYER_SOUL_SPLIT ||
            frame->now_ns <= out->latest_soul_split_send_ns)
        {
            out->desired_prayer = RS3_PRAYER_SOUL_SPLIT;
        }
        else
        {
            out->reason = RS3_PRAYER_CYCLE_SHORT_WINDOW;
        }
    }
    return RS3_OK;
}
