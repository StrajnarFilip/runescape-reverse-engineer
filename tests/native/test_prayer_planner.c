/* Replays real prayer-timing failure modes deterministically; no game process or input. */
#undef NDEBUG
#include <assert.h>
#include <stdio.h>

#include "rs3/prayer.h"

#define MILLISECONDS(value) ((uint64_t)(value) * 1000000ull)
#define SESSION_ID          1u
#define PRAYER_POINTS       840u
#define ATTACKER_INSTANCE   42u

#define RUN_TEST(test_function)                                                                    \
    do                                                                                             \
    {                                                                                              \
        printf("RUN  %s\n", #test_function);                                                       \
        test_function();                                                                           \
    } while (0)

/** Soul Split idle, 200 ms lead, 50 ms hold, 100 ms gap, timeout and maximum age. */
static rs3_prayer_planner *create_planner(void)
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
    assert(rs3_prayer_create(&config, &planner) == RS3_OK);
    return planner;
}

/** A connected frame at time_ms with five seconds of complete coverage. */
static rs3_prayer_frame frame_at(uint64_t time_ms, uint32_t active_prayer)
{
    rs3_prayer_frame frame = {
        .now_ns = MILLISECONDS(time_ms),
        .observed_ns = MILLISECONDS(time_ms),
        .session_id = SESSION_ID,
        .sequence = time_ms,
        .coverage_until_ns = MILLISECONDS(time_ms + 5000),
        .flags = RS3_PRAYER_CONNECTED | RS3_PRAYER_ACTIVE_KNOWN | RS3_PRAYER_POINTS_KNOWN |
                 RS3_PRAYER_COVERAGE_KNOWN,
        .active_prayer = active_prayer,
        .available_mask = RS3_PRAYER_OVERHEADS,
        .prayer_points = PRAYER_POINTS,
    };
    return frame;
}

/** A validated attack whose 20 ms check window opens at time_ms. */
static rs3_prayer_threat attack_at(uint64_t event_id, uint64_t time_ms, uint32_t prayer)
{
    rs3_prayer_threat threat = {
        .event_id = event_id,
        .source_instance = ATTACKER_INSTANCE,
        .check_earliest_ns = MILLISECONDS(time_ms),
        .check_latest_ns = MILLISECONDS(time_ms + 20),
        .prayer = prayer,
        .flags = RS3_PRAYER_TIMING_VALIDATED,
    };
    return threat;
}

static rs3_prayer_decision step(rs3_prayer_planner *planner, rs3_prayer_frame frame,
                                rs3_prayer_threat *threats, uint32_t threat_count)
{
    rs3_prayer_decision decision;
    assert(rs3_prayer_step(planner, &frame, threats, threat_count, &decision, sizeof decision) ==
           RS3_OK);
    return decision;
}

static void waits_for_acknowledgement_and_returns_to_soul_split(void)
{
    rs3_prayer_planner *planner = create_planner();
    rs3_prayer_threat attack = attack_at(1, 10500, RS3_PRAYER_MAGIC);
    rs3_prayer_decision decision =
        step(planner, frame_at(10300, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(decision.action == RS3_PRAYER_SET && decision.prayer == RS3_PRAYER_MAGIC);
    uint64_t command_id = decision.command_id;

    /* Observing another frame does not imply that the caller sent the proposal. */
    decision = step(planner, frame_at(10301, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(decision.action == RS3_PRAYER_SET && decision.command_id == command_id);
    assert(rs3_prayer_note_sent(planner, command_id, MILLISECONDS(10301)) == RS3_OK);
    assert(rs3_prayer_note_sent(planner, command_id, MILLISECONDS(10301)) == RS3_E_STATE);

    /* Re-reading the same snapshot cannot acknowledge the key. */
    rs3_prayer_frame same_snapshot = frame_at(10302, RS3_PRAYER_MAGIC);
    same_snapshot.sequence = 10301;
    decision = step(planner, same_snapshot, &attack, 1);
    assert(decision.action == RS3_PRAYER_WAIT_ACK);
    decision = step(planner, frame_at(10310, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(decision.action == RS3_PRAYER_WAIT_ACK);
    decision = step(planner, frame_at(10321, RS3_PRAYER_MAGIC), &attack, 1);
    assert(decision.action == RS3_PRAYER_HOLD);

    /* Protection is held through the latest check time plus the hold time. */
    decision = step(planner, frame_at(10570, RS3_PRAYER_MAGIC), &attack, 1);
    assert(decision.action == RS3_PRAYER_HOLD);
    decision = step(planner, frame_at(10571, RS3_PRAYER_MAGIC), &attack, 1);
    assert(decision.action == RS3_PRAYER_SET && decision.prayer == RS3_PRAYER_SOUL_SPLIT);
    assert(decision.command_id != command_id);
    rs3_prayer_destroy(planner);
}

static void latches_lost_input_until_the_session_changes(void)
{
    rs3_prayer_planner *planner = create_planner();
    rs3_prayer_threat attack = attack_at(1, 10500, RS3_PRAYER_MAGIC);
    rs3_prayer_decision decision =
        step(planner, frame_at(10300, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    uint64_t command_id = decision.command_id;
    assert(rs3_prayer_note_sent(planner, command_id, MILLISECONDS(10300)) == RS3_OK);
    decision = step(planner, frame_at(10400, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(decision.action == RS3_PRAYER_FAULT && decision.reason == RS3_PRAYER_ACK_TIMEOUT);
    decision = step(planner, frame_at(10410, RS3_PRAYER_MAGIC), &attack, 1);
    assert(decision.action == RS3_PRAYER_FAULT);

    /* A new game scene invalidates the old pending input and fault state. */
    rs3_prayer_frame new_session = frame_at(10420, RS3_PRAYER_SOUL_SPLIT);
    new_session.session_id = SESSION_ID + 1;
    decision = step(planner, new_session, &attack, 1);
    assert(decision.action == RS3_PRAYER_SET && decision.command_id != command_id);
    assert(rs3_prayer_note_sent(planner, command_id, MILLISECONDS(10420)) == RS3_E_STATE);
    assert(rs3_prayer_reset(planner) == RS3_OK);
    assert(rs3_prayer_note_sent(planner, decision.command_id, MILLISECONDS(10420)) == RS3_E_STATE);
    rs3_prayer_destroy(planner);
}

static void blocks_unsafe_observations(void)
{
    rs3_prayer_planner *planner = create_planner();
    rs3_prayer_frame frame = frame_at(10000, RS3_PRAYER_MAGIC);
    frame.flags &= ~RS3_PRAYER_COVERAGE_KNOWN;
    rs3_prayer_decision decision = step(planner, frame, NULL, 0);
    assert(decision.action == RS3_PRAYER_HOLD);
    assert(decision.reason == RS3_PRAYER_INCOMPLETE_COVERAGE);

    /*
     * A distant known attack cannot justify dropping current protection while
     * other incoming attacks in the gap remain unknown.
     */
    rs3_prayer_threat distant_attack = attack_at(99, 12000, RS3_PRAYER_RANGED);
    decision = step(planner, frame, &distant_attack, 1);
    assert(decision.action == RS3_PRAYER_HOLD && decision.prayer == RS3_PRAYER_MAGIC);

    frame = frame_at(10001, RS3_PRAYER_MAGIC);
    frame.observed_ns -= MILLISECONDS(101);
    assert(step(planner, frame, NULL, 0).reason == RS3_PRAYER_STALE);
    frame = frame_at(10002, RS3_PRAYER_MAGIC);
    frame.flags &= ~RS3_PRAYER_ACTIVE_KNOWN;
    assert(step(planner, frame, NULL, 0).reason == RS3_PRAYER_ACTIVE_UNMAPPED);
    frame = frame_at(10003, RS3_PRAYER_MAGIC);
    frame.flags &= ~RS3_PRAYER_CONNECTED;
    assert(step(planner, frame, NULL, 0).reason == RS3_PRAYER_DISCONNECTED);
    frame = frame_at(10004, RS3_PRAYER_MAGIC);
    frame.prayer_points = 0;
    assert(step(planner, frame, NULL, 0).reason == RS3_PRAYER_NO_POINTS);

    rs3_prayer_threat unvalidated = attack_at(1, 11000, RS3_PRAYER_RANGED);
    unvalidated.flags = 0;
    assert(step(planner, frame_at(10005, RS3_PRAYER_MAGIC), &unvalidated, 1).reason ==
           RS3_PRAYER_UNKNOWN_ATTACK);

    /* An unvalidated attack while input is pending latches a fault. */
    rs3_prayer_threat attack = attack_at(2, 10206, RS3_PRAYER_MAGIC);
    decision = step(planner, frame_at(10006, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(rs3_prayer_note_sent(planner, decision.command_id, MILLISECONDS(10006)) == RS3_OK);
    attack.flags = 0;
    decision = step(planner, frame_at(10007, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(decision.action == RS3_PRAYER_FAULT && decision.reason == RS3_PRAYER_UNKNOWN_ATTACK);
    rs3_prayer_destroy(planner);
}

static void handles_simultaneous_attacks_and_late_switches(void)
{
    rs3_prayer_planner *planner = create_planner();
    rs3_prayer_threat attacks[2] = {attack_at(1, 10500, RS3_PRAYER_MAGIC),
                                    attack_at(2, 10500, RS3_PRAYER_MELEE)};
    rs3_prayer_decision decision =
        step(planner, frame_at(10300, RS3_PRAYER_SOUL_SPLIT), attacks, 2);
    assert(decision.action == RS3_PRAYER_BLOCKED && decision.reason == RS3_PRAYER_CONFLICT);

    attacks[1].prayer = RS3_PRAYER_MAGIC;
    attacks[1].check_latest_ns = MILLISECONDS(10600);
    decision = step(planner, frame_at(10580, RS3_PRAYER_MAGIC), attacks, 2);
    assert(decision.action == RS3_PRAYER_HOLD);
    decision = step(planner, frame_at(10581, RS3_PRAYER_RANGED), attacks, 2);
    assert(decision.action == RS3_PRAYER_SET && decision.prayer == RS3_PRAYER_MAGIC);
    assert(decision.flags & RS3_PRAYER_DEADLINE_MISSED);
    assert(rs3_prayer_note_sent(planner, decision.command_id, MILLISECONDS(10581)) == RS3_OK);
    decision = step(planner, frame_at(10582, RS3_PRAYER_RANGED), attacks, 2);
    assert(decision.action == RS3_PRAYER_WAIT_ACK);
    assert(decision.flags & RS3_PRAYER_DEADLINE_MISSED);

    /* Duplicate event IDs make the frame invalid. */
    attacks[1].event_id = 1;
    assert(step(planner, frame_at(10583, RS3_PRAYER_MAGIC), attacks, 2).reason ==
           RS3_PRAYER_BAD_FRAME);
    rs3_prayer_destroy(planner);
}

static void respects_availability_and_short_gaps(void)
{
    rs3_prayer_planner *planner = create_planner();
    rs3_prayer_frame frame = frame_at(10000, RS3_PRAYER_MAGIC);
    frame.available_mask &= ~RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT);
    assert(step(planner, frame, NULL, 0).action == RS3_PRAYER_HOLD);

    /* A 150 ms gap cannot fit the idle time plus the acknowledgement allowance. */
    rs3_prayer_threat attack = attack_at(1, 10350, RS3_PRAYER_MELEE);
    rs3_prayer_decision decision = step(planner, frame_at(10001, RS3_PRAYER_MAGIC), &attack, 1);
    assert(decision.action == RS3_PRAYER_SET && decision.prayer == RS3_PRAYER_MELEE);

    frame = frame_at(10002, RS3_PRAYER_MAGIC);
    frame.available_mask &= ~RS3_PRAYER_BIT(RS3_PRAYER_MELEE);
    assert(step(planner, frame, &attack, 1).reason == RS3_PRAYER_UNAVAILABLE);

    /* Known typeless damage does not request a protection prayer. */
    attack.prayer = RS3_PRAYER_NONE;
    decision = step(planner, frame_at(10003, RS3_PRAYER_SOUL_SPLIT), &attack, 1);
    assert(decision.action == RS3_PRAYER_HOLD);
    rs3_prayer_destroy(planner);
}

static void rejects_bad_bounds_and_clocks(void)
{
    rs3_prayer_planner *planner = create_planner();
    rs3_prayer_frame frame = frame_at(10000, RS3_PRAYER_MAGIC);
    rs3_prayer_decision decision;
    assert(rs3_prayer_step(planner, &frame, NULL, 0, &decision, 1) == RS3_E_ABI);

    /* A proposal expires after the maximum observation age. */
    decision = step(planner, frame, NULL, 0);
    uint64_t command_id = decision.command_id;
    assert(command_id != 0);
    assert(rs3_prayer_note_sent(planner, command_id, MILLISECONDS(10101)) == RS3_E_STATE);

    /* Too many threats withdraw the proposal. */
    rs3_prayer_threat attack = attack_at(1, 10500, RS3_PRAYER_MAGIC);
    assert(rs3_prayer_step(planner, &frame, &attack, RS3_PRAYER_MAX_THREATS + 1, &decision,
                           sizeof decision) == RS3_E_BUFFER);
    assert(rs3_prayer_note_sent(planner, command_id, MILLISECONDS(10000)) == RS3_E_STATE);

    /* An observation from the future and a clock that runs backwards are bad frames. */
    frame.observed_ns = frame.now_ns + 1;
    assert(step(planner, frame, NULL, 0).reason == RS3_PRAYER_BAD_FRAME);
    frame = frame_at(9999, RS3_PRAYER_MAGIC);
    assert(step(planner, frame, NULL, 0).reason == RS3_PRAYER_BAD_FRAME);

    assert(rs3_prayer_sizeof(RS3_PRAYER_SIZE_CONFIG) == 32);
    assert(rs3_prayer_sizeof(RS3_PRAYER_SIZE_FRAME) == 56);
    assert(rs3_prayer_sizeof(RS3_PRAYER_SIZE_THREAT) == 40);
    assert(rs3_prayer_sizeof(RS3_PRAYER_SIZE_DECISION) == 56);
    rs3_prayer_destroy(planner);
}

int main(void)
{
    RUN_TEST(waits_for_acknowledgement_and_returns_to_soul_split);
    RUN_TEST(latches_lost_input_until_the_session_changes);
    RUN_TEST(blocks_unsafe_observations);
    RUN_TEST(handles_simultaneous_attacks_and_late_switches);
    RUN_TEST(respects_availability_and_short_gaps);
    RUN_TEST(rejects_bad_bounds_and_clocks);
    puts("PASS: prayer planner tests");
    return 0;
}
