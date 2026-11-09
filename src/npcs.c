/* Decode and commit observations from the loaded NPC registry. */
#include "internal.h"

#include <stdlib.h>

#include "profile_generated.h"

#define ACTOR_BYTES (P_NPC_HEALTH_MAX_OFFSET + 4u)

void reset_identities(rs3_context *c) {
    memset(c->identities,0,sizeof(npc_identity)*RS3_MAX_NPCS);
    memset(c->identity_index,0,sizeof c->identity_index); c->npc_context=c->npc_state=0;
}
static int npc_name(rs3_context *c,uint64_t actor,const unsigned char *b,rs3_npc *npc) {
    // The high bit of the final byte selects a pointer-backed string.
    // Inline strings store the unused byte count in that tag.
    const unsigned char *s=b+P_NPC_NAME_OFFSET; uint32_t tag=s[23];
    uint64_t n=tag&128?u64(s+8):23-tag, data=u64(s);
    if (n>512) return fail(c,RS3_READ_FAILED,"Invalid NPC name length");
    if (!n) return 1;
    if (!(tag&128)) memcpy(npc->name,s,(size_t)n);
    else if (data>=actor && data-actor<=ACTOR_BYTES && n<=ACTOR_BYTES-(data-actor)) memcpy(npc->name,b+(size_t)(data-actor),(size_t)n);
    else if (!rd(c,data,npc->name,(size_t)n)) return 0;
    for (uint32_t i=0;i<n;i++) if ((unsigned char)npc->name[i]<32) return fail(c,RS3_READ_FAILED,"Invalid NPC name");
    if (!valid_utf8(npc->name,(uint32_t)n)) return fail(c,RS3_READ_FAILED,"Invalid NPC name encoding");
    npc->name[n]=0; npc->name_length=(uint32_t)n; npc->flags|=RS3_NPC_NAME; return 1;
}
int decode_hit(const unsigned char *b,uint32_t tick,uint32_t slot,rs3_hit *h) {
    int32_t type=i32(b),amount=i32(b+4),duration=i32(b+20); uint32_t end=u32(b+8);
    // Skip empty or expired slots. Future hits can still be present in the ring.
    if (type<0 || duration<=0 || tick_delta(end,tick)<=0) return 0;
    if (type>1000000 || amount<0 || duration>100000) return -1;
    memset(h,0,sizeof *h); h->slot=slot; h->type_id=type; h->amount=amount;
    h->secondary_type_id=i32(b+12); h->secondary_amount=i32(b+16);
    h->end_tick=end; h->duration_ticks=(uint32_t)duration; h->start_tick=end-(uint32_t)duration;
    h->state=tick_delta(tick,h->start_tick)>=0?RS3_HIT_ACTIVE:RS3_HIT_SCHEDULED; return 1;
}
static int npc_hits(rs3_context *c,uint64_t ring,uint32_t tick,rs3_snapshot *s,rs3_npc *npc) {
    if (!ring) return 1;
    unsigned char header[40],records[64*24],check[64*24],after[16];
    if (!rd(c,ring,header,sizeof header)) return 0;
    uint64_t count=u64(header+24),data=u64(header+32); uint32_t capacity=u32(header+4);
    if (count>capacity || capacity>64 || (count && !data)) return fail(c,RS3_READ_FAILED,"Invalid NPC hitmark ring");
    if (count && (!rd(c,data,records,(size_t)count*24) || !rd(c,data,check,(size_t)count*24))) return 0;
    if ((count && memcmp(records,check,(size_t)count*24)) || !rd(c,ring+24,after,16) || memcmp(after,header+24,16))
        return fail(c,RS3_READ_FAILED,"NPC hitmark ring changed during read");
    for (uint32_t slot=0;slot<count;slot++) {
        rs3_hit h; int result=decode_hit(records+slot*24,tick,slot,&h);
        if (result<0) return fail(c,RS3_READ_FAILED,"Invalid hitmark record");
        if (result) { s->hits[s->info.hit_count++]=h; npc->hit_count++; }
    }
    return 1;
}
static int npc_node(rs3_context *c,uint64_t table,uint64_t count,uint64_t sentinel,uint32_t uid,uint64_t *address,unsigned char out[32]) {
    uint64_t node=c->buckets[uid%count],visited[1024]; unsigned n=0;
    (void)table;
    while (node && node!=sentinel) {
        if (n==1024) return fail(c,RS3_READ_FAILED,"NPC bucket chain exceeds its bound");
        for (unsigned j=0;j<n;j++) if (visited[j]==node) return fail(c,RS3_READ_FAILED,"NPC bucket chain cycle");
        visited[n++]=node;
        if (!rd(c,node,out,32)) return 0;
        if (u32(out)==uid) { *address=node; return 1; }
        node=u64(out+24);
    }
    return fail(c,RS3_READ_FAILED,"NPC left the registry");
}
static int npc_one(rs3_context *c,rs3_snapshot *s,uint64_t table,uint64_t count,uint64_t sentinel,uint32_t uid) {
    /* Decode fixed actor fields from one bulk read, then verify identity/ring.
     * A failure rolls back this NPC's hit slice without touching other NPCs. */
    unsigned char node[32], after[32], actor[ACTOR_BYTES];
    uint64_t address = 0, object, config, check_vtable, check_ring;
    uint16_t check_uid;
    rs3_npc npc={0}; npc.config_id=-1; npc.runtime_id=uid; npc.hit_offset=s->info.hit_count;
    if (!npc_node(c,table,count,sentinel,uid,&address,node)) return 0;
    object=u64(node+16);
    if (!rd(c,object,actor,sizeof actor)) return 0;
    if (u64(actor)!=c->module_base+c->profile->npc_entity_vtable_rva || (u32(actor+0x88)&65535)!=uid)
        return fail(c,RS3_READ_FAILED,"NPC identity mismatch");
    config=u64(actor+P_NPC_CONFIG_OVERRIDE_OFFSET);
    if (!config) config=u64(actor+P_NPC_CONFIG_OFFSET);
    if (config) {
        if (!rd(c,config+8,&npc.config_id,4)) return 0;
        if (npc.config_id<0 || npc.config_id>1000000) return fail(c,RS3_READ_FAILED,"Invalid NPC config ID");
        npc.flags|=RS3_NPC_CONFIG;
    }
    if (!npc_name(c,object,actor,&npc)) return 0;
    npc.health_current=i32(actor+P_NPC_HEALTH_CURRENT_OFFSET); npc.health_max=i32(actor+P_NPC_HEALTH_MAX_OFFSET);
    npc.health_reason=RS3_NOT_TRANSMITTED;
    if (npc.health_max) {
        if (npc.health_max>0 && npc.health_max<=0xffffff && npc.health_current>=0 && npc.health_current<=npc.health_max) {
            npc.flags|=RS3_NPC_HEALTH; npc.health_reason=RS3_REASON_NONE;
        } else npc.health_reason=RS3_INVALID_STATS;
    }
    if (!npc_hits(c,u64(actor+P_NPC_HITMARKS_OFFSET),s->info.logic_tick,s,&npc)) goto rollback;
    if (!rd(c,address,after,32) || !rd(c,object+0x88,&check_uid,2) || check_uid!=uid ||
        !ptr(c,object,&check_vtable) || check_vtable!=u64(actor) ||
        !ptr(c,object+P_NPC_HITMARKS_OFFSET,&check_ring) || check_ring!=u64(actor+P_NPC_HITMARKS_OFFSET) ||
        u32(after)!=uid || u64(after+16)!=object) goto rollback;
    c->actors[s->info.npc_count]=object; s->npcs[s->info.npc_count++]=npc; return 1;
rollback:
    s->info.hit_count=npc.hit_offset; return fail(c,RS3_READ_FAILED,"NPC changed during read");
}
static npc_identity *identity(rs3_context *c,uint32_t uid,uint64_t actor) {
    uint16_t index=c->identity_index[uid]; npc_identity *state=NULL;
    // The runtime ID index is one-based so zero can mean no tracked instance.
    if (index) state=c->identities+index-1;
    else for (unsigned i=0;i<RS3_MAX_NPCS;i++) if (!c->identities[i].used) {
        state=c->identities+i; c->identity_index[uid]=(uint16_t)(i+1); break;
    }
    if (!state) return NULL;
    if (!state->used || state->actor!=actor) {
        memset(state,0,sizeof *state); state->used=1; state->actor=actor;
        state->uid=uid; state->instance=++c->next_instance;
    }
    return state;
}
/* Commit identities/events only after every frame-level consistency check. */
void commit_npcs(rs3_context *c,rs3_snapshot *s,const uint32_t *ids,uint32_t count,uint64_t manager,uint64_t world_state) {
    unsigned char present[65536/8]={0};
    if (c->npc_context!=manager || c->npc_state!=world_state) {
        reset_identities(c); c->npc_context=manager; c->npc_state=world_state;
    }
    // Use the registry list to retire identities. A skipped actor read alone
    // does not mean the NPC despawned.
    for (uint32_t i=0;i<count;i++) present[ids[i]/8]|=(unsigned char)(1u<<(ids[i]%8));
    for (unsigned i=0;i<RS3_MAX_NPCS;i++) if (c->identities[i].used &&
        !(present[c->identities[i].uid/8]&(1u<<(c->identities[i].uid%8)))) {
        c->identity_index[c->identities[i].uid]=0; c->identities[i].used=0;
    }
    AcquireSRWLockExclusive(&c->events_lock);
    for (uint32_t i=0;i<s->info.npc_count;i++) {
        rs3_npc *npc=s->npcs+i; npc_identity *state=identity(c,npc->runtime_id,c->actors[i]);
        if (!state) continue;
        npc->instance_id=state->instance;
        for (uint32_t j=0;j<npc->hit_count;j++) {
            rs3_hit *h=s->hits+npc->hit_offset+j;
            if (h->state!=RS3_HIT_ACTIVE) continue;
            // Suppress a hit if this instance's ring slot still holds the same record.
            uint64_t bit=1ull<<h->slot;
            if ((state->valid_hits&bit) && !memcmp(state->previous+h->slot,h,sizeof *h)) continue;
            state->previous[h->slot]=*h; state->valid_hits|=bit;
            uint64_t sequence=++c->event_sequence;
            rs3_event *event=c->events+(sequence-1)%EVENT_CAPACITY;
            memset(event,0,sizeof *event); event->sequence=sequence; event->observed_at_ns=s->info.timestamp_ns;
            event->process_creation_time=c->creation_time; event->pid=c->pid;
            event->instance_id=npc->instance_id; event->runtime_id=npc->runtime_id;
            event->config_id=npc->config_id; event->name_length=npc->name_length;
            event->hit=*h; memcpy(event->name,npc->name,npc->name_length+1);
        }
    }
    s->info.event_sequence=c->event_sequence; ReleaseSRWLockExclusive(&c->events_lock);
}
int npcs_once(rs3_context *c,rs3_snapshot *s,uint64_t state,uint32_t *ids,uint32_t *out_n,uint64_t *out_manager) {
    uint64_t manager,table,count,sentinel,after_manager; uint32_t n,after_n,after_ids[1024];
    unsigned char b[16],seen[65536/8]={0}; s->info.npc_count=s->info.hit_count=s->info.skipped_npcs=0;
    if (!ptr(c,state+P_NPC_MANAGER_OFFSET,&manager)) return 0;
    if (!manager) return fail(c,RS3_READ_FAILED,"NPC manager unavailable");
    if (!rd(c,manager,b,8)) return 0;
    if (u64(b)!=c->module_base+c->profile->npc_manager_vtable_rva) return fail(c,RS3_READ_FAILED,"NPC manager profile mismatch");
    if (!rd(c,manager+P_NPC_COUNT_OFFSET,&n,4) || n>1024) return fail(c,RS3_READ_FAILED,"Invalid NPC count");
    if (n && !rd(c,manager+P_NPC_INDICES_OFFSET,ids,n*4)) return 0;
    for (uint32_t i=0;i<n;i++) {
        if (ids[i]>65535 || (seen[ids[i]/8]&(1u<<(ids[i]%8)))) return fail(c,RS3_READ_FAILED,"Invalid NPC index list");
        seen[ids[i]/8]|=(unsigned char)(1u<<(ids[i]%8));
    }
    // The loaded IDs define what to visit. The hash buckets resolve those IDs to actors.
    if (!rd(c,manager+16,b,16)) return 0;
    table=u64(b); count=u64(b+8);
    if (!table || count<1 || count>1000000) return fail(c,RS3_READ_FAILED,"NPC table unavailable");
    if (c->bucket_capacity<count) {
        void *memory=realloc(c->buckets,(size_t)count*8);
        if (!memory) return fail(c,RS3_READ_FAILED,"NPC bucket allocation failed");
        c->buckets=memory; c->bucket_capacity=(size_t)count;
    }
    if (!rd(c,table,c->buckets,(size_t)count*8) || !rd(c,table+count*8,&sentinel,8)) return 0;
    s->info.loaded_npcs=n;
    for (uint32_t i=0;i<n;i++) if (!npc_one(c,s,table,count,sentinel,ids[i])) s->info.skipped_npcs++;
    if (!rd(c,manager+P_NPC_COUNT_OFFSET,&after_n,4) || after_n!=n ||
        (n && (!rd(c,manager+P_NPC_INDICES_OFFSET,after_ids,n*4) || memcmp(ids,after_ids,n*4))) ||
        !ptr(c,state+P_NPC_MANAGER_OFFSET,&after_manager) || after_manager!=manager)
        return fail(c,RS3_READ_FAILED,"NPC list changed during read");
    *out_n=n; *out_manager=manager; return 1;
}
