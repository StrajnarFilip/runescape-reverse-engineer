/* The cadence calculator is deterministic and never sends game input. */
#undef NDEBUG
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "rs3/prayer.h"

#define MS(value) ((uint64_t)(value) * 1000000ull)

static rs3_prayer_cycle_frame frame_at(uint64_t now_ms, uint32_t active_prayer)
{
    rs3_prayer_cycle_frame frame = {
        .now_ns = MS(now_ms),
        .last_cue_ns = MS(1000),
        .last_outcome_ns = MS(1550),
        .period_ms = 2400,
        .lead_before_cue_ms = 900,
        .acknowledgement_budget_ms = 800,
        .minimum_soul_split_ms = 100,
        .cue_tolerance_ms = 200,
        .active_prayer = active_prayer,
        .protection_prayer = RS3_PRAYER_MELEE,
    };
    return frame;
}

static rs3_prayer_cycle_prediction predict(rs3_prayer_cycle_frame frame)
{
    rs3_prayer_cycle_prediction prediction;
    assert(rs3_prayer_predict_cycle(&frame, &prediction, sizeof prediction) == RS3_OK);
    return prediction;
}

int main(void)
{
    assert(rs3_prayer_sizeof(RS3_PRAYER_SIZE_CYCLE_FRAME) == 56);
    assert(rs3_prayer_sizeof(RS3_PRAYER_SIZE_CYCLE_PREDICTION) == 48);
    rs3_prayer_cycle_frame frame = frame_at(1550, RS3_PRAYER_MELEE);
    rs3_prayer_cycle_prediction plan = predict(frame);
    assert(plan.next_cue_ns == MS(3400));
    assert(plan.protection_send_ns == MS(2500));
    assert(plan.cue_deadline_ns == MS(3600));
    assert(plan.latest_soul_split_send_ns == MS(1600));
    assert(plan.desired_prayer == RS3_PRAYER_SOUL_SPLIT);
    frame.now_ns = MS(2499);
    frame.active_prayer = RS3_PRAYER_SOUL_SPLIT;
    assert(predict(frame).desired_prayer == RS3_PRAYER_SOUL_SPLIT);
    frame.now_ns = MS(2500);
    assert(predict(frame).desired_prayer == RS3_PRAYER_MELEE);
    frame = frame_at(1700, RS3_PRAYER_MELEE);
    plan = predict(frame);
    assert(plan.desired_prayer == RS3_PRAYER_MELEE && plan.reason == RS3_PRAYER_CYCLE_SHORT_WINDOW);
    frame.protection_prayer = RS3_PRAYER_MAGIC;
    frame.now_ns = MS(2500);
    assert(predict(frame).desired_prayer == RS3_PRAYER_MAGIC);
    frame.now_ns = MS(3601);
    assert(predict(frame).reason == RS3_PRAYER_CYCLE_EXPIRED);
    frame.last_cue_ns = 0;
    assert(predict(frame).reason == RS3_PRAYER_CYCLE_NEEDS_ANCHOR);
    frame = frame_at(1550, RS3_PRAYER_MELEE);
    frame.last_cue_ns = UINT64_MAX - MS(100);
    frame.last_outcome_ns = frame.last_cue_ns;
    frame.now_ns = frame.last_cue_ns;
    assert(rs3_prayer_predict_cycle(&frame, &plan, sizeof plan) == RS3_E_ARGUMENT);
    frame = frame_at(1550, RS3_PRAYER_MELEE);
    frame.protection_prayer = RS3_PRAYER_UNKNOWN;
    assert(rs3_prayer_predict_cycle(&frame, &plan, sizeof plan) == RS3_E_ARGUMENT);
    frame.protection_prayer = RS3_PRAYER_SOUL_SPLIT;
    assert(rs3_prayer_predict_cycle(&frame, &plan, sizeof plan) == RS3_E_ARGUMENT);
    frame = frame_at(1550, RS3_PRAYER_MELEE);
    frame.lead_before_cue_ms = frame.period_ms;
    assert(rs3_prayer_predict_cycle(&frame, &plan, sizeof plan) == RS3_E_ARGUMENT);
    assert(rs3_prayer_predict_cycle(NULL, &plan, sizeof plan) == RS3_E_ARGUMENT);
    assert(rs3_prayer_predict_cycle(&frame, &plan, 1) == RS3_E_ABI);
    puts("PASS: prayer cycle prediction tests");
    return 0;
}
