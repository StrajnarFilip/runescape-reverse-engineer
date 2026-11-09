/* Read-only acquisition engine.
 *
 * One sampler (synchronous or worker) owns process/decoder/identity state.
 * control serializes lifecycle calls, latest_lock protects published frames,
 * and events_lock protects an independent bounded observation queue. Consumers
 * never access working buffers. Game addresses are used only by rd(), which
 * bounds and accounts every read; no target code is executed.
 */
#include "internal.h"

#include <stdio.h>
#include <stdlib.h>

#include "profile_generated.h"

#if !defined(_M_X64) && !defined(__x86_64__)
#error Build for Windows x64
#endif

_Static_assert(sizeof(void*)==8, "Build for Windows x64");
_Static_assert(sizeof(rs3_info)==384, "ABI info layout");
_Static_assert(sizeof(rs3_player)==136, "ABI player layout");
_Static_assert(sizeof(rs3_position)==16, "ABI position layout");
_Static_assert(sizeof(rs3_minimap)==64, "ABI minimap layout");
_Static_assert(sizeof(rs3_npc)==568, "ABI NPC layout");
_Static_assert(sizeof(rs3_hit)==40, "ABI hit layout");
_Static_assert(sizeof(rs3_event)==608, "ABI event layout");
_Static_assert(sizeof(rs3_inventory_info)==168, "ABI inventory info layout");
_Static_assert(sizeof(rs3_item)==192, "ABI item layout");
_Static_assert(sizeof(rs3_ability)==192, "ABI ability layout");
_Static_assert(sizeof(rs3_item_event)==424, "ABI item event layout");

/* Copy only populated array prefixes. The frame counts define which entries are valid. */
static void copy_frame(rs3_snapshot *to,const rs3_snapshot *from) {
    to->info=from->info; to->player=from->player;
    to->profile = from->profile;
    to->position = from->position;
    to->combat = from->combat;
    memcpy(to->animations,from->animations,from->info.npc_count*sizeof(rs3_npc_animation));
    memcpy(to->npcs,from->npcs,from->info.npc_count*sizeof(rs3_npc));
    memcpy(to->hits,from->hits,from->info.hit_count*sizeof(rs3_hit));
    to->inventory_info=from->inventory_info;
    memcpy(to->items,from->items,(from->inventory_info.backpack_count+from->inventory_info.equipment_count)*sizeof(rs3_item));
    memcpy(to->abilities,from->abilities,from->inventory_info.ability_count*sizeof(rs3_ability));
}
static void poll_and_publish(rs3_context *c,rs3_snapshot *s) {
    uint64_t begin=monotonic_ns(); DWORD exit_code=0;
    memset(&s->info,0,sizeof s->info); memset(&s->player,0,sizeof s->player);
    s->position = (rs3_position){0};
    memset(&s->combat,0,sizeof s->combat);
    memset(&s->inventory_info,0,sizeof s->inventory_info);
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
    int decoded = ready && decode(c, s);
    // Discard staged combat fields when the decode failed and clear their freshness baseline.
    if (!decoded) {
        if (!c->reason) fail(c,RS3_NO_CLIENT,"Waiting to reconnect to RS3");
        s->info.status=RS3_UNAVAILABLE; s->info.reason=c->reason;
        s->info.npc_count=s->info.hit_count=s->info.loaded_npcs=s->info.skipped_npcs=0;
        memset(&s->player,0,sizeof s->player);
        s->player.gcd_reason = RS3_NOT_SET;
        memset(&s->inventory_info,0,sizeof s->inventory_info);
        s->inventory_info.status=RS3_UNAVAILABLE; s->inventory_info.reason=c->reason;
        c->previous_valid=0; c->clock_context=0;
        memset(&s->combat,0,sizeof s->combat);
        s->combat.status=RS3_UNAVAILABLE; s->combat.reason=c->reason; c->combat_world=0;
        snprintf(s->info.message,sizeof s->info.message,"%s",c->error);
        if (c->reason==RS3_UNSUPPORTED_CLIENT && c->attached) schedule_retry(c,begin);
    }
    // Position can still provide a partial observation when combat decoding fails.
    if (c->profile && c->profile->has_position && decode_position(c, &s->position) && !decoded)
        s->info.status = RS3_PARTIAL;
    s->info.pid=c->pid; s->info.process_creation_time=c->creation_time;
    snprintf(s->info.executable_sha256,sizeof s->info.executable_sha256,"%s",c->actual_sha256);
    s->profile = c->profile;
    const char *version = c->profile ? c->profile->short_version : "";
    snprintf(s->info.client_version, sizeof s->info.client_version, "%s", version);
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
        // Drop missed deadlines to avoid a burst of catch-up samples.
        // A negative due time is relative and uses 100 ns units.
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
    case RS3_SIZE_HIT:return sizeof(rs3_hit); case RS3_SIZE_EVENT:return sizeof(rs3_event);
    case RS3_SIZE_INVENTORY_INFO:return sizeof(rs3_inventory_info); case RS3_SIZE_ITEM:return sizeof(rs3_item);
    case RS3_SIZE_ABILITY:return sizeof(rs3_ability); case RS3_SIZE_ITEM_EVENT:return sizeof(rs3_item_event);
    case RS3_SIZE_POSITION:return sizeof(rs3_position); case RS3_SIZE_MINIMAP:return sizeof(rs3_minimap);
    default:return 0; }
}
const char *rs3_profile_sha256(void) { return P_PROFILE_SHA256; }
const char *rs3_result_string(int32_t r) {
    switch(r) { case RS3_OK:return "OK"; case RS3_E_ARGUMENT:return "Invalid argument";
    case RS3_E_MEMORY:return "Allocation failed"; case RS3_E_ABI:return "ABI layout mismatch";
    case RS3_E_BUFFER:return "Output buffer too small"; case RS3_E_STATE:return "Invalid lifecycle state";
    case RS3_E_OS:return "Windows operation failed"; case RS3_E_TARGET:return "Input target unavailable";
    default:return "Unknown result"; }
}
int32_t rs3_snapshot_create(rs3_snapshot **out) {
    if (!out) return RS3_E_ARGUMENT;
    *out = calloc(1, sizeof **out);
    return *out ? RS3_OK : RS3_E_MEMORY;
}
void rs3_snapshot_destroy(rs3_snapshot *s) { free(s); }
int32_t rs3_create(const rs3_options *o,rs3_context **out) {
    if (!out) return RS3_E_ARGUMENT;
    *out = NULL;
    if (o && (o->struct_size!=sizeof *o || o->reserved)) return RS3_E_ARGUMENT;
    rs3_context *c=calloc(1,sizeof *c); if (!c) return RS3_E_MEMORY;
    InitializeSRWLock(&c->control); InitializeSRWLock(&c->latest_lock); InitializeSRWLock(&c->events_lock);
    c->requested_pid=o?o->pid:0; c->retry_ms=o && o->retry_ms?o->retry_ms:1000;
    c->read=windows_read; c->read_user=c;
    c->events=calloc(EVENT_CAPACITY,sizeof(rs3_event)); c->identities=calloc(RS3_MAX_NPCS,sizeof(npc_identity));
    c->working=calloc(1,sizeof(rs3_snapshot)); c->latest=calloc(1,sizeof(rs3_snapshot));
    c->inventory=inventory_create();
    if (!c->events || !c->identities || !c->working || !c->latest || !c->inventory) { rs3_destroy(c); return RS3_E_MEMORY; }
    inventory_default_catalog(c);
    *out=c; return RS3_OK;
}
int32_t rs3_start(rs3_context *c,uint32_t hz) {
    if (!c || !hz || hz>1000) return RS3_E_ARGUMENT;
    AcquireSRWLockExclusive(&c->control);
    if (c->worker) { ReleaseSRWLockExclusive(&c->control); return RS3_E_STATE; }
    c->stop_event=CreateEventW(NULL,TRUE,FALSE,NULL);
    // Try a high-resolution timer first, then fall back to a regular waitable timer.
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
    free(c->events); free(c->identities); free(c->working); free(c->latest); free(c->buckets);
    inventory_free(c->inventory); free(c);
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
    if (!s || !out) return RS3_E_ARGUMENT;
    if (size < sizeof *out) return RS3_E_ABI;
    *out = s->info;
    return RS3_OK;
}
const char *rs3_snapshot_client_version(const rs3_snapshot *s) {
    return s && s->profile ? s->profile->version : "";
}
int32_t rs3_snapshot_player(const rs3_snapshot *s,rs3_player *out,uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT;
    if (size < sizeof *out) return RS3_E_ABI;
    *out = s->player;
    return RS3_OK;
}
int32_t rs3_snapshot_position(const rs3_snapshot *s, rs3_position *out, uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT;
    if (size < sizeof *out) return RS3_E_ABI;
    *out = s->position;
    return RS3_OK;
}
int32_t rs3_snapshot_npcs(const rs3_snapshot *s,rs3_npc *out,uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT;
    *required = s->info.npc_count;
    if (capacity < *required) return RS3_E_BUFFER;
    if (*required && !out) return RS3_E_ARGUMENT;
    if (*required) memcpy(out, s->npcs, *required * sizeof *out);
    return RS3_OK;
}
int32_t rs3_snapshot_hits(const rs3_snapshot *s,rs3_hit *out,uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT;
    *required = s->info.hit_count;
    if (capacity < *required) return RS3_E_BUFFER;
    if (*required && !out) return RS3_E_ARGUMENT;
    if (*required) memcpy(out, s->hits, *required * sizeof *out);
    return RS3_OK;
}
int32_t rs3_events_since(rs3_context *c,uint64_t after,rs3_event *out,uint32_t capacity,uint32_t *written,uint64_t *next,uint64_t *lost) {
    if (!c || !written || !next || !lost || (capacity && !out)) return RS3_E_ARGUMENT;
    AcquireSRWLockShared(&c->events_lock);
    uint64_t latest=c->event_sequence,oldest=latest>=EVENT_CAPACITY?latest-EVENT_CAPACITY+1:1;
    *written=0; *lost=0; *next=after;
    if (after>latest) { ReleaseSRWLockShared(&c->events_lock); return RS3_E_ARGUMENT; }
    uint64_t first=after+1;
    // Report overwritten sequences before copying retained events. A zero-capacity
    // call can still advance the cursor past the gap.
    if(first<oldest){*lost=oldest-first; first=oldest; *next=oldest-1;}
    while(first<=latest && *written<capacity) { out[(*written)++]=c->events[(first-1)%EVENT_CAPACITY]; *next=first++; }
    ReleaseSRWLockShared(&c->events_lock); return RS3_OK;
}
