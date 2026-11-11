/* Synthetic prayer timing example. No client is opened and no input is sent. */
#include <stdio.h>

#include "rs3/prayer.h"

int main(void)
{
    rs3_prayer_config config = {.struct_size = sizeof config,
                                .idle_prayer = RS3_PRAYER_SOUL_SPLIT,
                                .lead_ms = 200,
                                .hold_ms = 50,
                                .min_idle_ms = 100,
                                .ack_timeout_ms = 100,
                                .max_age_ms = 100,
                                .reserved = 0};
    rs3_prayer_planner *planner = NULL;
    if (rs3_prayer_create(&config, &planner) != RS3_OK)
    {
        return 1;
    }

    /* Soul Split is active and a validated magic attack is checked 200 ms from now. */
    rs3_prayer_frame frame = {.now_ns = 10000000000ull,
                              .observed_ns = 10000000000ull,
                              .session_id = 1,
                              .sequence = 1,
                              .coverage_until_ns = 15000000000ull,
                              .flags = RS3_PRAYER_CONNECTED | RS3_PRAYER_ACTIVE_KNOWN |
                                       RS3_PRAYER_POINTS_KNOWN | RS3_PRAYER_COVERAGE_KNOWN,
                              .active_prayer = RS3_PRAYER_SOUL_SPLIT,
                              .available_mask = RS3_PRAYER_OVERHEADS,
                              .prayer_points = 840};
    rs3_prayer_threat magic_attack = {.event_id = 1,
                                      .source_instance = 42,
                                      .check_earliest_ns = 10200000000ull,
                                      .check_latest_ns = 10200000000ull,
                                      .prayer = RS3_PRAYER_MAGIC,
                                      .flags = RS3_PRAYER_TIMING_VALIDATED};

    rs3_prayer_decision decision;
    int32_t result = rs3_prayer_step(planner, &frame, &magic_attack, 1, &decision, sizeof decision);
    if (result == RS3_OK)
    {
        printf("Synthetic proposal: action=%u prayer=%u reason=%s\n", decision.action,
               decision.prayer, rs3_prayer_reason_string(decision.reason));
    }
    int proposes_magic = result == RS3_OK && decision.action == RS3_PRAYER_SET &&
                         decision.prayer == RS3_PRAYER_MAGIC;
    rs3_prayer_destroy(planner);
    return proposes_magic ? 0 : 1;
}
