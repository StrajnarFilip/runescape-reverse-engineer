/* Deterministic decoder fixtures, not a test-only exported remote-memory API. */
/* Assertions must run even when the toolchain defines NDEBUG for -O2. */
#undef NDEBUG
#include <assert.h>
#include "../src/rs3_native.c"

typedef struct block { uint64_t base; size_t size; unsigned char *data; } block;
typedef struct fixture {
    block blocks[40]; unsigned count;
    uint64_t trigger, target; unsigned trigger_count, mutate_on; uint32_t replacement;
    rs3_context *c; rs3_snapshot *s;
} fixture;
#define BASE 0x140000000ull
#define PLAYER 0x200000ull
#define CLIENT 0x300000ull
#define WORLD 0x400000ull
#define MANAGER 0x500000ull
#define ACTOR 0x600000ull
#define PLAYER_TABLE 0x700000ull
#define CLIENT_TABLE 0x710000ull
#define VARS 0x720000ull
#define NPC_TABLE 0x730000ull
#define NPC_NODE 0x740000ull
#define CONFIG 0x750000ull
#define RING 0x760000ull
#define RECORD 0x770000ull
#define SKILLS 0x780000ull
#define ENTRIES 0x790000ull
#define DOMAINS 0x800000ull
#define FUNCTIONS 0x810000ull
#define VTABLES 0x820000ull

static void add(fixture *f,uint64_t base,size_t size) {
    assert(f->count<40); block *b=f->blocks+f->count++;
    b->base=base; b->size=size; b->data=calloc(1,size); assert(b->data);
}
static unsigned char *at(fixture *f,uint64_t a,size_t size) {
    for(unsigned i=0;i<f->count;i++) {
        block *b=f->blocks+i;
        if(a>=b->base && a-b->base<=b->size && size<=b->size-(size_t)(a-b->base)) return b->data+(size_t)(a-b->base);
    }
    return NULL;
}
static void put32(fixture *f,uint64_t a,uint32_t value) { unsigned char *p=at(f,a,4); assert(p); memcpy(p,&value,4); }
static void put64(fixture *f,uint64_t a,uint64_t value) { unsigned char *p=at(f,a,8); assert(p); memcpy(p,&value,8); }
static int fake_read(void *user,uint64_t a,void *out,size_t size) {
    fixture *f=user;
    if(a==f->trigger && ++f->trigger_count==f->mutate_on) put32(f,f->target,f->replacement);
    unsigned char *p=at(f,a,size); if(!p)return 0; memcpy(out,p,size); return 1;
}
static fixture *make_fixture(void) {
    fixture *f=calloc(1,sizeof *f); assert(f);
    assert(rs3_create(NULL,&f->c)==RS3_OK); assert(rs3_snapshot_create(&f->s)==RS3_OK);
    f->c->read=fake_read; f->c->read_user=f; f->c->module_base=BASE; f->c->pid=123; f->c->creation_time=456;
    add(f,BASE+P_REGISTRY_RVA+P_REGISTRY_ROOT_OFFSET,8); add(f,DOMAINS,512);
    add(f,FUNCTIONS,512); add(f,VTABLES,512);
    put64(f,BASE+P_REGISTRY_RVA+P_REGISTRY_ROOT_OFFSET,DOMAINS);
    for(unsigned i=0;i<2;i++) {
        uint64_t node=DOMAINS+i*256,fun=FUNCTIONS+i*256,vt=VTABLES+i*256;
        put32(f,node+P_TREE_KEY_OFFSET,i?P_CLIENT_ID:P_PLAYER_ID);
        put64(f,node+P_FUNCTION_POINTER_OFFSET,fun); put64(f,fun,vt);
        put64(f,vt+16,BASE+(i?P_CLIENT_HANDLER_RVA:P_PLAYER_HANDLER_RVA));
        put64(f,fun+P_FUNCTION_CAPTURE_OFFSET,i?CLIENT:PLAYER);
    }
    put64(f,DOMAINS+P_TREE_LEFT_OFFSET,DOMAINS+256);
    add(f,PLAYER,P_PLAYER_COUNT_OFFSET+8); add(f,CLIENT,0x7400); add(f,WORLD,P_NPC_MANAGER_OFFSET+8);
    put64(f,CLIENT,BASE+P_CLIENT_VTABLE_RVA); put64(f,CLIENT+P_STATE_POINTER_OFFSET,WORLD);
    put32(f,WORLD+P_LOGIC_CLOCK_OFFSET,1005);
    add(f,SKILLS,32); add(f,ENTRIES,6*P_SKILL_STRIDE);
    put64(f,CLIENT+P_SKILLS_MANAGER_OFFSET,SKILLS); put64(f,SKILLS+P_SKILLS_ENTRIES_OFFSET,ENTRIES);
    put32(f,ENTRIES+5*P_SKILL_STRIDE+P_SKILL_BASE_LEVEL_OFFSET,84);
    add(f,PLAYER_TABLE,98*8); add(f,CLIENT_TABLE,98*8); add(f,VARS,48*10);
    put64(f,PLAYER+P_PLAYER_TABLE_OFFSET,PLAYER_TABLE); put64(f,PLAYER+P_PLAYER_COUNT_OFFSET,97);
    put64(f,CLIENT+P_CLIENT_TABLE_OFFSET,CLIENT_TABLE); put64(f,CLIENT+P_CLIENT_COUNT_OFFSET,97);
    put64(f,PLAYER_TABLE+97*8,UINT64_MAX); put64(f,CLIENT_TABLE+97*8,UINT64_MAX);
    int64_t values[10]={8700,8700,7635,500,0,0,3,1000,1090,0};
    for(unsigned i=0;i<10;i++) {
        uint64_t table=(i==7 || i==8)?CLIENT_TABLE:PLAYER_TABLE;
        uint64_t node=VARS+i*48,bucket=table+(p_variable_ids[i]%97)*8;
        put64(f,node+40,u64(at(f,bucket,8))); put64(f,bucket,node);
        put32(f,node,p_variable_ids[i]); put64(f,node+8,(uint64_t)values[i]);
    }
    add(f,MANAGER,P_NPC_COUNT_OFFSET+4); add(f,NPC_TABLE,98*8); add(f,NPC_NODE,32);
    put64(f,WORLD+P_NPC_MANAGER_OFFSET,MANAGER); put64(f,MANAGER,BASE+P_NPC_MANAGER_VTABLE_RVA);
    put64(f,MANAGER+16,NPC_TABLE); put64(f,MANAGER+24,97);
    put32(f,MANAGER+P_NPC_COUNT_OFFSET,1); put32(f,MANAGER+P_NPC_INDICES_OFFSET,42);
    put64(f,NPC_TABLE+42*8,NPC_NODE); put64(f,NPC_TABLE+97*8,UINT64_MAX);
    put32(f,NPC_NODE,42); put64(f,NPC_NODE+16,ACTOR);
    add(f,ACTOR,ACTOR_BYTES); add(f,CONFIG,16); add(f,RING,40); add(f,RECORD,24);
    put64(f,ACTOR,BASE+P_NPC_ENTITY_VTABLE_RVA); put32(f,ACTOR+0x88,42);
    unsigned char *name=at(f,ACTOR+P_NPC_NAME_OFFSET,24); memcpy(name,"Goblin",6); name[23]=17;
    put64(f,ACTOR+P_NPC_CONFIG_OFFSET,CONFIG); put32(f,CONFIG+8,12357);
    put32(f,ACTOR+P_NPC_HEALTH_CURRENT_OFFSET,326); put32(f,ACTOR+P_NPC_HEALTH_MAX_OFFSET,550);
    put64(f,ACTOR+P_NPC_HITMARKS_OFFSET,RING); put32(f,RING+4,1);
    put64(f,RING+24,1); put64(f,RING+32,RECORD);
    int32_t hit[6]={133,224,1060,-1,-1,60}; memcpy(at(f,RECORD,24),hit,24);
    return f;
}
static int read_fixture(fixture *f) {
    memset(f->s,0,sizeof *f->s); f->s->info.timestamp_ns=1000000000;
    f->s->info.monotonic_ns=monotonic_ns(); return decode(f->c,f->s);
}
static void dispose(fixture *f) {
    rs3_destroy(f->c); rs3_snapshot_destroy(f->s);
    for(unsigned i=0;i<f->count;i++) free(f->blocks[i].data); free(f);
}
static void player_and_sparse(void) {
    fixture *f=make_fixture(); assert(read_fixture(f));
    assert(f->s->player.raw_variables[0]==8700); assert(f->s->player.raw_variables[2]==7635);
    assert(f->s->player.gcd_remaining_ms==1700); assert(f->s->player.valid_variables==1023);
    /* Sparse UINTPTR_MAX sentinel is not followed or interpreted as zero. */
    uint64_t table=PLAYER_TABLE+(p_variable_ids[3]%97)*8;
    put64(f,table,UINT64_MAX); assert(read_fixture(f)); assert(!(f->s->player.valid_variables&(1u<<3)));
    /* Numeric int64 variants are supported, including sign. */
    put64(f,VARS+8,(uint64_t)-1); *at(f,VARS+32,1)=1;
    int64_t value=0; int present=0;
    assert(variable(f->c,PLAYER_TABLE,97,UINT64_MAX,p_variable_ids[0],&value,&present));
    assert(present && value==-1);
    dispose(f);
}
static void invalid_tables_and_profile(void) {
    fixture *f=make_fixture(); uint64_t calls=f->c->calls;
    assert(!signatures(f->c,"bad hash")); assert(f->c->calls==calls);
    put32(f,VARS,999); put64(f,VARS+40,VARS);
    int64_t value; int present;
    assert(!variable(f->c,PLAYER_TABLE,97,UINT64_MAX,p_variable_ids[0],&value,&present));
    assert(strstr(f->c->error,"cycle"));
    dispose(f); f=make_fixture();
    put32(f,NPC_NODE,43); put64(f,NPC_NODE+24,NPC_NODE); assert(read_fixture(f));
    assert(f->s->info.skipped_npcs==1 && f->s->info.npc_count==0);
    put64(f,CLIENT,BASE); assert(!read_fixture(f)); assert(f->c->reason==RS3_UNSUPPORTED_CLIENT);
    dispose(f);
}
static void hits_and_health(void) {
    fixture *f=make_fixture(); assert(read_fixture(f));
    assert(f->s->info.npc_count==1 && f->s->info.hit_count==1);
    assert(f->s->npcs[0].health_current==326 && f->s->hits[0].amount==224);
    assert(!strcmp(f->s->npcs[0].name,"Goblin")); assert(f->c->event_sequence==1);
    assert(read_fixture(f)); assert(f->c->event_sequence==1);
    put32(f,ACTOR+P_NPC_HEALTH_MAX_OFFSET,0); assert(read_fixture(f));
    assert(!(f->s->npcs[0].flags&RS3_NPC_HEALTH) && f->s->npcs[0].health_reason==RS3_NOT_TRANSMITTED);
    put32(f,ACTOR+P_NPC_HEALTH_MAX_OFFSET,550); put32(f,ACTOR+P_NPC_HEALTH_CURRENT_OFFSET,0);
    assert(read_fixture(f)); assert(f->s->npcs[0].flags&RS3_NPC_HEALTH);
    rs3_hit h; unsigned char b[24]; memcpy(b,at(f,RECORD,24),24);
    assert(decode_hit(b,999,0,&h)==1 && h.state==RS3_HIT_SCHEDULED);
    assert(decode_hit(b,1060,0,&h)==0);
    put32(f,RECORD+8,48); memcpy(b,at(f,RECORD,24),24);
    assert(decode_hit(b,UINT32_MAX,0,&h)==1 && h.state==RS3_HIT_ACTIVE);
    assert(tick_delta(48,UINT32_MAX)==49);
    dispose(f);
}
static void torn_reads_and_transitions(void) {
    fixture *f=make_fixture();
    /* A torn ring is skipped and cannot produce a false event. */
    f->trigger=RECORD; f->mutate_on=2; f->target=RECORD+4; f->replacement=550;
    assert(read_fixture(f)); assert(f->s->info.skipped_npcs==1 && f->c->event_sequence==0);
    f->trigger=0; assert(read_fixture(f)); assert(f->c->event_sequence==1);
    uint64_t instance=f->s->npcs[0].instance_id;
    put32(f,MANAGER+P_NPC_COUNT_OFFSET,0); assert(read_fixture(f)); assert(f->s->info.npc_count==0);
    put32(f,MANAGER+P_NPC_COUNT_OFFSET,1); assert(read_fixture(f));
    assert(f->s->npcs[0].instance_id!=instance && f->c->event_sequence==2);
    dispose(f);
    /* A world change after NPC reads must not commit events/identity state. */
    f=make_fixture(); f->trigger=CLIENT+P_STATE_POINTER_OFFSET; f->mutate_on=2;
    f->target=CLIENT+P_STATE_POINTER_OFFSET; f->replacement=0;
    assert(!read_fixture(f)); assert(f->c->event_sequence==0);
    put64(f,CLIENT+P_STATE_POINTER_OFFSET,WORLD); f->trigger=0;
    assert(read_fixture(f)); assert(f->c->event_sequence==1);
    dispose(f);
    /* The list transition retries once and publishes the event once. */
    f=make_fixture(); f->trigger=MANAGER+P_NPC_COUNT_OFFSET; f->mutate_on=2;
    f->target=MANAGER+P_NPC_COUNT_OFFSET; f->replacement=0;
    assert(read_fixture(f)); assert(f->s->info.npc_count==0 && f->c->event_sequence==0);
    dispose(f);
}
static void buffers_and_event_cursors(void) {
    fixture *f=make_fixture(); assert(read_fixture(f));
    uint32_t count; uint64_t next,lost; rs3_event e;
    assert(rs3_snapshot_npcs(f->s,NULL,0,&count)==RS3_E_BUFFER && count==1);
    assert(rs3_snapshot_info(f->s,(rs3_info*)&e,4)==RS3_E_ABI);
    assert(rs3_events_since(f->c,0,&e,1,&count,&next,&lost)==0 && count==1 && next==1 && lost==0);
    assert(e.hit.amount==224 && e.pid==123 && e.observed_at_ns==1000000000);
    /* A second consumer has an independent cursor. */
    assert(rs3_events_since(f->c,0,&e,1,&count,&next,&lost)==0 && count==1);
    assert(rs3_events_since(f->c,1,&e,1,&count,&next,&lost)==0 && count==0);
    for(unsigned i=0;i<EVENT_CAPACITY+2;i++) {
        put32(f,RECORD+8,1061+i); put32(f,WORLD+P_LOGIC_CLOCK_OFFSET,1051+i); assert(read_fixture(f));
    }
    assert(rs3_events_since(f->c,0,&e,1,&count,&next,&lost)==0);
    assert(lost==3 && count==1 && next==4 && e.sequence==4);
    assert(rs3_events_since(f->c,UINT64_MAX,&e,1,&count,&next,&lost)==RS3_E_ARGUMENT);
    dispose(f);
}
static void failed_sample_clears_fields(void) {
    fixture *f=make_fixture();
    f->c->process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,GetCurrentProcessId());
    assert(f->c->process); f->c->attached=1;
    assert(rs3_sample(f->c,f->s)==0 && f->s->player.valid_variables);
    put64(f,CLIENT+P_STATE_POINTER_OFFSET,0);
    assert(rs3_sample(f->c,f->s)==0 && f->s->info.status==RS3_UNAVAILABLE);
    assert(f->s->info.reason==RS3_READ_FAILED);
    assert(f->s->player.valid_variables==0 && f->s->info.npc_count==0 && f->s->info.hit_count==0);
    assert(f->c->event_sequence==1); dispose(f);
}
static void worker_lifecycle(void) {
    rs3_context *c=NULL; rs3_snapshot *s=NULL;
    rs3_options o={sizeof o,UINT32_MAX,20,0};
    assert(rs3_create(&o,&c)==0 && rs3_snapshot_create(&s)==0);
    assert(rs3_start(c,0)==RS3_E_ARGUMENT); assert(rs3_start(c,100)==0);
    assert(rs3_start(c,100)==RS3_E_STATE); assert(rs3_sample(c,s)==RS3_E_STATE);
    Sleep(60); assert(rs3_copy_latest(c,s)==0);
    assert(s->info.sequence>0 && s->info.status==RS3_UNAVAILABLE && s->player.valid_variables==0);
    assert(s->info.reason==RS3_NO_CLIENT);
    assert(s->info.npc_count==0 && s->info.hit_count==0);
    assert(rs3_stop(c)==0 && rs3_stop(c)==0); assert(rs3_sample(c,s)==0);
    rs3_destroy(c); rs3_snapshot_destroy(s);
}
int main(void) {
    player_and_sparse(); invalid_tables_and_profile(); hits_and_health();
    torn_reads_and_transitions(); buffers_and_event_cursors(); failed_sample_clears_fields(); worker_lifecycle();
    puts("PASS: 7 native test groups (decoder, races, sparse tables, events, ABI, invalidation, lifecycle)");
    return 0;
}
