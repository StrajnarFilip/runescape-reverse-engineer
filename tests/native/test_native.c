/* Deterministic decoder fixtures, not a test-only exported remote-memory API. */
/* Assertions must run even when the toolchain defines NDEBUG for -O2. */
#undef NDEBUG
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "inventory.h"
#include "profile_generated.h"

typedef struct block { uint64_t base; size_t size; unsigned char *data; } block;
typedef struct fixture {
    block blocks[64]; unsigned count;
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
    assert(f->count < sizeof f->blocks / sizeof f->blocks[0]); block *b=f->blocks+f->count++;
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
    // Mutate just before the selected read to reproduce a client update deterministically.
    if(a==f->trigger && ++f->trigger_count==f->mutate_on) put32(f,f->target,f->replacement);
    unsigned char *p=at(f,a,size); if(!p)return 0; memcpy(out,p,size); return 1;
}
static fixture *empty_fixture(void) {
    fixture *f=calloc(1,sizeof *f); assert(f);
    assert(rs3_create(NULL,&f->c)==RS3_OK); assert(rs3_snapshot_create(&f->s)==RS3_OK);
    f->c->read=fake_read; f->c->read_user=f; f->c->module_base=BASE; f->c->pid=123; f->c->creation_time=456;
    return f;
}
static fixture *make_profile_fixture(const client_profile *profile) {
    fixture *f = empty_fixture();
    f->c->profile = profile;
    // Map the two domain registry nodes and their callback captures.
    add(f,BASE+profile->registry_rva+P_REGISTRY_ROOT_OFFSET,8); add(f,DOMAINS,512);
    add(f,FUNCTIONS,512); add(f,VTABLES,512);
    put64(f,BASE+profile->registry_rva+P_REGISTRY_ROOT_OFFSET,DOMAINS);
    for(unsigned i=0;i<2;i++) {
        uint64_t node=DOMAINS+i*256,fun=FUNCTIONS+i*256,vt=VTABLES+i*256;
        put32(f,node+P_TREE_KEY_OFFSET,i?P_CLIENT_ID:P_PLAYER_ID);
        put64(f,node+P_FUNCTION_POINTER_OFFSET,fun); put64(f,fun,vt);
        put64(f,vt+16,BASE+(i?profile->client_handler_rva:profile->player_handler_rva));
        put64(f,fun+P_FUNCTION_CAPTURE_OFFSET,i?CLIENT:PLAYER);
    }
    put64(f,DOMAINS+P_TREE_LEFT_OFFSET,DOMAINS+256);
    add(f,PLAYER,P_PLAYER_COUNT_OFFSET+8); add(f,CLIENT,0x7400); add(f,WORLD,0x1a000);
    put32(f,WORLD+P_GAME_STATE_OFFSET,P_IN_GAME_STATE);
    put64(f,CLIENT,BASE+profile->client_vtable_rva); put64(f,CLIENT+P_STATE_POINTER_OFFSET,WORLD);
    put32(f,WORLD+P_LOGIC_CLOCK_OFFSET,1005);
    add(f,SKILLS,32); add(f,ENTRIES,6*P_SKILL_STRIDE);
    put64(f,CLIENT+P_SKILLS_MANAGER_OFFSET,SKILLS); put64(f,SKILLS+P_SKILLS_ENTRIES_OFFSET,ENTRIES);
    put32(f,ENTRIES+5*P_SKILL_STRIDE+P_SKILL_BASE_LEVEL_OFFSET,84);
    // Each table has 97 buckets and a final sentinel entry.
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
    // Register one loaded NPC with known HP and a single active hit record.
    add(f,MANAGER,P_NPC_COUNT_OFFSET+4); add(f,NPC_TABLE,98*8); add(f,NPC_NODE,32);
    put64(f,WORLD+P_NPC_MANAGER_OFFSET,MANAGER); put64(f,MANAGER,BASE+profile->npc_manager_vtable_rva);
    put64(f,MANAGER+16,NPC_TABLE); put64(f,MANAGER+24,97);
    put32(f,MANAGER+P_NPC_COUNT_OFFSET,1); put32(f,MANAGER+P_NPC_INDICES_OFFSET,42);
    put64(f,NPC_TABLE+42*8,NPC_NODE); put64(f,NPC_TABLE+97*8,UINT64_MAX);
    put32(f,NPC_NODE,42); put64(f,NPC_NODE+16,ACTOR);
    add(f,ACTOR,P_NPC_HEALTH_MAX_OFFSET+4u); add(f,CONFIG,16); add(f,RING,40); add(f,RECORD,24);
    put64(f,ACTOR,BASE+profile->npc_entity_vtable_rva); put32(f,ACTOR+0x88,42);
    unsigned char *name=at(f,ACTOR+P_NPC_NAME_OFFSET,24); memcpy(name,"Goblin",6); name[23]=17;
    put64(f,ACTOR+P_NPC_CONFIG_OFFSET,CONFIG); put32(f,CONFIG+8,12357);
    put32(f,ACTOR+P_NPC_HEALTH_CURRENT_OFFSET,326); put32(f,ACTOR+P_NPC_HEALTH_MAX_OFFSET,550);
    put64(f,ACTOR+P_NPC_HITMARKS_OFFSET,RING); put32(f,RING+4,1);
    put64(f,RING+24,1); put64(f,RING+32,RECORD);
    int32_t hit[6]={133,224,1060,-1,-1,60}; memcpy(at(f,RECORD,24),hit,24);
    return f;
}
static fixture *make_fixture(void) {
    return make_profile_fixture(&p_profile);
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
static void position_profile(void) {
    fixture *f = make_profile_fixture(&pos_profile);
    for (size_t i = 0; i < sizeof pos_signatures / sizeof pos_signatures[0]; i++) {
        const profile_signature *signature = &pos_signatures[i];
        uint64_t address = BASE + signature->rva;
        add(f, address, signature->length);
        memcpy(at(f, address, signature->length), signature->bytes, signature->length);
    }
    unsigned char *first = at(f, BASE + pos_signatures[0].rva, 1);
    *first ^= 1;
    assert(!signatures(f->c, POS_SHA256) && !f->c->profile);
    *first ^= 1;
    assert(signatures(f->c, POS_SHA256) && f->c->profile->has_position);

    uint64_t scene = 0x900000, context = 0xa00000, table = 0xb00000;
    uint64_t slots = 0xc00000, slot = 0xd00000, actor = 0xe00000;
    add(f, BASE + POS_CLIENT_PTR_RVA, 8);
    add(f, scene, POS_PLAYER_CONTEXT + 8);
    add(f, context, POS_PLAYER_OVERRIDE + 8);
    add(f, table, POS_TABLE_RANGE + 16);
    add(f, slots, 16);
    add(f, slot, POS_SLOT_ENTITY + 8);
    add(f, actor, POS_FINE_POSITION + 12);
    put64(f, BASE + POS_CLIENT_PTR_RVA, scene);
    put32(f, scene + POS_STATE, POS_IN_GAME_STATE);
    put64(f, scene + POS_PLAYER_CONTEXT, context);
    put64(f, context, scene);
    put64(f, scene + POS_PLAYER_TABLE, table);
    put64(f, table + POS_TABLE_RANGE, slots);
    put64(f, table + POS_TABLE_RANGE + 8, slots + 16);
    put32(f, context + POS_PLAYER_INDEX, 1);
    put64(f, slots + 8, slot);
    put64(f, slot + POS_SLOT_ENTITY, actor);
    put32(f, actor + POS_PLANE, 2);
    put32(f, actor + POS_SIZE, 2);
    float fine[3] = {3166.5f * 512, 1234, 3483.5f * 512};
    memcpy(at(f, actor + POS_FINE_POSITION, sizeof fine), fine, sizeof fine);
    f->c->process = OpenProcess(PROCESS_QUERY_INFORMATION, FALSE, GetCurrentProcessId());
    assert(f->c->process);
    f->c->attached = 1;

    rs3_position position;
    assert(rs3_sample(f->c, f->s) == RS3_OK && f->s->info.status == RS3_AVAILABLE);
    assert(rs3_copy_latest(f->c, f->s) == RS3_OK);
    assert(!strcmp(f->s->info.client_version, POS_SHORT_VERSION));
    assert(!strcmp(rs3_snapshot_client_version(f->s), POS_VERSION));
    assert(f->s->player.raw_variables[RS3_HEALTH] == 8700 && f->s->player.prayer_base_level == 84);
    assert(f->s->info.npc_count == 1 && f->s->npcs[0].health_current == 326);
    assert(rs3_snapshot_position(f->s, &position, 4) == RS3_E_ABI);
    assert(rs3_snapshot_position(f->s, &position, sizeof position) == RS3_OK);
    assert(position.valid && position.x == 3165 && position.y == 3482 && position.plane == 2);

    put32(f, context + POS_PLAYER_INDEX, 2);
    assert(rs3_sample(f->c, f->s) == RS3_OK && f->s->info.status == RS3_AVAILABLE);
    assert(!f->s->position.valid && f->s->player.valid_variables);
    put64(f, context + POS_PLAYER_OVERRIDE, actor);
    put64(f, BASE + pos_profile.registry_rva + P_REGISTRY_ROOT_OFFSET, 0);
    fine[0] += 512;
    memcpy(at(f, actor + POS_FINE_POSITION, sizeof fine), fine, sizeof fine);
    assert(rs3_sample(f->c, f->s) == RS3_OK && f->s->info.status == RS3_PARTIAL);
    assert(f->s->position.valid && f->s->position.x == 3166 && !f->s->player.valid_variables);
    put32(f, scene + POS_STATE, 20);
    put64(f, BASE + pos_profile.registry_rva + P_REGISTRY_ROOT_OFFSET, DOMAINS);
    assert(rs3_sample(f->c, f->s) == RS3_OK);
    assert(rs3_copy_latest(f->c, f->s) == RS3_OK && f->s->info.status == RS3_UNAVAILABLE);
    assert(!f->s->position.valid && !f->s->player.valid_variables && !f->s->info.npc_count);
    dispose(f);
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
#define INV_MANAGER 0x830000ull
#define INV_ENTRIES 0x840000ull
#define BAG 0x850000ull
#define WORN 0x860000ull
#define EXTRA_VARS 0x870000ull
static void word(unsigned char *p,uint32_t v){memcpy(p,&v,4);}
static void slot(fixture *f,uint64_t data,unsigned index,int32_t id,uint32_t quantity) {
    put32(f,data+index*8,(uint32_t)id);put32(f,data+index*8+4,quantity);
}
static fixture *inventory_fixture(void) {
    fixture *f=make_fixture();
    add(f,WORLD+P_INV_MANAGER_OFFSET,8);put64(f,WORLD+P_INV_MANAGER_OFFSET,INV_MANAGER);
    add(f,INV_MANAGER,32);add(f,INV_ENTRIES,144);add(f,BAG,28*8);add(f,WORN,19*8);add(f,EXTRA_VARS,5*48);
    put64(f,INV_MANAGER+8,INV_ENTRIES);put64(f,INV_MANAGER+16,INV_ENTRIES+144);put64(f,INV_MANAGER+24,INV_ENTRIES+144);
    for(unsigned i=0;i<2;i++) {
        uint64_t r=INV_ENTRIES+i*72,data=i?WORN:BAG;unsigned count=i?19:28,id=93+i;
        put32(f,r,id*2);put32(f,r+16,id);put64(f,r+24,data);put64(f,r+32,data+count*8);put64(f,r+40,data+count*8);
        for(unsigned j=0;j<count;j++)slot(f,data,j,-1,0);
    }
    uint32_t ids[5]={2107,2108,2147,2148,P_INV_BONE_SHIELD_VARP},values[5]={1000,2020,1000,2500,0};
    for(unsigned i=0;i<5;i++) {
        uint64_t table=i==4?PLAYER_TABLE:CLIENT_TABLE,bucket=table+(ids[i]%97)*8,node=EXTRA_VARS+i*48;
        put32(f,node,ids[i]);put32(f,node+8,values[i]);put64(f,node+40,u64(at(f,bucket,8)));put64(f,bucket,node);
    }
    /* Tiny synthetic catalog: tests never depend on installed game definitions. */
    inventory_state *v=f->c->inventory;free(v->catalog);v->catalog=calloc(1,88+8*40+2*48+200);
    assert(v->catalog);v->item_count=8;v->ability_count=2;v->catalog_status=RS3_AVAILABLE;
    v->item_defs=v->catalog+88;v->ability_defs=v->item_defs+8*40;v->strings=(const char*)v->ability_defs+2*48;
    const char *names[]={"Sword","Off-hand sword","Two-handed sword","Shield","Defender","Test brew (6)","Test brew (5)","Other item","Flurry","Resonance"};
    uint32_t offsets[10],pos=1;
    for(unsigned i=0;i<10;i++){offsets[i]=pos;strcpy((char*)v->strings+pos,names[i]);pos+=(uint32_t)strlen(names[i])+1;}
    v->strings_size=pos;v->catalog_size=88+8*40+2*48+pos;
    uint32_t item_ids[8]={100,101,102,103,104,200,201,300};
    for(unsigned i=0;i<8;i++) {
        unsigned char *r=(unsigned char*)v->item_defs+i*40;word(r,item_ids[i]);word(r+4,offsets[i]);
        word(r+12,UINT32_MAX);word(r+16,UINT32_MAX);word(r+20,UINT32_MAX);
        if(i<5){word(r+20,i==1 || i==3 || i==4?5:3);word(r+24,1);word(r+28,i==2?3:(i==3?4:(i==4?13:1)));}
        if(i==5 || i==6){word(r+8,19);word(r+12,i==5?6:5);word(r+16,i==5?201:UINT32_MAX);word(r+32,1);word(r+36,1000);}
    }
    for(unsigned i=0;i<2;i++) {
        unsigned char *r=(unsigned char*)v->ability_defs+i*48;word(r,i?14713:14684);word(r+4,offsets[8+i]);
        word(r+8,i?2147:2107);word(r+12,i?2148:2108);word(r+16,i?30000:20400);
        word(r+20,i?RS3_REQ_SHIELD:RS3_REQ_DUAL_WIELD);word(r+24,i?0:1);word(r+28,1);word(r+44,1);
    }
    slot(f,BAG,0,200,1);slot(f,BAG,1,300,1);slot(f,WORN,3,100,1);slot(f,WORN,5,101,1);
    return f;
}
static void inventory_identity_and_doses(void) {
    fixture *f=inventory_fixture();assert(read_fixture(f));
    assert(f->s->inventory_info.backpack_count==28 && f->s->inventory_info.equipment_count==19);
    assert(f->s->items[0].doses==6 && f->s->items[0].base_heal==1000);
    uint64_t token=f->s->items[0].token;assert(token && f->c->inventory->sequence==0);
    slot(f,BAG,0,-1,0);slot(f,BAG,8,200,1);assert(read_fixture(f));
    assert(f->s->items[8].token==token && f->c->inventory->events[0].kind==RS3_ITEM_MOVED);
    slot(f,BAG,8,201,1);assert(read_fixture(f));
    rs3_item_event *e=f->c->inventory->events+1;
    assert(f->s->items[8].token==token && e->kind==RS3_ITEM_DOSES_CHANGED && e->before.doses==6 && e->after.doses==5);
    rs3_item zero_family_before=e->before,zero_family_after=e->after;
    zero_family_before.family_id=zero_family_after.family_id=0;
    assert(same_item(&zero_family_before,&zero_family_after,1));
    /* Move and consume between the same pair of observations. */
    slot(f,BAG,8,-1,0);slot(f,BAG,0,200,1);assert(read_fixture(f));
    token=f->s->items[0].token;slot(f,BAG,0,-1,0);slot(f,BAG,7,201,1);assert(read_fixture(f));
    assert(f->s->items[7].token==token && f->c->inventory->events[4].kind==RS3_ITEM_DOSES_CHANGED);
    slot(f,BAG,7,300,1);assert(read_fixture(f));assert(f->s->items[7].token!=token);
    e=f->c->inventory->events+5;assert(e->kind==RS3_ITEM_CHANGED && e->before.item_id==201 && e->after.item_id==300);
    token=f->s->items[7].token;slot(f,BAG,7,300,10);assert(read_fixture(f));
    assert(f->s->items[7].token==token && f->c->inventory->events[6].kind==RS3_ITEM_QUANTITY_CHANGED);
    uint32_t count;assert(rs3_snapshot_items(f->s,93,NULL,0,&count)==RS3_E_BUFFER && count==28);
    assert(rs3_snapshot_items(f->s,94,NULL,0,&count)==RS3_E_BUFFER && count==19);
    dispose(f);
}
static void sparse_inventory_slots(void) {
    fixture *f = inventory_fixture();
    put64(f, INV_ENTRIES + 32, BAG + 8);
    assert(read_fixture(f) && f->s->inventory_info.backpack_count == 28);
    assert(f->s->items[0].item_id == 200 && f->s->items[1].item_id == -1);

    put64(f, INV_ENTRIES + 24, 0);
    put64(f, INV_ENTRIES + 32, 0);
    put64(f, INV_ENTRIES + 40, 0);
    assert(read_fixture(f) && f->s->inventory_info.backpack_count == 28);
    for (unsigned i = 0; i < 28; i++)
        assert(f->s->items[i].item_id == -1 && f->s->items[i].quantity == 0);
    assert(f->s->inventory_info.equipment_count == 19 && f->s->items[31].item_id == 100);
    dispose(f);
}
static void ability_cooldowns_and_equipment(void) {
    fixture *f=inventory_fixture();assert(read_fixture(f));
    rs3_ability *flurry=f->s->abilities,*resonance=flurry+1;
    assert(flurry->flags&1);assert(flurry->duration_ms==20400 && flurry->remaining_ms==20300);
    assert(flurry->equipment_match==RS3_EQUIPMENT_ALLOWED && resonance->equipment_match==RS3_EQUIPMENT_BLOCKED);
    slot(f,WORN,3,102,1);slot(f,WORN,5,-1,0);assert(read_fixture(f));
    assert(flurry->equipment_match==RS3_EQUIPMENT_BLOCKED);
    word((unsigned char*)f->c->inventory->ability_defs+20,RS3_REQ_TWO_HANDED);assert(read_fixture(f));
    assert(flurry->equipment_match==RS3_EQUIPMENT_ALLOWED);
    word((unsigned char*)f->c->inventory->ability_defs+20,RS3_REQ_DUAL_WIELD);
    slot(f,WORN,3,100,1);slot(f,WORN,5,103,1);assert(read_fixture(f));
    assert(resonance->equipment_match==RS3_EQUIPMENT_ALLOWED && flurry->equipment_match==RS3_EQUIPMENT_BLOCKED);
    slot(f,WORN,5,104,1);assert(read_fixture(f));
    assert(flurry->equipment_match==RS3_EQUIPMENT_ALLOWED && resonance->equipment_match==RS3_EQUIPMENT_ALLOWED);
    slot(f,WORN,5,-1,0);put32(f,EXTRA_VARS+4*48+8,1u<<30);assert(read_fixture(f));
    assert(resonance->equipment_match==RS3_EQUIPMENT_UNKNOWN);
    word((unsigned char*)f->c->inventory->item_defs+28,RS3_WEAPON|RS3_SHIELD);assert(read_fixture(f));
    assert(resonance->equipment_match==RS3_EQUIPMENT_ALLOWED); /* Main-hand shield-capable weapon. */
    // Start just before the 32-bit wrap and sample before the post-wrap end.
    put32(f,EXTRA_VARS+8,0xfffffff0u);put32(f,EXTRA_VARS+48+8,0x50);put32(f,WORLD+P_LOGIC_CLOCK_OFFSET,0x20);
    assert(read_fixture(f));assert(flurry->remaining_ms==960 && flurry->duration_ms==1920);
    put32(f,WORLD+P_LOGIC_CLOCK_OFFSET,3000);assert(read_fixture(f));assert(flurry->remaining_ms==0);
    /* An absent sparse cooldown stays unknown; it is never fabricated ready. */
    put64(f,CLIENT_TABLE+(2107%97)*8,0);assert(read_fixture(f));assert(!(flurry->flags&1) && flurry->reason==RS3_NOT_SET);
    dispose(f);
}
static void inventory_races_and_duplicates(void) {
    fixture *f=inventory_fixture();assert(read_fixture(f));uint64_t sequence=f->c->inventory->sequence;
    f->trigger=BAG;f->mutate_on=2;f->target=BAG;f->replacement=201;
    assert(read_fixture(f));assert(f->s->inventory_info.status==RS3_UNAVAILABLE);
    assert(f->s->inventory_info.backpack_count==0 && f->c->inventory->sequence==sequence);
    f->trigger=0;assert(read_fixture(f));assert(f->c->inventory->sequence==sequence+1);
    /* Two identical copies moved simultaneously cannot be uniquely identified. */
    slot(f,BAG,0,-1,0);slot(f,BAG,2,300,1);assert(read_fixture(f));
    slot(f,BAG,1,-1,0);slot(f,BAG,2,-1,0);slot(f,BAG,3,300,1);slot(f,BAG,4,300,1);assert(read_fixture(f));
    assert(f->s->items[3].flags&RS3_ITEM_AMBIGUOUS_IDENTITY);assert(f->s->items[4].flags&RS3_ITEM_AMBIGUOUS_IDENTITY);
    /* World replacement establishes a new baseline without false item additions. */
    sequence=f->c->inventory->sequence;inventory_commit(f->c,f->s,WORLD+4096);assert(f->c->inventory->sequence==sequence);
    uint32_t written;uint64_t next,lost;rs3_item_event event;
    assert(rs3_item_events_since(f->c,0,&event,1,&written,&next,&lost)==0 && written==1 && next==1 && lost==0);
    assert(rs3_item_events_since(f->c,0,&event,1,&written,&next,&lost)==0 && written==1);
    dispose(f);
}
static void catalog_validation_and_item_queue_gaps(void) {
    fixture *f=inventory_fixture();inventory_state *v=f->c->inventory;assert(read_fixture(f));
    /* A valid SHA-256 is integrity checking, not authentication. Validate every
     * record too: negative timer IDs with source=client must never index arrays. */
    unsigned char *data=malloc(v->catalog_size);assert(data);memcpy(data,v->catalog,v->catalog_size);
    memcpy(data,"RS3CAT1\0",8);word(data+8,1);word(data+12,v->item_count);word(data+16,v->ability_count);word(data+20,v->strings_size);
    for(unsigned i=0;i<32;i++){unsigned byte;assert(sscanf(&P_SHA256[i*2],"%2x",&byte)==1);data[24+i]=(unsigned char)byte;}
    word(data+88+v->item_count*40+8,UINT32_MAX);
    assert(digest_bytes(data+88,(uint32_t)v->catalog_size-88,data+56));
    wchar_t folder[MAX_PATH],path[MAX_PATH];assert(GetTempPathW(MAX_PATH,folder));assert(GetTempFileNameW(folder,L"rs3",0,path));
    HANDLE file=CreateFileW(path,GENERIC_WRITE,0,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_TEMPORARY,NULL);assert(file!=INVALID_HANDLE_VALUE);
    DWORD got;assert(WriteFile(file,data,(DWORD)v->catalog_size,&got,NULL) && got==v->catalog_size);CloseHandle(file);
    assert(catalog_load_wide(v,path)==RS3_E_ABI);assert(v->ability_count==2 && v->catalog_status==RS3_AVAILABLE);
    assert(DeleteFileW(path));free(data);
    AcquireSRWLockExclusive(&f->c->events_lock);
    for(unsigned i=0;i<ITEM_QUEUE_CAPACITY+3;i++)inventory_event(f->c,f->s,RS3_ITEM_MOVED,f->s->items,f->s->items);
    ReleaseSRWLockExclusive(&f->c->events_lock);
    uint32_t written;uint64_t next,lost;rs3_item_event event;
    assert(rs3_item_events_since(f->c,0,&event,1,&written,&next,&lost)==0);
    assert(written==1 && lost==3 && next==4 && event.sequence==4);
    dispose(f);
}
static void input_projection_and_guards(void) {
    rs3_position tile = {1, 3200, 3200, 0};
    rs3_minimap map = {
        .anchor = tile, .screen_x = -250, .screen_y = 400,
        .east_dx = 3, .east_dy = 4, .north_dx = 4, .north_dy = -3,
        .radius_pixels = 25
    };
    POINT point;
    assert(rs3_sizeof(RS3_SIZE_MINIMAP) == sizeof map);
    assert(project_minimap_tile(&tile, &map, 3203, 3204, 0, &point) == RS3_OK);
    assert(point.x == -225 && point.y == 400);
    assert(project_minimap_tile(&tile, &map, 3199, 3198, 0, &point) == RS3_OK);
    assert(point.x == -261 && point.y == 402);
    assert(project_minimap_tile(&tile, &map, 3203, 3205, 0, &point) == RS3_E_TARGET);
    assert(project_minimap_tile(&tile, &map, 3203, 3204, 1, &point) == RS3_E_TARGET);
    assert(project_minimap_tile(&tile, &map, -1, 3200, 0, &point) == RS3_E_ARGUMENT);
    tile.x++;
    assert(project_minimap_tile(&tile, &map, 3203, 3204, 0, &point) == RS3_E_STATE);
    tile.x--;
    map.north_dx = NAN;
    assert(project_minimap_tile(&tile, &map, 3203, 3204, 0, &point) == RS3_E_ARGUMENT);
    map.north_dx = -2.25;
    assert(project_minimap_tile(&tile, &map, 3203, 3204, 0, &point) == RS3_E_ARGUMENT);
    map.north_dx = 4;

    // A fresh fixture with no selected NPC or ability fails before dispatch.
    fixture *f = empty_fixture();
    assert(rs3_move_to_tile(f->s, 3200, 3200, 0, &map) == RS3_E_STATE);
    assert(rs3_interact(f->s, 1, 0, 0, 0) == RS3_E_ARGUMENT);
    assert(rs3_activate_ability(f->s, 1, VK_LWIN) == RS3_E_ARGUMENT);
    f->s->profile = &pos_profile;
    f->s->position = tile;
    f->s->info.status = RS3_AVAILABLE;
    f->s->info.pid = GetCurrentProcessId();
    f->s->info.process_creation_time = process_creation_time(GetCurrentProcess());
    f->s->info.monotonic_ns = rs3_monotonic_ns();
    assert(rs3_activate_ability(f->s, 1, 'Q') == RS3_E_TARGET);
    assert(rs3_interact(f->s, 1, RS3_ENTITY_OPEN_MENU, 0, 0) == RS3_E_TARGET);
    assert(rs3_move_to_tile(f->s, 3210, 3200, 0, &map) == RS3_E_TARGET);
    f->s->info.monotonic_ns += 5000000000ull;
    assert(rs3_interact(f->s, 0, RS3_ENTITY_SELECT_OPTION, 0, 0) == RS3_E_STATE);
    dispose(f);
}
static void combat_flags_and_animation(void) {
    fixture *f=make_fixture();
    const uint64_t vars=0xb00000;
    const uint32_t ids[]={3272,3275,3273,5859};
    add(f,vars,4*48);
    for (uint32_t i=0;i<4;i++) {
        uint64_t node=vars+i*48,bucket=PLAYER_TABLE+(ids[i]%97)*8;
        put64(f,node+40,u64(at(f,bucket,8))); put64(f,bucket,node); put32(f,node,ids[i]);
    }
    put32(f,vars+48+8,1u<<7);
    put64(f,ACTOR+P_NPC_ANIMATION_COMPONENT_OFFSET,BASE+P_NPC_ANIMATION_VTABLE_RVA);
    put32(f,ACTOR+P_NPC_ACTION_SEQUENCE_OFFSET,28929);
    put32(f,ACTOR+P_NPC_MOVEMENT_SEQUENCE_OFFSET,(uint32_t)-1);
    assert(read_fixture(f));
    rs3_combat_state c;
    assert(rs3_snapshot_combat(f->s,&c,sizeof c)==RS3_OK && c.raw_valid_mask==15);
    assert(c.known_mask==RS3_PRAYER_OVERHEADS && rs3_combat_active_prayer(&c)==RS3_PRAYER_MAGIC);
    assert(rs3_combat_sizeof(1)==sizeof c && rs3_combat_sizeof(2)==sizeof(rs3_npc_animation));
    rs3_npc_animation a; uint32_t n;
    assert(rs3_snapshot_npc_animations(f->s,NULL,0,&n)==RS3_E_BUFFER && n==1);
    assert(rs3_snapshot_npc_animations(f->s,&a,1,&n)==RS3_OK);
    assert(a.valid && a.action_sequence==28929 && a.movement_sequence==-1 && a.instance_id==f->s->npcs[0].instance_id);
    uint64_t epoch=c.scene_epoch;
    put32(f,vars+48+8,1u<<18); assert(read_fixture(f));
    assert(rs3_combat_active_prayer(&f->s->combat)==RS3_PRAYER_SOUL_SPLIT);
    put32(f,vars+48+8,0); assert(read_fixture(f));
    assert(rs3_combat_active_prayer(&f->s->combat)==RS3_PRAYER_NONE);
    // Two incompatible active flags are not a trustworthy overhead state.
    put32(f,vars+48+8,(1u<<7)|(1u<<9)); assert(read_fixture(f));
    assert(rs3_combat_active_prayer(&f->s->combat)==RS3_PRAYER_UNKNOWN);
    // Sparse absence is not proof that all overheads are off.
    put64(f,PLAYER_TABLE+(3275%97)*8,UINT64_MAX); assert(read_fixture(f));
    assert(rs3_combat_active_prayer(&f->s->combat)==RS3_PRAYER_UNKNOWN);
    f->c->combat_world=0; assert(read_fixture(f)); assert(f->s->combat.scene_epoch>epoch);
    put32(f,WORLD+P_GAME_STATE_OFFSET,10); assert(read_fixture(f));
    assert(!f->s->combat.scene_epoch && !f->s->combat.active_mask &&
           rs3_combat_active_prayer(&f->s->combat)==RS3_PRAYER_UNKNOWN);
    put32(f,WORLD+P_GAME_STATE_OFFSET,P_IN_GAME_STATE);
    put64(f,ACTOR+P_NPC_ANIMATION_COMPONENT_OFFSET,0); assert(read_fixture(f));
    assert(!f->s->animations[0].valid && (f->s->npcs[0].flags&RS3_NPC_HEALTH));
    dispose(f);
    client_profile compat=pos_profile; compat.has_position=0;
    f=make_profile_fixture(&compat); assert(read_fixture(f));
    assert(!f->s->animations[0].valid && f->s->animations[0].reason==RS3_UNSUPPORTED_CLIENT);
    dispose(f);
}
static void prayer_chords_and_partial_release(void) {
    INPUT keys[8],release[8]; uint32_t n;
    assert(build_key_chord(VK_F1,RS3_KEY_CTRL,keys,&n)==RS3_OK && n==4);
    assert(keys[0].ki.wVk==VK_CONTROL && !keys[0].ki.dwFlags);
    assert(keys[1].ki.wVk==VK_F1 && !keys[1].ki.dwFlags);
    assert(keys[2].ki.wVk==VK_F1 && keys[2].ki.dwFlags==KEYEVENTF_KEYUP);
    assert(keys[3].ki.wVk==VK_CONTROL && keys[3].ki.dwFlags==KEYEVENTF_KEYUP);
    for (uint32_t inserted=1;inserted<4;inserted++) {
        uint32_t count=release_inserted_inputs(keys,inserted,release);
        assert(count==(inserted==1?1:2));
        for (uint32_t i=0;i<count;i++) assert(release[i].ki.dwFlags&KEYEVENTF_KEYUP);
        assert(release[count-1].ki.wVk==VK_CONTROL);
    }
    assert(build_key_chord('A',7,keys,&n)==RS3_OK && n==8);
    assert(build_key_chord(VK_CONTROL,0,keys,&n)==RS3_E_ARGUMENT);
    fixture *f=empty_fixture();
    assert(rs3_set_prayer(f->s,RS3_PRAYER_MAGIC,VK_F1,RS3_KEY_CTRL)==RS3_E_STATE);
    assert(rs3_set_prayer(f->s,RS3_PRAYER_MAGIC,VK_F1,8)==RS3_E_ARGUMENT);
    dispose(f);
}
int main(void) {
    player_and_sparse(); invalid_tables_and_profile(); hits_and_health();
    torn_reads_and_transitions(); buffers_and_event_cursors(); failed_sample_clears_fields(); worker_lifecycle();
    inventory_identity_and_doses();ability_cooldowns_and_equipment();inventory_races_and_duplicates();
    catalog_validation_and_item_queue_gaps();
    position_profile();
    sparse_inventory_slots();
    input_projection_and_guards();
    combat_flags_and_animation(); prayer_chords_and_partial_release();
    puts("PASS: 16 native test groups");
    return 0;
}
