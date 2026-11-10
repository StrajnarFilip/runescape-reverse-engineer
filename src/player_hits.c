/* Reconstruct the verified local actor; never infer attacker from a hit amount. */
#include "internal.h"
#include "profile_generated.h"

_Static_assert(sizeof(rs3_player_hit_info)==24,"Player hit info ABI");
_Static_assert(sizeof(rs3_player_hit_event)==96,"Player hit event ABI");

static int local_actor(rs3_context *c,uint64_t world,uint64_t *actor,uint32_t *index) {
    uint64_t context,owner,table,range[2],slot; int32_t id;
    if (!ptr(c,world+P_LOCAL_CONTEXT_OFFSET,&context) || !context ||
        !ptr(c,context,&owner) || owner!=world ||
        !rd(c,context+P_LOCAL_INDEX_OFFSET,&id,4) || id<0 || id>65535 ||
        !ptr(c,context+P_LOCAL_OVERRIDE_OFFSET,actor)) return 0;
    if (!*actor) {
        if (!ptr(c,world+P_LOCAL_TABLE_OFFSET,&table) || !table ||
            !rd(c,table+P_LOCAL_RANGE_OFFSET,range,sizeof range) ||
            !pointer_valid(range[0]) || range[1]<range[0] || (range[1]-range[0])%8 ||
            (range[1]-range[0])/8>65536 || (uint64_t)id>=(range[1]-range[0])/8 ||
            !ptr(c,range[0]+(uint64_t)id*8,&slot) || !slot ||
            !ptr(c,slot+P_LOCAL_SLOT_ENTITY_OFFSET,actor)) return 0;
    }
    uint64_t vtable; uint16_t uid;
    if (!*actor || !ptr(c,*actor,&vtable) || vtable!=c->module_base+c->profile->player_actor_vtable_rva ||
        !rd(c,*actor+P_LOCAL_UID_OFFSET,&uid,2) || uid!=(uint32_t)id) return 0;
    *index=(uint32_t)id; return 1;
}
void player_hits_stage(rs3_context *c,rs3_snapshot *s,uint64_t world) {
    rs3_player_hit_info *info=&s->player_hit_info;
    memset(info,0,sizeof *info); s->player_actor=0;
    info->status=RS3_UNAVAILABLE; info->reason=RS3_NOT_SET;
    if (!c->profile->player_actor_vtable_rva) { info->reason=RS3_UNSUPPORTED_CLIENT; return; }
    if (s->combat.scene_state!=(int32_t)P_IN_GAME_STATE) return;
    uint32_t saved_reason=c->reason; char saved_error[192]; memcpy(saved_error,c->error,sizeof saved_error);
    uint64_t actor,again,ring,after_ring; uint32_t index,after_index;
    rs3_hit hits[64]; uint32_t count=0;
    if (!local_actor(c,world,&actor,&index) || !ptr(c,actor+P_LOCAL_HITMARKS_OFFSET,&ring) ||
        !read_hitmarks(c,ring,s->info.logic_tick,hits,&count) ||
        !local_actor(c,world,&again,&after_index) || again!=actor || after_index!=index ||
        !ptr(c,actor+P_LOCAL_HITMARKS_OFFSET,&after_ring) || after_ring!=ring) info->reason=RS3_READ_FAILED;
    else {
        info->status=RS3_AVAILABLE; info->reason=RS3_REASON_NONE;
        info->player_index=index; info->hit_count=count; s->player_actor=actor;
        memcpy(s->player_hits,hits,count*sizeof *hits);
    }
    c->reason=saved_reason; memcpy(c->error,saved_error,sizeof saved_error);
}
void player_hits_commit(rs3_context *c,rs3_snapshot *s) {
    player_hit_identity *state=&c->player_hit_identity;
    if (!s->combat.scene_epoch || s->player_hit_info.status!=RS3_AVAILABLE) {
        state->sample=0; if (!s->combat.scene_epoch) state->actor=0; return;
    }
    if (state->actor!=s->player_actor || state->epoch!=s->combat.scene_epoch ||
        state->creation!=c->creation_time || state->index!=s->player_hit_info.player_index) {
        uint64_t instance=state->instance+1;
        memset(state,0,sizeof *state); state->instance=instance; state->actor=s->player_actor;
        state->epoch=s->combat.scene_epoch; state->creation=c->creation_time; state->index=s->player_hit_info.player_index;
    }
    int continuous=state->sample && state->sample+1==s->info.sequence;
    if (!continuous) state->valid_hits=0;
    s->player_hit_info.instance_id=state->instance;
    AcquireSRWLockExclusive(&c->events_lock);
    for (uint32_t i=0;i<s->player_hit_info.hit_count;i++) {
        rs3_hit *h=s->player_hits+i;
        if (h->state!=RS3_HIT_ACTIVE) continue;
        uint64_t bit=1ull<<h->slot;
        if ((state->valid_hits&bit) && !memcmp(state->previous+h->slot,h,sizeof *h)) continue;
        state->valid_hits|=bit; state->previous[h->slot]=*h;
        if (!continuous) continue;
        uint64_t sequence=++c->player_hit_event_sequence;
        c->player_hit_events[(sequence-1)%EVENT_CAPACITY]=(rs3_player_hit_event){
            .sequence=sequence,.observed_ns=monotonic_ns(),.sample_sequence=s->info.sequence,
            .scene_epoch=s->combat.scene_epoch,.process_creation_time=c->creation_time,.instance_id=state->instance,
            .pid=c->pid,.player_index=state->index,.hit=*h};
    }
    ReleaseSRWLockExclusive(&c->events_lock); state->sample=s->info.sequence;
}
int32_t rs3_snapshot_player_hit_info(const rs3_snapshot *s,rs3_player_hit_info *out,uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT;
    if (size<sizeof *out) return RS3_E_ABI;
    *out=s->player_hit_info; return RS3_OK;
}
int32_t rs3_snapshot_player_hits(const rs3_snapshot *s,rs3_hit *out,uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT;
    *required=s->player_hit_info.hit_count;
    if (capacity<*required) return RS3_E_BUFFER;
    if (*required && !out) return RS3_E_ARGUMENT;
    if (*required) memcpy(out,s->player_hits,*required*sizeof *out);
    return RS3_OK;
}
int32_t rs3_player_hit_events_since(rs3_context *c,uint64_t after,rs3_player_hit_event *out,
    uint32_t capacity,uint32_t *written,uint64_t *next,uint64_t *lost) {
    if (!c || !written || !next || !lost || (capacity && !out)) return RS3_E_ARGUMENT;
    AcquireSRWLockShared(&c->events_lock); uint64_t latest=c->player_hit_event_sequence;
    *written=0; *next=after; *lost=0;
    if (after>latest) { ReleaseSRWLockShared(&c->events_lock); return RS3_E_ARGUMENT; }
    uint64_t available=latest-after;
    if (available>EVENT_CAPACITY) { *lost=available-EVENT_CAPACITY; *next+=*lost; available=EVENT_CAPACITY; }
    *written=available<capacity?(uint32_t)available:capacity;
    for (uint32_t i=0;i<*written;i++) out[i]=c->player_hit_events[(*next+i)%EVENT_CAPACITY];
    *next+=*written; ReleaseSRWLockShared(&c->events_lock); return RS3_OK;
}
