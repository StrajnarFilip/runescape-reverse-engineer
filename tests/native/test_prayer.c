/* Replay real failure modes deterministically; no game process or input. */
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
#include "rs3/prayer.h"
#define NS(ms) ((uint64_t)(ms)*1000000ull)

static rs3_prayer_planner *planner(void) {
    rs3_prayer_config c={sizeof c,RS3_PRAYER_SOUL_SPLIT,200,50,100,100,100,0};
    rs3_prayer_planner *p=NULL; assert(rs3_prayer_create(&c,&p)==RS3_OK); return p;
}
static rs3_prayer_frame frame(uint64_t ms,uint32_t active) {
    rs3_prayer_frame f={NS(ms),NS(ms),1,ms,NS(ms+5000),15,active,RS3_PRAYER_OVERHEADS,840};
    return f;
}
static rs3_prayer_decision step(rs3_prayer_planner *p,rs3_prayer_frame f,
    rs3_prayer_threat *t,uint32_t n) {
    rs3_prayer_decision d;
    assert(rs3_prayer_step(p,&f,t,n,&d,sizeof d)==RS3_OK); return d;
}
static rs3_prayer_threat hit(uint64_t id,uint64_t ms,uint32_t prayer) {
    rs3_prayer_threat t={id,42,NS(ms),NS(ms+20),prayer,RS3_PRAYER_TIMING_VALIDATED}; return t;
}
static void acknowledgement_and_healing_window(void) {
    rs3_prayer_planner *p=planner(); rs3_prayer_threat t=hit(1,10500,RS3_PRAYER_MAGIC);
    rs3_prayer_decision d=step(p,frame(10300,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(d.action==RS3_PRAYER_SET && d.prayer==RS3_PRAYER_MAGIC);
    uint64_t id=d.command_id;
    // Observing another frame does not imply the caller sent the proposal.
    d=step(p,frame(10301,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(d.action==RS3_PRAYER_SET && d.command_id==id);
    assert(rs3_prayer_note_sent(p,id,NS(10301))==RS3_OK);
    assert(rs3_prayer_note_sent(p,id,NS(10301))==RS3_E_STATE);
    // Re-reading the same snapshot cannot acknowledge the key.
    rs3_prayer_frame f=frame(10302,RS3_PRAYER_MAGIC); f.sequence=10301;
    d=step(p,f,&t,1); assert(d.action==RS3_PRAYER_WAIT_ACK);
    d=step(p,frame(10310,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(d.action==RS3_PRAYER_WAIT_ACK);
    d=step(p,frame(10321,RS3_PRAYER_MAGIC),&t,1);
    assert(d.action==RS3_PRAYER_HOLD);
    d=step(p,frame(10570,RS3_PRAYER_MAGIC),&t,1);
    assert(d.action==RS3_PRAYER_HOLD); // Includes latest check time + hold.
    d=step(p,frame(10571,RS3_PRAYER_MAGIC),&t,1);
    assert(d.action==RS3_PRAYER_SET && d.prayer==RS3_PRAYER_SOUL_SPLIT);
    assert(d.command_id!=id);
    rs3_prayer_destroy(p);
}
static void lost_input_and_session_reset(void) {
    rs3_prayer_planner *p=planner(); rs3_prayer_threat t=hit(1,10500,RS3_PRAYER_MAGIC);
    rs3_prayer_decision d=step(p,frame(10300,RS3_PRAYER_SOUL_SPLIT),&t,1);
    uint64_t id=d.command_id; assert(rs3_prayer_note_sent(p,id,NS(10300))==RS3_OK);
    d=step(p,frame(10400,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(d.action==RS3_PRAYER_FAULT && d.reason==RS3_PRAYER_ACK_TIMEOUT);
    d=step(p,frame(10410,RS3_PRAYER_MAGIC),&t,1); assert(d.action==RS3_PRAYER_FAULT);
    // A new game scene invalidates old pending input and fault state.
    rs3_prayer_frame f=frame(10420,RS3_PRAYER_SOUL_SPLIT); f.session_id=2;
    d=step(p,f,&t,1); assert(d.action==RS3_PRAYER_SET && d.command_id!=id);
    assert(rs3_prayer_note_sent(p,id,NS(10420))==RS3_E_STATE);
    assert(rs3_prayer_reset(p)==RS3_OK);
    assert(rs3_prayer_note_sent(p,d.command_id,NS(10420))==RS3_E_STATE);
    rs3_prayer_destroy(p);
}
static void unsafe_observations(void) {
    rs3_prayer_planner *p=planner(); rs3_prayer_frame f=frame(10000,RS3_PRAYER_MAGIC);
    f.flags&=~RS3_PRAYER_COVERAGE_KNOWN;
    rs3_prayer_decision d=step(p,f,NULL,0);
    assert(d.action==RS3_PRAYER_HOLD && d.reason==RS3_PRAYER_INCOMPLETE_COVERAGE);
    rs3_prayer_threat distant=hit(99,12000,RS3_PRAYER_RANGED);
    d=step(p,f,&distant,1);
    assert(d.action==RS3_PRAYER_HOLD && d.prayer==RS3_PRAYER_MAGIC);
    // A distant known attack cannot justify dropping current protection while
    // other incoming attacks in the intervening gap remain unknown.
    f=frame(10001,RS3_PRAYER_MAGIC); f.observed_ns-=NS(101);
    assert(step(p,f,NULL,0).reason==RS3_PRAYER_STALE);
    f=frame(10002,RS3_PRAYER_MAGIC); f.flags&=~RS3_PRAYER_ACTIVE_KNOWN;
    assert(step(p,f,NULL,0).reason==RS3_PRAYER_ACTIVE_UNMAPPED);
    f=frame(10003,RS3_PRAYER_MAGIC); f.flags&=~RS3_PRAYER_CONNECTED;
    assert(step(p,f,NULL,0).reason==RS3_PRAYER_DISCONNECTED);
    f=frame(10004,RS3_PRAYER_MAGIC); f.prayer_points=0;
    assert(step(p,f,NULL,0).reason==RS3_PRAYER_NO_POINTS);
    rs3_prayer_threat t=hit(1,11000,RS3_PRAYER_RANGED); t.flags=0;
    assert(step(p,frame(10005,RS3_PRAYER_MAGIC),&t,1).reason==RS3_PRAYER_UNKNOWN_ATTACK);
    t=hit(2,10206,RS3_PRAYER_MAGIC);
    d=step(p,frame(10006,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(rs3_prayer_note_sent(p,d.command_id,NS(10006))==RS3_OK);
    t.flags=0;
    d=step(p,frame(10007,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(d.action==RS3_PRAYER_FAULT && d.reason==RS3_PRAYER_UNKNOWN_ATTACK);
    rs3_prayer_destroy(p);
}
static void simultaneous_attacks_and_late_switch(void) {
    rs3_prayer_planner *p=planner(); rs3_prayer_threat t[2]={hit(1,10500,RS3_PRAYER_MAGIC),hit(2,10500,RS3_PRAYER_MELEE)};
    rs3_prayer_decision d=step(p,frame(10300,RS3_PRAYER_SOUL_SPLIT),t,2);
    assert(d.action==RS3_PRAYER_BLOCKED && d.reason==RS3_PRAYER_CONFLICT);
    t[1].prayer=RS3_PRAYER_MAGIC; t[1].check_latest_ns=NS(10600);
    d=step(p,frame(10580,RS3_PRAYER_MAGIC),t,2); assert(d.action==RS3_PRAYER_HOLD);
    d=step(p,frame(10581,RS3_PRAYER_RANGED),t,2);
    assert(d.action==RS3_PRAYER_SET && d.prayer==RS3_PRAYER_MAGIC);
    assert(d.flags&RS3_PRAYER_DEADLINE_MISSED);
    assert(rs3_prayer_note_sent(p,d.command_id,NS(10581))==RS3_OK);
    d=step(p,frame(10582,RS3_PRAYER_RANGED),t,2);
    assert(d.action==RS3_PRAYER_WAIT_ACK && (d.flags&RS3_PRAYER_DEADLINE_MISSED));
    t[1].event_id=1;
    assert(step(p,frame(10583,RS3_PRAYER_MAGIC),t,2).reason==RS3_PRAYER_BAD_FRAME);
    rs3_prayer_destroy(p);
}
static void availability_and_short_gaps(void) {
    rs3_prayer_planner *p=planner(); rs3_prayer_frame f=frame(10000,RS3_PRAYER_MAGIC);
    f.available_mask&=~RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT);
    assert(step(p,f,NULL,0).action==RS3_PRAYER_HOLD);
    rs3_prayer_threat t=hit(1,10350,RS3_PRAYER_MELEE);
    // The 150ms gap cannot fit idle time plus input acknowledgement allowance.
    rs3_prayer_decision d=step(p,frame(10001,RS3_PRAYER_MAGIC),&t,1);
    assert(d.action==RS3_PRAYER_SET && d.prayer==RS3_PRAYER_MELEE);
    f=frame(10002,RS3_PRAYER_MAGIC); f.available_mask&=~RS3_PRAYER_BIT(RS3_PRAYER_MELEE);
    assert(step(p,f,&t,1).reason==RS3_PRAYER_UNAVAILABLE);
    t.prayer=RS3_PRAYER_NONE;
    d=step(p,frame(10003,RS3_PRAYER_SOUL_SPLIT),&t,1);
    assert(d.action==RS3_PRAYER_HOLD); // Known typeless damage does not request a protection prayer.
    rs3_prayer_destroy(p);
}
static void bounds_and_clock_failures(void) {
    rs3_prayer_planner *p=planner(); rs3_prayer_frame f=frame(10000,RS3_PRAYER_MAGIC);
    rs3_prayer_decision d;
    assert(rs3_prayer_step(p,&f,NULL,0,&d,1)==RS3_E_ABI);
    d=step(p,f,NULL,0); uint64_t id=d.command_id;
    assert(id && rs3_prayer_note_sent(p,id,NS(10101))==RS3_E_STATE);
    rs3_prayer_threat t=hit(1,10500,RS3_PRAYER_MAGIC);
    assert(rs3_prayer_step(p,&f,&t,129,&d,sizeof d)==RS3_E_BUFFER);
    assert(rs3_prayer_note_sent(p,id,NS(10000))==RS3_E_STATE);
    f.observed_ns=f.now_ns+1;
    assert(step(p,f,NULL,0).reason==RS3_PRAYER_BAD_FRAME);
    f=frame(9999,RS3_PRAYER_MAGIC);
    assert(step(p,f,NULL,0).reason==RS3_PRAYER_BAD_FRAME);
    assert(rs3_prayer_sizeof(1)==32 && rs3_prayer_sizeof(2)==56 &&
           rs3_prayer_sizeof(3)==40 && rs3_prayer_sizeof(4)==56);
    rs3_prayer_destroy(p);
}
int main(void) {
    acknowledgement_and_healing_window(); lost_input_and_session_reset(); unsafe_observations();
    simultaneous_attacks_and_late_switch(); availability_and_short_gaps(); bounds_and_clock_failures();
    puts("PASS: 6 prayer planner test groups"); return 0;
}
