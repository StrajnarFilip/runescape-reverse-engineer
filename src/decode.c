/* Stage gameplay fields and verify the world context before committing events. */
#include "internal.h"

#include <stdio.h>

#include "profile_generated.h"

int capture(rs3_context *c,int client,uint64_t *out) {
    uint64_t node,visited[64]; unsigned n=0;
    uint32_t id=client?P_CLIENT_ID:P_PLAYER_ID;
    uint64_t handler=client?c->profile->client_handler_rva:c->profile->player_handler_rva;
    if (!ptr(c,c->module_base+c->profile->registry_rva+P_REGISTRY_ROOT_OFFSET,&node)) return 0;
    while (node && n<64) {
        for (unsigned j=0;j<n;j++) if (visited[j]==node) return fail(c,RS3_READ_FAILED,"Domain registry cycle");
        visited[n++]=node; unsigned char b[104];
        if (!rd(c,node,b,sizeof b)) return 0;
        int32_t key=i32(b+P_TREE_KEY_OFFSET);
        if (key==(int32_t)id) {
            uint64_t function=u64(b+P_FUNCTION_POINTER_OFFSET),vtable,callback;
            if (!ptr(c,function,&vtable) || !ptr(c,vtable+16,&callback)) return 0;
            if (callback!=c->module_base+handler) return fail(c,RS3_UNSUPPORTED_CLIENT,"Domain callback profile mismatch");
            if (!ptr(c,function+P_FUNCTION_CAPTURE_OFFSET,out)) return 0;
            return *out!=0 || fail(c,RS3_READ_FAILED,"Domain context unavailable");
        }
        node=u64(b+(key<(int32_t)id?P_TREE_RIGHT_OFFSET:P_TREE_LEFT_OFFSET));
    }
    return fail(c,RS3_READ_FAILED,"Domain registry unavailable");
}
int variable(rs3_context *c,uint64_t table,uint64_t count,uint64_t sentinel,uint32_t id,int64_t *value,int *present) {
    uint64_t node,visited[1024]; unsigned n=0; *present=0;
    if (!rd(c,table+(id%count)*8,&node,8)) return 0;
    while (node && node!=sentinel) {
        if (n==1024) return fail(c,RS3_READ_FAILED,"Variable chain exceeds its bound");
        for (unsigned j=0;j<n;j++) if (visited[j]==node) return fail(c,RS3_READ_FAILED,"Variable chain cycle");
        visited[n++]=node; unsigned char b[48]; uint32_t check;
        if (!rd(c,node,b,sizeof b)) return 0;
        if (u32(b)==id) {
            if (b[32]==0) *value=i32(b+8);
            else if (b[32]==1) memcpy(value,b+8,8);
            else return fail(c,RS3_READ_FAILED,"Non-numeric gameplay variable");
            if (!rd(c,node,&check,4) || check!=id) return fail(c,RS3_READ_FAILED,"Variable changed during read");
            *present=1; return 1;
        }
        node=u64(b+40);
    }
    return 1;
}
static int variable_tables(rs3_context *c,rs3_player *p,uint64_t player,uint64_t client) {
    for (unsigned domain=0;domain<2;domain++) {
        uint64_t table,count,sentinel; unsigned char b[16];
        uint64_t context=domain?client:player;
        uint64_t table_offset=domain?P_CLIENT_TABLE_OFFSET:P_PLAYER_TABLE_OFFSET;
        if (!rd(c,context+table_offset,b,sizeof b)) return 0;
        table=u64(b); count=u64(b+8);
        if (!table || count<1 || count>1000000) return fail(c,RS3_READ_FAILED,"Variable table unavailable");
        if (!rd(c,table+count*8,&sentinel,8)) return 0;
        for (unsigned i=0;i<RS3_VARIABLE_COUNT;i++) if ((i==7 || i==8)==(domain!=0)) {
            int present;
            if (!variable(c,table,count,sentinel,p_variable_ids[i],p->raw_variables+i,&present)) return 0;
            if (present) p->valid_variables|=1ull<<i;
        }
    }
    return 1;
}
int decode(rs3_context *c,rs3_snapshot *s) {
    /* Stage fields first. Only the final context check permits event commits. */
    uint64_t player,client,state,vtable,manager,entries,after,scene=0; rs3_player *p=&s->player;
    if (c->profile->has_position && !player_scene(c, &scene))
        return fail(c, RS3_NOT_SET, "Player is outside the active game scene");
    if (!capture(c,0,&player) || !capture(c,1,&client) || !ptr(c,client,&vtable)) return 0;
    if (vtable!=c->module_base+c->profile->client_vtable_rva) return fail(c,RS3_UNSUPPORTED_CLIENT,"Client context profile mismatch");
    if (!ptr(c,client+P_SKILLS_MANAGER_OFFSET,&manager)) return 0;
    if (!manager) return fail(c,RS3_READ_FAILED,"Player statistics unavailable");
    if (!ptr(c,manager+P_SKILLS_ENTRIES_OFFSET,&entries)) return 0;
    if (!entries) return fail(c,RS3_READ_FAILED,"Player statistics unavailable");
    if (!rd(c,entries+5*P_SKILL_STRIDE+P_SKILL_BASE_LEVEL_OFFSET,&p->prayer_base_level,4)) return 0;
    if (p->prayer_base_level<1 || p->prayer_base_level>255) return fail(c,RS3_READ_FAILED,"Invalid prayer base level");
    if (!variable_tables(c,p,player,client) || !ptr(c,client+P_STATE_POINTER_OFFSET,&state)) return 0;
    if (!state) return fail(c,RS3_READ_FAILED,"Client clock unavailable");
    if (!rd(c,state+P_LOGIC_CLOCK_OFFSET,&s->info.logic_tick,4)) return 0;
    if ((p->valid_variables&1) && (p->raw_variables[0]<0 || p->raw_variables[0]>1000000))
        return fail(c,RS3_READ_FAILED,"Implausible player life points");
    if ((p->valid_variables&(1u<<3)) && (p->raw_variables[3]<0 || p->raw_variables[3]>10000))
        return fail(c,RS3_READ_FAILED,"Implausible adrenaline");
    p->gcd_reason=RS3_NOT_SET;
    if ((p->valid_variables&0x180)==0x180) {
        int32_t duration=tick_delta((uint32_t)p->raw_variables[8],(uint32_t)p->raw_variables[7]);
        int32_t elapsed=tick_delta(s->info.logic_tick,(uint32_t)p->raw_variables[7]);
        if (duration>=0 && duration<=10000 && elapsed>=0) {
            p->gcd_reason=0; p->gcd_duration_ms=duration*P_LOGIC_TICK_MS;
            p->gcd_remaining_ms=elapsed<duration?(duration-elapsed)*P_LOGIC_TICK_MS:0;
        } else p->gcd_reason=RS3_INCONSISTENT_CLOCK;
    }
    s->info.status=RS3_AVAILABLE;
    int npc_ok=0; uint32_t ids[1024],npc_count=0; uint64_t npc_manager=0;
    for (unsigned attempt=0;attempt<2;attempt++) {
        if (npcs_once(c,s,state,ids,&npc_count,&npc_manager)) { npc_ok=1; break; }
        if (strcmp(c->error,"NPC list changed during read")) break;
    }
    if (!npc_ok) {
        s->info.npc_count=s->info.hit_count=0; s->info.loaded_npcs=0;
        s->info.status=RS3_PARTIAL; s->info.reason=RS3_READ_FAILED;
        snprintf(s->info.message,sizeof s->info.message,"NPCs: %s",c->error);
    } else if (s->info.skipped_npcs) s->info.status=RS3_PARTIAL;
    inventory_stage(c,s,client,state);
    if (!capture(c,0,&after) || after!=player || !capture(c,1,&after) || after!=client ||
        !ptr(c,client+P_STATE_POINTER_OFFSET,&after) || after!=state ||
        (scene && (!player_scene(c, &after) || after != scene)))
        return fail(c,RS3_READ_FAILED,"World context changed during read");
    if (npc_ok) commit_npcs(c,s,ids,npc_count,npc_manager,state);
    inventory_commit(c,s,state);
    uint64_t now=s->info.monotonic_ns;
    if (c->clock_context!=state || c->last_clock!=s->info.logic_tick) {
        c->clock_context=state; c->last_clock=s->info.logic_tick; c->clock_changed=now;
    }
    p->clock_stalled_ns=now-c->clock_changed;
    unsigned indices[3]={0,2,3}; uint64_t *changes[3]={&p->hp_changed_ns,&p->prayer_changed_ns,&p->adrenaline_changed_ns};
    for (unsigned j=0;j<3;j++) {
        unsigned i=indices[j]; uint32_t bit=1u<<j;
        if (!(p->valid_variables&(1ull<<i))) { c->previous_valid&=~bit; continue; }
        int64_t value=p->raw_variables[i]; if (i==2) value&=32767;
        if (!(c->previous_valid&bit) || value!=c->previous_values[j]) c->changed_at[j]=now;
        c->previous_values[j]=value; c->previous_valid|=bit; *changes[j]=c->changed_at[j];
    }
    return 1;
}
