/* Numeric prayer flags from locally decoded VarBit definitions, not pixels. */
#include "internal.h"
#include "profile_generated.h"

_Static_assert(sizeof(rs3_npc_action_event)==80, "ABI NPC action event layout");

static const uint32_t prayer_vars[]={3272,3275,3273,5859};
uint32_t rs3_combat_sizeof(uint32_t kind) {
    switch (kind) {
    case 1: return sizeof(rs3_combat_state);
    case 2: return sizeof(rs3_npc_animation);
    case 3: return sizeof(rs3_npc_action_event);
    case 4: return sizeof(rs3_player_hit_info);
    case 5: return sizeof(rs3_player_hit_event);
    default: return 0;
    }
}
uint32_t rs3_combat_active_prayer(const rs3_combat_state *s) {
    if (!s || s->status==RS3_UNAVAILABLE || s->status==RS3_NO_SAMPLE) return RS3_PRAYER_UNKNOWN;
    uint32_t active=s->active_mask&RS3_PRAYER_OVERHEADS;
    if (active && !(active&(active-1)) && (s->known_mask&active))
        for (uint32_t p=RS3_PRAYER_MELEE;p<=RS3_PRAYER_NECROMANCY;p++)
            if (active==RS3_PRAYER_BIT(p)) return p;
    if (!active && (s->known_mask&RS3_PRAYER_OVERHEADS)==RS3_PRAYER_OVERHEADS) return RS3_PRAYER_NONE;
    return RS3_PRAYER_UNKNOWN;
}
int32_t rs3_snapshot_combat(const rs3_snapshot *s,rs3_combat_state *out,uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT;
    if (size<sizeof *out) return RS3_E_ABI;
    *out=s->combat; return RS3_OK;
}
int32_t rs3_snapshot_npc_animations(const rs3_snapshot *s,rs3_npc_animation *out,
    uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT;
    *required=s->info.npc_count;
    if (capacity<*required) return RS3_E_BUFFER;
    if (*required && !out) return RS3_E_ARGUMENT;
    if (*required) memcpy(out,s->animations,*required*sizeof *out);
    return RS3_OK;
}

/* Called with the event lock held, after frame and NPC identity verification. */
void npc_action_commit(rs3_context *c,const rs3_snapshot *s,npc_identity *state,uint32_t index) {
    const rs3_npc_animation *animation=&s->animations[index];
    uint64_t epoch=s->combat.scene_epoch;
    if (!epoch || !animation->valid) {
        state->action_epoch=0;
        return;
    }

    int has_stamp=c->profile->npc_action_stamp_offset!=0;
    int32_t action=animation->action_sequence;
    uint32_t stamp=c->action_stamps[index];
    // The client writes the stamp after applying the sequence. On that profile
    // use the stamp as the edge so one application cannot emit twice.
    int applied=has_stamp?stamp!=state->action_stamp:action!=state->action_sequence;
    if (state->action_epoch==epoch && state->action_sample+1==s->info.sequence &&
        action>=0 && applied) {
        uint64_t sequence=++c->action_event_sequence;
        rs3_npc_action_event *event=c->action_events+(sequence-1)%EVENT_CAPACITY;
        *event=(rs3_npc_action_event){
            .sequence=sequence, .observed_ns=monotonic_ns(), .sample_sequence=s->info.sequence,
            .scene_epoch=epoch, .process_creation_time=c->creation_time, .instance_id=state->instance,
            .pid=c->pid, .runtime_id=animation->runtime_id, .config_id=animation->config_id,
            .previous_action_sequence=state->action_sequence, .action_sequence=action,
            .logic_tick=s->info.logic_tick, .application_stamp=stamp,
            .flags=has_stamp?RS3_NPC_ACTION_HAS_APPLICATION_STAMP:0,
        };
    }
    state->action_epoch=epoch;
    state->action_sample=s->info.sequence;
    state->action_sequence=action;
    state->action_stamp=stamp;
}

int32_t rs3_npc_action_events_since(rs3_context *c,uint64_t after,rs3_npc_action_event *out,
    uint32_t capacity,uint32_t *written,uint64_t *next,uint64_t *lost) {
    if (!c || !written || !next || !lost || (capacity && !out)) return RS3_E_ARGUMENT;
    AcquireSRWLockShared(&c->events_lock);
    uint64_t latest=c->action_event_sequence;
    *written=0; *next=after; *lost=0;
    if (after>latest) {
        ReleaseSRWLockShared(&c->events_lock);
        return RS3_E_ARGUMENT;
    }
    uint64_t retained=latest-after;
    if (retained>EVENT_CAPACITY) {
        *lost=retained-EVENT_CAPACITY;
        *next+=*lost;
        retained=EVENT_CAPACITY;
    }
    *written=retained<capacity?(uint32_t)retained:capacity;
    for (uint32_t i=0;i<*written;i++)
        out[i]=c->action_events[(*next+i)%EVENT_CAPACITY];
    *next+=*written;
    ReleaseSRWLockShared(&c->events_lock);
    return RS3_OK;
}
static int read_prayers(rs3_context *c,uint64_t player,int32_t raw[4],uint32_t *valid) {
    unsigned char header[16],after[16]; uint64_t sentinel;
    *valid=0;
    if (!rd(c,player+P_PLAYER_TABLE_OFFSET,header,16)) return 0;
    uint64_t table=u64(header),count=u64(header+8);
    if (!table || !count || count>1000000 || !rd(c,table+count*8,&sentinel,8)) return 0;
    for (uint32_t i=0;i<4;i++) {
        int present; int64_t value=0;
        if (!variable(c,table,count,sentinel,prayer_vars[i],&value,&present)) return 0;
        if (present) {
            if (value<INT32_MIN || value>INT32_MAX) return 0;
            raw[i]=(int32_t)value; *valid|=1u<<i;
        }
    }
    return rd(c,player+P_PLAYER_TABLE_OFFSET,after,16) && !memcmp(header,after,16);
}
static void overhead_bit(rs3_combat_state *s,uint32_t prayer,uint32_t normal_bit,uint32_t curse_bit) {
    uint32_t bit=RS3_PRAYER_BIT(prayer);
    int normal=(s->raw_valid_mask&1) && ((uint32_t)s->normal_raw&normal_bit);
    int curse=(s->raw_valid_mask&2) && ((uint32_t)s->curses_raw&curse_bit);
    if (normal || curse) { s->known_mask|=bit; s->active_mask|=bit; }
    else if ((s->raw_valid_mask&3)==3) s->known_mask|=bit;
}
void combat_stage(rs3_context *c,rs3_snapshot *s,uint64_t player,uint64_t world) {
    /* An optional decode failure must not discard already verified HP/NPCs. */
    uint32_t reason=c->reason; char error[192]; memcpy(error,c->error,sizeof error);
    int32_t a[4]={0},b[4]={0}; uint32_t va=0,vb=0;
    rs3_combat_state *r=&s->combat; memset(r,0,sizeof *r);
    r->status=RS3_PARTIAL; r->reason=RS3_NOT_SET;
    int32_t scene, after_scene;
    if (rd(c,world+P_GAME_STATE_OFFSET,&scene,4)) r->scene_state=scene;
    int scene_active=r->scene_state==(int32_t)P_IN_GAME_STATE;
    int prayers_ok=scene_active && read_prayers(c,player,a,&va) && read_prayers(c,player,b,&vb) &&
        va==vb && !memcmp(a,b,sizeof a);
    // Verify the scene independently of optional prayer reads.
    if (!scene_active) { r->status=RS3_UNAVAILABLE; }
    else if (!rd(c,world+P_GAME_STATE_OFFSET,&after_scene,4) || after_scene!=r->scene_state) {
        r->scene_state=0; r->status=RS3_UNAVAILABLE; r->reason=RS3_READ_FAILED;
    }
    else if (!prayers_ok) { r->status=RS3_UNAVAILABLE; r->reason=RS3_READ_FAILED; }
    else {
        r->normal_raw=a[0]; r->curses_raw=a[1]; r->normal_necro_raw=a[2]; r->curses_necro_raw=a[3];
        r->raw_valid_mask=va;
        overhead_bit(r,RS3_PRAYER_MAGIC,1u<<6,1u<<7);
        overhead_bit(r,RS3_PRAYER_RANGED,1u<<7,1u<<8);
        overhead_bit(r,RS3_PRAYER_MELEE,1u<<8,1u<<9);
        if (va&2) { r->known_mask|=RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT);
            if ((uint32_t)a[1]&(1u<<18)) r->active_mask|=RS3_PRAYER_BIT(RS3_PRAYER_SOUL_SPLIT); }
        uint32_t necro=RS3_PRAYER_BIT(RS3_PRAYER_NECROMANCY);
        if (((va&4) && ((uint32_t)a[2]&(1u<<3))) || ((va&8) && ((uint32_t)a[3]&(1u<<17)))) {
            r->known_mask|=necro; r->active_mask|=necro;
        } else if ((va&12)==12) r->known_mask|=necro;
        if (rs3_combat_active_prayer(r)!=RS3_PRAYER_UNKNOWN) { r->status=RS3_AVAILABLE; r->reason=RS3_REASON_NONE; }
    }
    c->reason=reason; memcpy(c->error,error,sizeof error);
}
void combat_commit(rs3_context *c,rs3_snapshot *s,uint64_t world) {
    if (s->combat.scene_state!=(int32_t)P_IN_GAME_STATE) {
        c->combat_world=0; s->combat.scene_epoch=0; return;
    }
    if (c->combat_world!=world || c->combat_creation!=c->creation_time) {
        c->combat_world=world; c->combat_creation=c->creation_time; ++c->combat_epoch;
    }
    s->combat.scene_epoch=c->combat_epoch;
}
