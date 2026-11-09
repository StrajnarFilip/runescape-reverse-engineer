/* A proposal is distinct from accepted input and from observed game state. */
#include "rs3/prayer.h"
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#define MS 1000000ull
struct rs3_prayer_planner {
    rs3_prayer_config config;
    uint64_t session, last_now, last_sequence, serial, proposal, proposed_at;
    uint64_t sent_at, sent_sequence;
    uint32_t proposed_prayer, pending, fault;
};
static uint64_t add_sat(uint64_t a,uint64_t b) { return UINT64_MAX-a<b?UINT64_MAX:a+b; }
static uint64_t sub_sat(uint64_t a,uint64_t b) { return a>b?a-b:0; }
static int valid_prayer(uint32_t p) { return p>=RS3_PRAYER_NONE && p<=RS3_PRAYER_NECROMANCY; }
static int protective(uint32_t p) { return p>=RS3_PRAYER_MELEE && p!=RS3_PRAYER_SOUL_SPLIT && p<=RS3_PRAYER_NECROMANCY; }
static uint32_t validate_threats(const rs3_prayer_threat *t,uint32_t n) {
    for (uint32_t i=0;i<n;i++) {
        if (!t[i].event_id || t[i].check_earliest_ns>t[i].check_latest_ns ||
            t[i].flags!=RS3_PRAYER_TIMING_VALIDATED || !valid_prayer(t[i].prayer) ||
            t[i].prayer==RS3_PRAYER_SOUL_SPLIT) return RS3_PRAYER_UNKNOWN_ATTACK;
        for (uint32_t j=0;j<i;j++) if (t[j].event_id==t[i].event_id) return RS3_PRAYER_BAD_FRAME;
    }
    return RS3_PRAYER_READY;
}
static int32_t block(rs3_prayer_planner *p,rs3_prayer_decision *d,uint32_t reason) {
    if (p->pending) p->fault=reason;
    p->proposal=0;
    d->action=p->fault?RS3_PRAYER_FAULT:RS3_PRAYER_BLOCKED; d->reason=reason;
    return RS3_OK;
}
uint32_t rs3_prayer_sizeof(uint32_t s) {
    switch(s) { case RS3_PRAYER_SIZE_CONFIG:return sizeof(rs3_prayer_config);
    case RS3_PRAYER_SIZE_FRAME:return sizeof(rs3_prayer_frame);
    case RS3_PRAYER_SIZE_THREAT:return sizeof(rs3_prayer_threat);
    case RS3_PRAYER_SIZE_DECISION:return sizeof(rs3_prayer_decision); default:return 0; }
}
const char *rs3_prayer_reason_string(uint32_t r) {
    static const char *names[]={"ready","bad_frame","stale_observation","disconnected",
        "active_prayer_unknown","prayer_points_unavailable_or_empty","unvalidated_attack",
        "incompatible_protection_windows","prayer_unavailable","incomplete_attack_coverage",
        "awaiting_game_acknowledgement","acknowledgement_timeout","protection_deadline_missed"};
    return r<sizeof names/sizeof names[0]?names[r]:"unknown_reason";
}
int32_t rs3_prayer_create(const rs3_prayer_config *c,rs3_prayer_planner **out) {
    if (!out) return RS3_E_ARGUMENT;
    *out=NULL;
    if (!c || c->struct_size!=sizeof *c || c->reserved ||
        (c->idle_prayer!=RS3_PRAYER_NONE && c->idle_prayer!=RS3_PRAYER_SOUL_SPLIT) ||
        !c->lead_ms || !c->ack_timeout_ms || !c->max_age_ms ||
        c->lead_ms>60000 || c->hold_ms>60000 || c->min_idle_ms>60000 ||
        c->ack_timeout_ms>60000 || c->max_age_ms>60000) return RS3_E_ARGUMENT;
    rs3_prayer_planner *p=calloc(1,sizeof *p); if (!p) return RS3_E_MEMORY;
    p->config=*c; *out=p; return RS3_OK;
}
void rs3_prayer_destroy(rs3_prayer_planner *p) { free(p); }
int32_t rs3_prayer_reset(rs3_prayer_planner *p) {
    if (!p) return RS3_E_ARGUMENT;
    rs3_prayer_config c=p->config; uint64_t serial=p->serial;
    memset(p,0,sizeof *p); p->config=c; p->serial=serial; return RS3_OK;
}
int32_t rs3_prayer_note_sent(rs3_prayer_planner *p,uint64_t id,uint64_t now) {
    if (!p || !id) return RS3_E_ARGUMENT;
    if (p->fault || p->pending || p->proposal!=id || now<p->proposed_at ||
        now-p->proposed_at>(uint64_t)p->config.max_age_ms*MS) return RS3_E_STATE;
    p->pending=1; p->sent_at=now; p->sent_sequence=p->last_sequence; return RS3_OK;
}
int32_t rs3_prayer_step(rs3_prayer_planner *p,const rs3_prayer_frame *f,
    const rs3_prayer_threat *t,uint32_t count,rs3_prayer_decision *d,uint32_t size) {
    if (!p || !f || !d || (count && !t)) return RS3_E_ARGUMENT;
    if (size<sizeof *d) return RS3_E_ABI;
    memset(d,0,sizeof *d);
    if (count>RS3_PRAYER_MAX_THREATS) {
        block(p,d,RS3_PRAYER_BAD_FRAME); return RS3_E_BUFFER;
    }
    /* Invalid frames cannot leave an old sendable proposal behind. */
    if (!f->session_id || !f->sequence || f->observed_ns>f->now_ns ||
        (f->flags&~15u) || (f->available_mask&~RS3_PRAYER_OVERHEADS))
        return block(p,d,RS3_PRAYER_BAD_FRAME);
    if (p->session && p->session!=f->session_id) rs3_prayer_reset(p);
    if ((p->last_now && f->now_ns<p->last_now) || f->sequence<p->last_sequence)
        return block(p,d,RS3_PRAYER_BAD_FRAME);
    p->session=f->session_id; p->last_now=f->now_ns; p->last_sequence=f->sequence;
    if (p->fault) { d->action=RS3_PRAYER_FAULT; d->reason=p->fault; return RS3_OK; }
    if (!(f->flags&RS3_PRAYER_CONNECTED)) return block(p,d,RS3_PRAYER_DISCONNECTED);
    if (f->now_ns-f->observed_ns>(uint64_t)p->config.max_age_ms*MS)
        return block(p,d,RS3_PRAYER_STALE);
    if (!(f->flags&RS3_PRAYER_ACTIVE_KNOWN) || !valid_prayer(f->active_prayer))
        return block(p,d,RS3_PRAYER_ACTIVE_UNMAPPED);
    if (!(f->flags&RS3_PRAYER_POINTS_KNOWN) || !f->prayer_points)
        return block(p,d,RS3_PRAYER_NO_POINTS);
    uint32_t invalid=validate_threats(t,count);
    if (invalid) return block(p,d,invalid);
    if (p->pending) {
        if (f->now_ns<p->sent_at) return block(p,d,RS3_PRAYER_BAD_FRAME);
        if (f->sequence>p->sent_sequence && f->observed_ns>=p->sent_at &&
            f->active_prayer==p->proposed_prayer) { p->pending=0; p->proposal=0; }
        else if (f->now_ns-p->sent_at>=(uint64_t)p->config.ack_timeout_ms*MS) {
            p->fault=RS3_PRAYER_ACK_TIMEOUT; p->proposal=0;
            d->action=RS3_PRAYER_FAULT; d->reason=RS3_PRAYER_ACK_TIMEOUT; return RS3_OK;
        } else {
            d->action=RS3_PRAYER_WAIT_ACK; d->prayer=p->proposed_prayer;
            d->reason=RS3_PRAYER_ACK_PENDING; d->command_id=p->proposal;
            d->next_wakeup_ns=add_sat(p->sent_at,(uint64_t)p->config.ack_timeout_ms*MS);
            for (uint32_t i=0;i<count;i++) if (protective(t[i].prayer) &&
                f->active_prayer!=t[i].prayer && f->now_ns>=t[i].check_earliest_ns &&
                f->now_ns<=add_sat(t[i].check_latest_ns,(uint64_t)p->config.hold_ms*MS)) {
                d->flags|=RS3_PRAYER_DEADLINE_MISSED; d->threat_id=t[i].event_id;
                d->check_earliest_ns=t[i].check_earliest_ns; d->check_latest_ns=t[i].check_latest_ns;
            }
            return RS3_OK;
        }
    }
    uint32_t desired=RS3_PRAYER_UNKNOWN; const rs3_prayer_threat *selected=NULL,*next=NULL;
    uint64_t next_switch=UINT64_MAX, lead=(uint64_t)p->config.lead_ms*MS;
    for (uint32_t i=0;i<count;i++) {
        uint64_t end=add_sat(t[i].check_latest_ns,(uint64_t)p->config.hold_ms*MS);
        if (end<f->now_ns || !protective(t[i].prayer)) continue;
        if (!(f->available_mask&RS3_PRAYER_BIT(t[i].prayer)))
            return block(p,d,RS3_PRAYER_UNAVAILABLE);
        uint64_t start=sub_sat(t[i].check_earliest_ns,lead);
        if (start<=f->now_ns) {
            if (desired && desired!=t[i].prayer) return block(p,d,RS3_PRAYER_CONFLICT);
            desired=t[i].prayer;
            if (!selected || t[i].check_earliest_ns<selected->check_earliest_ns) selected=t+i;
            uint64_t wake=add_sat(end,1); if (!d->next_wakeup_ns || wake<d->next_wakeup_ns) d->next_wakeup_ns=wake;
        } else if (start<next_switch) { next_switch=start; next=t+i; }
    }
    if (!desired) {
        uint64_t safe_until=sub_sat(f->coverage_until_ns,lead);
        if (next_switch<safe_until) safe_until=next_switch;
        uint64_t gap=(uint64_t)p->config.min_idle_ms*MS;
        if (f->active_prayer!=RS3_PRAYER_SOUL_SPLIT) gap=add_sat(gap,(uint64_t)p->config.ack_timeout_ms*MS);
        if (p->config.idle_prayer==RS3_PRAYER_SOUL_SPLIT &&
            (f->available_mask&RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT)) &&
            (f->flags&RS3_PRAYER_COVERAGE_KNOWN) && safe_until>f->now_ns && safe_until-f->now_ns>=gap) {
            desired=RS3_PRAYER_SOUL_SPLIT; d->next_wakeup_ns=safe_until;
        } else if (next && (f->flags&RS3_PRAYER_COVERAGE_KNOWN) &&
                   f->coverage_until_ns>=next->check_earliest_ns) {
            /* An insufficient healing gap favours early protection. */
            desired=next->prayer; selected=next; d->next_wakeup_ns=next_switch;
        } else {
            p->proposal=0; d->action=RS3_PRAYER_HOLD; d->prayer=f->active_prayer;
            d->reason=RS3_PRAYER_INCOMPLETE_COVERAGE; return RS3_OK;
        }
    }
    d->prayer=desired;
    if (selected) {
        d->threat_id=selected->event_id; d->check_earliest_ns=selected->check_earliest_ns;
        d->check_latest_ns=selected->check_latest_ns;
        if (f->now_ns>=selected->check_earliest_ns && f->active_prayer!=desired) {
            d->flags|=RS3_PRAYER_DEADLINE_MISSED; d->reason=RS3_PRAYER_LATE;
        }
    }
    if (desired==f->active_prayer) { p->proposal=0; d->action=RS3_PRAYER_HOLD; return RS3_OK; }
    if (!p->proposal || p->proposed_prayer!=desired) {
        if (p->serial==UINT64_MAX) return block(p,d,RS3_PRAYER_BAD_FRAME);
        p->proposal=++p->serial; p->proposed_prayer=desired;
    }
    p->proposed_at=f->now_ns; d->command_id=p->proposal; d->action=RS3_PRAYER_SET;
    return RS3_OK;
}
