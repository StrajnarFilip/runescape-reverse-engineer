/* Read-only acquisition engine.
 *
 * One sampler (synchronous or worker) owns process/decoder/identity state.
 * control serializes lifecycle calls, latest_lock protects published frames,
 * and events_lock protects an independent bounded observation queue. Consumers
 * never access working buffers. Game addresses are used only by rd(), which
 * bounds and accounts every read; no target code is executed.
 */
#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#define RS3_BUILD
#include <windows.h>
#include <tlhelp32.h>
#include <bcrypt.h>
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include "rs3/rs3.h"
#include <profile_generated.h>

_Static_assert(sizeof(void*)==8, "Build for Windows x64");
_Static_assert(sizeof(rs3_info)==384, "ABI info layout");
_Static_assert(sizeof(rs3_player)==136, "ABI player layout");
_Static_assert(sizeof(rs3_npc)==568, "ABI NPC layout");
_Static_assert(sizeof(rs3_hit)==40, "ABI hit layout");
_Static_assert(sizeof(rs3_event)==608, "ABI event layout");
#define EVENT_CAPACITY 4096u
#define ACTOR_BYTES (P_NPC_HEALTH_MAX_OFFSET+4u)

struct rs3_snapshot { rs3_info info; rs3_player player;
    rs3_npc npcs[RS3_MAX_NPCS]; rs3_hit hits[RS3_MAX_HITS]; };
typedef struct npc_identity { uint64_t actor, instance; uint32_t uid, used;
    uint64_t valid_hits; rs3_hit previous[64]; } npc_identity;
typedef int (*read_callback)(void*, uint64_t, void*, size_t);
struct rs3_context {
    SRWLOCK control, latest_lock, events_lock;
    HANDLE process, worker, stop_event, timer;
    uint32_t requested_pid, pid, retry_ms, hz, attached, module_size;
    uint64_t module_base, creation_time, retry_after, sequence, event_sequence;
    uint64_t calls, bytes, next_instance, clock_changed, clock_context;
    uint32_t last_clock, previous_valid;
    int64_t previous_values[3]; uint64_t changed_at[3];
    char actual_sha256[72], error[192], retry_error[192]; uint32_t reason, retry_reason;
    read_callback read; void *read_user;
    rs3_snapshot *working, *latest;
    rs3_event *events;
    npc_identity *identities;
    uint16_t identity_index[65536];
    uint64_t *buckets; size_t bucket_capacity;
    uint64_t npc_context, npc_state, actors[RS3_MAX_NPCS];
};

static uint64_t monotonic_ns(void) {
    LARGE_INTEGER c,f; QueryPerformanceCounter(&c); QueryPerformanceFrequency(&f);
    return (uint64_t)(c.QuadPart/f.QuadPart)*1000000000ull+
           (uint64_t)(c.QuadPart%f.QuadPart)*1000000000ull/(uint64_t)f.QuadPart;
}
static uint64_t unix_ns(void) {
    FILETIME t; ULARGE_INTEGER x; GetSystemTimePreciseAsFileTime(&t);
    x.LowPart=t.dwLowDateTime; x.HighPart=t.dwHighDateTime;
    return (x.QuadPart-116444736000000000ull)*100ull;
}
static uint32_t u32(const void *p) { uint32_t v; memcpy(&v,p,4); return v; }
static int32_t i32(const void *p) { int32_t v; memcpy(&v,p,4); return v; }
static uint64_t u64(const void *p) { uint64_t v; memcpy(&v,p,8); return v; }
static int32_t tick_delta(uint32_t a,uint32_t b) { return (int32_t)(a-b); }
static int fail(rs3_context *c,uint32_t reason,const char *message) {
    c->reason=reason; snprintf(c->error,sizeof c->error,"%s",message); return 0;
}
static int pointer_valid(uint64_t p) { return p>=0x10000 && p<0x7fffffffffffULL; }
static int windows_read(void *user,uint64_t address,void *out,size_t size) {
    rs3_context *c=user; SIZE_T got=0;
    return ReadProcessMemory(c->process,(const void*)(uintptr_t)address,out,size,&got) && got==size;
}
static int rd(rs3_context *c,uint64_t a,void *out,size_t n) {
    c->calls++; c->bytes+=n;
    if (!pointer_valid(a) || !n || n>16*1024*1024 || n>0x7fffffffffffULL-a ||
        !c->read(c->read_user,a,out,n)) return fail(c,RS3_READ_FAILED,"Process-memory read failed");
    return 1;
}
static int ptr(rs3_context *c,uint64_t a,uint64_t *out) {
    if (!rd(c,a,out,8)) return 0;
    return !*out || pointer_valid(*out) || fail(c,RS3_READ_FAILED,"Invalid remote pointer");
}
static int signatures(rs3_context *c,const char *hash) {
    unsigned char b[64];
    if (strcmp(hash,P_SHA256)) return fail(c,RS3_UNSUPPORTED_CLIENT,"Unsupported executable SHA-256; offsets not used");
    for (size_t i=0;i<sizeof p_signatures/sizeof p_signatures[0];i++) {
        const profile_signature *s=p_signatures+i;
        if (!rd(c,c->module_base+s->rva,b,s->length)) return 0;
        if (memcmp(b,s->bytes,s->length)) return fail(c,RS3_UNSUPPORTED_CLIENT,"Loaded code does not match the pinned profile");
    }
    return 1;
}
static int hash_file(const wchar_t *path,char result[72]) {
    BCRYPT_ALG_HANDLE algorithm=NULL; BCRYPT_HASH_HANDLE hash=NULL;
    unsigned char digest[32],buffer[65536]; DWORD n=0; int ok=0;
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,0,NULL);
    if (file==INVALID_HANDLE_VALUE) return 0;
    if (BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,NULL,0)<0 ||
        BCryptCreateHash(algorithm,&hash,NULL,0,NULL,0,0)<0) goto done;
    for (;;) {
        if (!ReadFile(file,buffer,sizeof buffer,&n,NULL)) goto done;
        if (!n) break;
        if (BCryptHashData(hash,buffer,n,0)<0) goto done;
    }
    if (BCryptFinishHash(hash,digest,sizeof digest,0)<0) goto done;
    for (unsigned i=0;i<32;i++) snprintf(result+i*2,3,"%02x",digest[i]);
    ok=1;
done:
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm,0);
    CloseHandle(file); return ok;
}
static void reset_identities(rs3_context *c) {
    memset(c->identities,0,sizeof(npc_identity)*RS3_MAX_NPCS);
    memset(c->identity_index,0,sizeof c->identity_index); c->npc_context=c->npc_state=0;
}
static void detach(rs3_context *c) {
    if (c->process) CloseHandle(c->process);
    c->process=NULL; c->attached=0; c->previous_valid=0; c->clock_context=0;
    reset_identities(c);
}
static void schedule_retry(rs3_context *c,uint64_t now) {
    c->retry_reason=c->reason;
    snprintf(c->retry_error,sizeof c->retry_error,"%s",c->error);
    detach(c); c->retry_after=now+(uint64_t)c->retry_ms*1000000ull;
}
/* Fingerprint the file and loaded entrypoints before following any offsets. */
static int attach(rs3_context *c) {
    c->pid=0; c->creation_time=0; c->actual_sha256[0]=0;
    HANDLE list=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    PROCESSENTRY32W pe={0}; pe.dwSize=sizeof pe; uint32_t pid=0,count=0;
    if (list==INVALID_HANDLE_VALUE) return fail(c,RS3_READ_FAILED,"Process discovery failed");
    if (Process32FirstW(list,&pe)) do {
        if (!_wcsicmp(pe.szExeFile,L"rs2client.exe") && (!c->requested_pid || pe.th32ProcessID==c->requested_pid)) {
            pid=pe.th32ProcessID; count++;
        }
    } while (Process32NextW(list,&pe));
    CloseHandle(list);
    if (count!=1) { c->pid=0; return fail(c,count?RS3_MULTIPLE_CLIENTS:RS3_NO_CLIENT,
        count?"Multiple RS3 clients; select a PID":"No RS3 client"); }
    c->pid=pid;
    c->process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,pid);
    if (!c->process) return fail(c,RS3_READ_FAILED,"Cannot open RS3 with read-only access");
    list=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
    MODULEENTRY32W module={0}; module.dwSize=sizeof module;
    if (list==INVALID_HANDLE_VALUE) return fail(c,RS3_READ_FAILED,"Module discovery failed");
    int found=Module32FirstW(list,&module); CloseHandle(list);
    if (!found) return fail(c,RS3_READ_FAILED,"Main module unavailable");
    FILETIME born,ended,kernel,user; ULARGE_INTEGER time;
    if (!GetProcessTimes(c->process,&born,&ended,&kernel,&user)) return fail(c,RS3_READ_FAILED,"Process identity unavailable");
    time.LowPart=born.dwLowDateTime; time.HighPart=born.dwHighDateTime;
    c->creation_time=time.QuadPart; c->module_base=(uint64_t)(uintptr_t)module.modBaseAddr;
    c->module_size=module.modBaseSize;
    if (!hash_file(module.szExePath,c->actual_sha256)) return fail(c,RS3_READ_FAILED,"Cannot hash the executable");
    if (!signatures(c,c->actual_sha256)) return 0;
    c->attached=1; return 1;
}
static int capture(rs3_context *c,int client,uint64_t *out) {
    uint64_t node,visited[64]; unsigned n=0;
    uint32_t id=client?P_CLIENT_ID:P_PLAYER_ID;
    uint64_t handler=client?P_CLIENT_HANDLER_RVA:P_PLAYER_HANDLER_RVA;
    if (!ptr(c,c->module_base+P_REGISTRY_RVA+P_REGISTRY_ROOT_OFFSET,&node)) return 0;
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
static int variable(rs3_context *c,uint64_t table,uint64_t count,uint64_t sentinel,uint32_t id,int64_t *value,int *present) {
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
static int valid_utf8(const char *s,uint32_t n) {
    return !n || MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s,(int)n,NULL,0)>0;
}
static int npc_name(rs3_context *c,uint64_t actor,const unsigned char *b,rs3_npc *npc) {
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
static int decode_hit(const unsigned char *b,uint32_t tick,uint32_t slot,rs3_hit *h) {
    int32_t type=i32(b),amount=i32(b+4),duration=i32(b+20); uint32_t end=u32(b+8);
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
    unsigned char node[32],after[32],actor[ACTOR_BYTES]; uint64_t address,object,config,check_vtable,check_ring; uint16_t check_uid;
    rs3_npc npc={0}; npc.config_id=-1; npc.runtime_id=uid; npc.hit_offset=s->info.hit_count;
    if (!npc_node(c,table,count,sentinel,uid,&address,node)) return 0;
    object=u64(node+16);
    if (!rd(c,object,actor,sizeof actor)) return 0;
    if (u64(actor)!=c->module_base+P_NPC_ENTITY_VTABLE_RVA || (u32(actor+0x88)&65535)!=uid)
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
static void commit_npcs(rs3_context *c,rs3_snapshot *s,const uint32_t *ids,uint32_t count,uint64_t manager,uint64_t world_state) {
    unsigned char present[65536/8]={0};
    if (c->npc_context!=manager || c->npc_state!=world_state) {
        reset_identities(c); c->npc_context=manager; c->npc_state=world_state;
    }
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
static int npcs_once(rs3_context *c,rs3_snapshot *s,uint64_t state,uint32_t *ids,uint32_t *out_n,uint64_t *out_manager) {
    uint64_t manager,table,count,sentinel,after_manager; uint32_t n,after_n,after_ids[1024];
    unsigned char b[16],seen[65536/8]={0}; s->info.npc_count=s->info.hit_count=s->info.skipped_npcs=0;
    if (!ptr(c,state+P_NPC_MANAGER_OFFSET,&manager)) return 0;
    if (!manager) return fail(c,RS3_READ_FAILED,"NPC manager unavailable");
    if (!rd(c,manager,b,8)) return 0;
    if (u64(b)!=c->module_base+P_NPC_MANAGER_VTABLE_RVA) return fail(c,RS3_READ_FAILED,"NPC manager profile mismatch");
    if (!rd(c,manager+P_NPC_COUNT_OFFSET,&n,4) || n>1024) return fail(c,RS3_READ_FAILED,"Invalid NPC count");
    if (n && !rd(c,manager+P_NPC_INDICES_OFFSET,ids,n*4)) return 0;
    for (uint32_t i=0;i<n;i++) {
        if (ids[i]>65535 || (seen[ids[i]/8]&(1u<<(ids[i]%8)))) return fail(c,RS3_READ_FAILED,"Invalid NPC index list");
        seen[ids[i]/8]|=(unsigned char)(1u<<(ids[i]%8));
    }
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
static int decode(rs3_context *c,rs3_snapshot *s) {
    /* Stage fields first. Only the final context check permits event commits. */
    uint64_t player,client,state,vtable,manager,entries,after; rs3_player *p=&s->player;
    if (!capture(c,0,&player) || !capture(c,1,&client) || !ptr(c,client,&vtable)) return 0;
    if (vtable!=c->module_base+P_CLIENT_VTABLE_RVA) return fail(c,RS3_UNSUPPORTED_CLIENT,"Client context profile mismatch");
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
    if (!capture(c,0,&after) || after!=player || !capture(c,1,&after) || after!=client ||
        !ptr(c,client+P_STATE_POINTER_OFFSET,&after) || after!=state)
        return fail(c,RS3_READ_FAILED,"World context changed during read");
    if (npc_ok) commit_npcs(c,s,ids,npc_count,npc_manager,state);
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
static void copy_frame(rs3_snapshot *to,const rs3_snapshot *from) {
    to->info=from->info; to->player=from->player;
    memcpy(to->npcs,from->npcs,from->info.npc_count*sizeof(rs3_npc));
    memcpy(to->hits,from->hits,from->info.hit_count*sizeof(rs3_hit));
}
static void poll_and_publish(rs3_context *c,rs3_snapshot *s) {
    uint64_t begin=monotonic_ns(); DWORD exit_code=0;
    memset(&s->info,0,sizeof s->info); memset(&s->player,0,sizeof s->player);
    s->info.abi_version=RS3_ABI_VERSION; s->info.sequence=++c->sequence;
    s->info.timestamp_ns=unix_ns(); s->info.monotonic_ns=begin;
    c->calls=c->bytes=0; c->reason=RS3_REASON_NONE; c->error[0]=0;
    if (c->attached && (!GetExitCodeProcess(c->process,&exit_code) || exit_code!=STILL_ACTIVE)) { detach(c); c->retry_after=0; }
    int ready=c->attached;
    if (!ready && begin>=c->retry_after) {
        ready=attach(c);
        if (!ready) schedule_retry(c,begin);
    } else if (!ready && c->retry_reason) {
        fail(c,c->retry_reason,c->retry_error);
    }
    if (!ready || !decode(c,s)) {
        if (!c->reason) fail(c,RS3_NO_CLIENT,"Waiting to reconnect to RS3");
        s->info.status=RS3_UNAVAILABLE; s->info.reason=c->reason;
        s->info.npc_count=s->info.hit_count=s->info.loaded_npcs=s->info.skipped_npcs=0;
        memset(&s->player,0,sizeof s->player);
        c->previous_valid=0; c->clock_context=0;
        snprintf(s->info.message,sizeof s->info.message,"%s",c->error);
        if (c->reason==RS3_UNSUPPORTED_CLIENT && c->attached) schedule_retry(c,begin);
    }
    s->info.pid=c->pid; s->info.process_creation_time=c->creation_time;
    snprintf(s->info.executable_sha256,sizeof s->info.executable_sha256,"%s",c->actual_sha256);
    snprintf(s->info.client_version,sizeof s->info.client_version,"%s",P_VERSION);
    s->info.event_sequence=c->event_sequence; s->info.read_calls=c->calls; s->info.read_bytes=c->bytes;
    s->info.sample_duration_ns=monotonic_ns()-begin;
    AcquireSRWLockExclusive(&c->latest_lock); copy_frame(c->latest,s); ReleaseSRWLockExclusive(&c->latest_lock);
}
static DWORD WINAPI worker_main(void *argument) {
    rs3_context *c=argument; uint64_t period=1000000000ull/c->hz,deadline=monotonic_ns();
    HANDLE handles[2]={c->stop_event,c->timer};
    while (WaitForSingleObject(c->stop_event,0)!=WAIT_OBJECT_0) {
        poll_and_publish(c,c->working); deadline+=period;
        uint64_t now=monotonic_ns();
        if (now>=deadline) { deadline=now; continue; }
        LARGE_INTEGER due; due.QuadPart=-(LONGLONG)((deadline-now+99)/100);
        if (!SetWaitableTimer(c->timer,&due,0,NULL,NULL,FALSE)) break;
        if (WaitForMultipleObjects(2,handles,FALSE,INFINITE)!=WAIT_OBJECT_0+1) break;
    }
    return 0;
}

uint32_t rs3_abi_version(void) { return RS3_ABI_VERSION; }
uint64_t rs3_monotonic_ns(void) { return monotonic_ns(); }
uint32_t rs3_sizeof(uint32_t which) {
    switch (which) { case RS3_SIZE_OPTIONS:return sizeof(rs3_options); case RS3_SIZE_INFO:return sizeof(rs3_info);
    case RS3_SIZE_PLAYER:return sizeof(rs3_player); case RS3_SIZE_NPC:return sizeof(rs3_npc);
    case RS3_SIZE_HIT:return sizeof(rs3_hit); case RS3_SIZE_EVENT:return sizeof(rs3_event); default:return 0; }
}
const char *rs3_profile_sha256(void) { return P_PROFILE_SHA256; }
const char *rs3_result_string(int32_t r) {
    switch(r) { case RS3_OK:return "OK"; case RS3_E_ARGUMENT:return "Invalid argument";
    case RS3_E_MEMORY:return "Allocation failed"; case RS3_E_ABI:return "ABI layout mismatch";
    case RS3_E_BUFFER:return "Output buffer too small"; case RS3_E_STATE:return "Invalid lifecycle state";
    case RS3_E_OS:return "Windows operation failed"; default:return "Unknown result"; }
}
int32_t rs3_snapshot_create(rs3_snapshot **out) {
    if (!out) return RS3_E_ARGUMENT; *out=calloc(1,sizeof **out); return *out?RS3_OK:RS3_E_MEMORY;
}
void rs3_snapshot_destroy(rs3_snapshot *s) { free(s); }
int32_t rs3_create(const rs3_options *o,rs3_context **out) {
    if (!out) return RS3_E_ARGUMENT; *out=NULL;
    if (o && (o->struct_size!=sizeof *o || o->reserved)) return RS3_E_ARGUMENT;
    rs3_context *c=calloc(1,sizeof *c); if (!c) return RS3_E_MEMORY;
    InitializeSRWLock(&c->control); InitializeSRWLock(&c->latest_lock); InitializeSRWLock(&c->events_lock);
    c->requested_pid=o?o->pid:0; c->retry_ms=o && o->retry_ms?o->retry_ms:1000;
    c->read=windows_read; c->read_user=c;
    c->events=calloc(EVENT_CAPACITY,sizeof(rs3_event)); c->identities=calloc(RS3_MAX_NPCS,sizeof(npc_identity));
    c->working=calloc(1,sizeof(rs3_snapshot)); c->latest=calloc(1,sizeof(rs3_snapshot));
    if (!c->events || !c->identities || !c->working || !c->latest) { rs3_destroy(c); return RS3_E_MEMORY; }
    *out=c; return RS3_OK;
}
int32_t rs3_start(rs3_context *c,uint32_t hz) {
    if (!c || !hz || hz>1000) return RS3_E_ARGUMENT;
    AcquireSRWLockExclusive(&c->control);
    if (c->worker) { ReleaseSRWLockExclusive(&c->control); return RS3_E_STATE; }
    c->stop_event=CreateEventW(NULL,TRUE,FALSE,NULL);
    c->timer=CreateWaitableTimerExW(NULL,NULL,2,TIMER_ALL_ACCESS);
    if (!c->timer) c->timer=CreateWaitableTimerExW(NULL,NULL,0,TIMER_ALL_ACCESS);
    c->hz=hz;
    if (c->stop_event && c->timer) c->worker=CreateThread(NULL,0,worker_main,c,0,NULL);
    int32_t result=c->worker?RS3_OK:RS3_E_OS;
    if (!c->worker) { if(c->timer)CloseHandle(c->timer); if(c->stop_event)CloseHandle(c->stop_event); c->timer=c->stop_event=NULL; }
    ReleaseSRWLockExclusive(&c->control); return result;
}
int32_t rs3_stop(rs3_context *c) {
    if (!c) return RS3_E_ARGUMENT;
    AcquireSRWLockExclusive(&c->control);
    if (c->worker) {
        SetEvent(c->stop_event); WaitForSingleObject(c->worker,INFINITE);
        CloseHandle(c->worker); CloseHandle(c->stop_event); CloseHandle(c->timer);
        c->worker=c->stop_event=c->timer=NULL;
    }
    ReleaseSRWLockExclusive(&c->control); return RS3_OK;
}
void rs3_destroy(rs3_context *c) {
    if (!c) return;
    rs3_stop(c); if(c->process)CloseHandle(c->process);
    free(c->events); free(c->identities); free(c->working); free(c->latest); free(c->buckets); free(c);
}
int32_t rs3_sample(rs3_context *c,rs3_snapshot *s) {
    if (!c || !s) return RS3_E_ARGUMENT;
    AcquireSRWLockExclusive(&c->control);
    if (c->worker) { ReleaseSRWLockExclusive(&c->control); return RS3_E_STATE; }
    poll_and_publish(c,s); ReleaseSRWLockExclusive(&c->control); return RS3_OK;
}
int32_t rs3_copy_latest(rs3_context *c,rs3_snapshot *s) {
    if (!c || !s) return RS3_E_ARGUMENT;
    AcquireSRWLockShared(&c->latest_lock); copy_frame(s,c->latest); ReleaseSRWLockShared(&c->latest_lock);
    return RS3_OK;
}
int32_t rs3_snapshot_info(const rs3_snapshot *s,rs3_info *out,uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT; if(size<sizeof *out)return RS3_E_ABI; *out=s->info; return RS3_OK;
}
int32_t rs3_snapshot_player(const rs3_snapshot *s,rs3_player *out,uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT; if(size<sizeof *out)return RS3_E_ABI; *out=s->player; return RS3_OK;
}
int32_t rs3_snapshot_npcs(const rs3_snapshot *s,rs3_npc *out,uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT; *required=s->info.npc_count;
    if(capacity<*required)return RS3_E_BUFFER; if(*required && !out)return RS3_E_ARGUMENT;
    if(*required)memcpy(out,s->npcs,*required*sizeof *out); return RS3_OK;
}
int32_t rs3_snapshot_hits(const rs3_snapshot *s,rs3_hit *out,uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT; *required=s->info.hit_count;
    if(capacity<*required)return RS3_E_BUFFER; if(*required && !out)return RS3_E_ARGUMENT;
    if(*required)memcpy(out,s->hits,*required*sizeof *out); return RS3_OK;
}
int32_t rs3_events_since(rs3_context *c,uint64_t after,rs3_event *out,uint32_t capacity,uint32_t *written,uint64_t *next,uint64_t *lost) {
    if (!c || !written || !next || !lost || (capacity && !out)) return RS3_E_ARGUMENT;
    AcquireSRWLockShared(&c->events_lock);
    uint64_t latest=c->event_sequence,oldest=latest>=EVENT_CAPACITY?latest-EVENT_CAPACITY+1:1;
    *written=0; *lost=0; *next=after;
    if (after>latest) { ReleaseSRWLockShared(&c->events_lock); return RS3_E_ARGUMENT; }
    uint64_t first=after+1;
    if(first<oldest){*lost=oldest-first; first=oldest; *next=oldest-1;}
    while(first<=latest && *written<capacity) { out[(*written)++]=c->events[(first-1)%EVENT_CAPACITY]; *next=first++; }
    ReleaseSRWLockShared(&c->events_lock); return RS3_OK;
}
