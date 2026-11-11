/*
 * Context lifecycle, the native worker and frame publication.
 *
 * A sample attaches to the client when needed, decodes one frame into a
 * private buffer and publishes a copy under latest_frame_lock. Remote memory is
 * only ever read; no target code is executed.
 */
#include "context.h"

#include <stdio.h>
#include <stdlib.h>

#include "clock.h"
#include "frame.h"
#include "inventory.h"
#include "memory.h"
#include "position.h"
#include "process.h"

#if !defined(_M_X64) && !defined(__x86_64__)
#error Build for Windows x64
#endif

_Static_assert(sizeof(void *) == 8, "Build for Windows x64");
_Static_assert(sizeof(rs3_info) == 384, "ABI info layout");
_Static_assert(sizeof(rs3_player) == 136, "ABI player layout");
_Static_assert(sizeof(rs3_position) == 16, "ABI position layout");
_Static_assert(sizeof(rs3_minimap) == 64, "ABI minimap layout");
_Static_assert(sizeof(rs3_npc) == 568, "ABI NPC layout");
_Static_assert(sizeof(rs3_hit) == 40, "ABI hit layout");
_Static_assert(sizeof(rs3_event) == 608, "ABI event layout");
_Static_assert(sizeof(rs3_inventory_info) == 168, "ABI inventory info layout");
_Static_assert(sizeof(rs3_item) == 192, "ABI item layout");
_Static_assert(sizeof(rs3_ability) == 192, "ABI ability layout");
_Static_assert(sizeof(rs3_item_event) == 424, "ABI item event layout");

#define DEFAULT_RETRY_INTERVAL_MS  1000u
#define MAX_WORKER_HZ              1000u
#define NANOSECONDS_PER_SECOND     1000000000ull
#define NANOSECONDS_PER_TIMER_UNIT 100ull
/* CREATE_WAITABLE_TIMER_HIGH_RESOLUTION; older SDKs may not define it. */
#define HIGH_RESOLUTION_TIMER_FLAG 0x00000002u

int context_fail(rs3_context *context, uint32_t reason, const char *message)
{
    context->failure_reason = reason;
    snprintf(context->failure_message, sizeof context->failure_message, "%s", message);
    return 0;
}

int context_reserve_buckets(rs3_context *context, uint64_t bucket_count)
{
    if (context->bucket_buffer_capacity >= bucket_count)
    {
        return 1;
    }
    uint64_t *grown = realloc(context->bucket_buffer, (size_t)bucket_count * sizeof *grown);
    if (grown == NULL)
    {
        return context_fail(context, RS3_READ_FAILED, "Bucket allocation failed");
    }
    context->bucket_buffer = grown;
    context->bucket_buffer_capacity = (size_t)bucket_count;
    return 1;
}

/** Copies a frame. Only the populated array prefixes are copied. */
static void copy_frame(rs3_snapshot *destination, const rs3_snapshot *source)
{
    uint32_t item_count =
        source->inventory_info.backpack_count + source->inventory_info.equipment_count;
    destination->info = source->info;
    destination->player = source->player;
    destination->profile = source->profile;
    destination->position = source->position;
    destination->combat = source->combat;
    destination->encounter = source->encounter;
    destination->player_hit_info = source->player_hit_info;
    destination->player_actor_address = source->player_actor_address;
    memcpy(destination->player_hits, source->player_hits,
           source->player_hit_info.hit_count * sizeof(rs3_hit));
    memcpy(destination->npc_animations, source->npc_animations,
           source->info.npc_count * sizeof(rs3_npc_animation));
    memcpy(destination->npcs, source->npcs, source->info.npc_count * sizeof(rs3_npc));
    memcpy(destination->hits, source->hits, source->info.hit_count * sizeof(rs3_hit));
    destination->inventory_info = source->inventory_info;
    memcpy(destination->items, source->items, item_count * sizeof(rs3_item));
    memcpy(destination->abilities, source->abilities,
           source->inventory_info.ability_count * sizeof(rs3_ability));
}

/** Clears every field that a failed decode may have staged. */
static void clear_unavailable_frame(rs3_context *context, rs3_snapshot *snapshot)
{
    snapshot->info.status = RS3_UNAVAILABLE;
    snapshot->info.reason = context->failure_reason;
    snapshot->info.npc_count = 0;
    snapshot->info.hit_count = 0;
    snapshot->info.loaded_npcs = 0;
    snapshot->info.skipped_npcs = 0;
    snprintf(snapshot->info.message, sizeof snapshot->info.message, "%s", context->failure_message);

    memset(&snapshot->player, 0, sizeof snapshot->player);
    snapshot->player.gcd_reason = RS3_NOT_SET;

    memset(&snapshot->inventory_info, 0, sizeof snapshot->inventory_info);
    snapshot->inventory_info.status = RS3_UNAVAILABLE;
    snapshot->inventory_info.reason = context->failure_reason;

    memset(&snapshot->combat, 0, sizeof snapshot->combat);
    snapshot->combat.status = RS3_UNAVAILABLE;
    snapshot->combat.reason = context->failure_reason;
    memset(&snapshot->encounter, 0, sizeof snapshot->encounter);
    snapshot->encounter.status = RS3_UNAVAILABLE;
    snapshot->encounter.reason = context->failure_reason;

    memset(&snapshot->player_hit_info, 0, sizeof snapshot->player_hit_info);
    snapshot->player_hit_info.status = RS3_UNAVAILABLE;
    snapshot->player_hit_info.reason = context->failure_reason;

    /* Without a decoded frame there is no freshness or adjacency baseline either. */
    context->player_changes.valid_mask = 0;
    context->player_changes.clock_world_state = 0;
    context->combat.world_state = 0;
    context->player_hits.sample_sequence = 0;
}

/** Ensures the context is attached, honouring the retry back-off. */
static int ensure_attached(rs3_context *context, uint64_t now_ns)
{
    DWORD exit_code = 0;
    if (context->target.attached &&
        (!GetExitCodeProcess(context->target.process_handle, &exit_code) ||
         exit_code != STILL_ACTIVE))
    {
        process_detach(context);
        context->retry.retry_after_ns = 0;
    }
    if (context->target.attached)
    {
        return 1;
    }
    if (now_ns >= context->retry.retry_after_ns)
    {
        if (process_attach(context))
        {
            return 1;
        }
        process_schedule_retry(context, now_ns);
        return 0;
    }
    if (context->retry.reason != RS3_REASON_NONE)
    {
        context_fail(context, context->retry.reason, context->retry.message);
    }
    return 0;
}

/** Samples one frame into snapshot and publishes a copy as the latest frame. */
static void sample_and_publish(rs3_context *context, rs3_snapshot *snapshot)
{
    uint64_t started_ns = clock_monotonic_ns();
    memset(&snapshot->info, 0, sizeof snapshot->info);
    memset(&snapshot->player, 0, sizeof snapshot->player);
    snapshot->position = (rs3_position){0};
    memset(&snapshot->combat, 0, sizeof snapshot->combat);
    memset(&snapshot->encounter, 0, sizeof snapshot->encounter);
    memset(&snapshot->player_hit_info, 0, sizeof snapshot->player_hit_info);
    snapshot->player_actor_address = 0;
    memset(&snapshot->inventory_info, 0, sizeof snapshot->inventory_info);
    snapshot->info.abi_version = RS3_ABI_VERSION;
    snapshot->info.sequence = ++context->sample_sequence;
    snapshot->info.timestamp_ns = clock_unix_ns();
    snapshot->info.monotonic_ns = started_ns;
    context->read_call_count = 0;
    context->read_byte_count = 0;
    context->failure_reason = RS3_REASON_NONE;
    context->failure_message[0] = '\0';

    int attached = ensure_attached(context, started_ns);
    int decoded = attached && frame_decode(context, snapshot);
    if (!decoded)
    {
        if (context->failure_reason == RS3_REASON_NONE)
        {
            context_fail(context, RS3_NO_CLIENT, "Waiting to reconnect to RS3");
        }
        clear_unavailable_frame(context, snapshot);
        if (context->failure_reason == RS3_UNSUPPORTED_CLIENT && context->target.attached)
        {
            process_schedule_retry(context, started_ns);
        }
    }

    /* The player tile can still make a partial observation when the combat decode fails. */
    if (context->target.profile != NULL && context->target.profile->has_position &&
        position_decode(context, &snapshot->position) && !decoded)
    {
        snapshot->info.status = RS3_PARTIAL;
    }

    const char *short_version =
        context->target.profile != NULL ? context->target.profile->short_version : "";
    snapshot->profile = context->target.profile;
    snapshot->info.pid = context->target.pid;
    snapshot->info.process_creation_time = context->target.process_creation_time;
    snprintf(snapshot->info.executable_sha256, sizeof snapshot->info.executable_sha256, "%s",
             context->target.executable_sha256);
    snprintf(snapshot->info.client_version, sizeof snapshot->info.client_version, "%s",
             short_version);
    snapshot->info.event_sequence = context->hit_event_sequence;
    snapshot->info.read_calls = context->read_call_count;
    snapshot->info.read_bytes = context->read_byte_count;
    snapshot->info.sample_duration_ns = clock_monotonic_ns() - started_ns;

    AcquireSRWLockExclusive(&context->latest_frame_lock);
    copy_frame(context->latest_frame, snapshot);
    ReleaseSRWLockExclusive(&context->latest_frame_lock);
}

static DWORD WINAPI worker_main(void *argument)
{
    rs3_context *context = argument;
    uint64_t period_ns = NANOSECONDS_PER_SECOND / context->worker.hz;
    uint64_t deadline_ns = clock_monotonic_ns();
    HANDLE wait_handles[2] = {context->worker.stop_event, context->worker.timer};

    while (WaitForSingleObject(context->worker.stop_event, 0) != WAIT_OBJECT_0)
    {
        sample_and_publish(context, context->working_frame);
        deadline_ns += period_ns;
        uint64_t now_ns = clock_monotonic_ns();
        /* Drop missed deadlines instead of bursting catch-up samples. */
        if (now_ns >= deadline_ns)
        {
            deadline_ns = now_ns;
            continue;
        }
        /* A negative due time is relative and uses 100 ns units. */
        LARGE_INTEGER due_time;
        due_time.QuadPart = -(LONGLONG)((deadline_ns - now_ns + NANOSECONDS_PER_TIMER_UNIT - 1) /
                                        NANOSECONDS_PER_TIMER_UNIT);
        if (!SetWaitableTimer(context->worker.timer, &due_time, 0, NULL, NULL, FALSE))
        {
            break;
        }
        if (WaitForMultipleObjects(2, wait_handles, FALSE, INFINITE) != WAIT_OBJECT_0 + 1)
        {
            break;
        }
    }
    return 0;
}

uint32_t rs3_abi_version(void)
{
    return RS3_ABI_VERSION;
}

uint64_t rs3_monotonic_ns(void)
{
    return clock_monotonic_ns();
}

uint32_t rs3_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_SIZE_OPTIONS:
            return sizeof(rs3_options);
        case RS3_SIZE_INFO:
            return sizeof(rs3_info);
        case RS3_SIZE_PLAYER:
            return sizeof(rs3_player);
        case RS3_SIZE_NPC:
            return sizeof(rs3_npc);
        case RS3_SIZE_HIT:
            return sizeof(rs3_hit);
        case RS3_SIZE_EVENT:
            return sizeof(rs3_event);
        case RS3_SIZE_INVENTORY_INFO:
            return sizeof(rs3_inventory_info);
        case RS3_SIZE_ITEM:
            return sizeof(rs3_item);
        case RS3_SIZE_ABILITY:
            return sizeof(rs3_ability);
        case RS3_SIZE_ITEM_EVENT:
            return sizeof(rs3_item_event);
        case RS3_SIZE_POSITION:
            return sizeof(rs3_position);
        case RS3_SIZE_MINIMAP:
            return sizeof(rs3_minimap);
        default:
            return 0;
    }
}

const char *rs3_profile_sha256(void)
{
    return PROFILE_PRIMARY_SOURCE_SHA256;
}

const char *rs3_result_string(int32_t result)
{
    switch (result)
    {
        case RS3_OK:
            return "OK";
        case RS3_E_ARGUMENT:
            return "Invalid argument";
        case RS3_E_MEMORY:
            return "Allocation failed";
        case RS3_E_ABI:
            return "ABI layout mismatch";
        case RS3_E_BUFFER:
            return "Output buffer too small";
        case RS3_E_STATE:
            return "Invalid lifecycle state";
        case RS3_E_OS:
            return "Windows operation failed";
        case RS3_E_TARGET:
            return "Input target unavailable";
        default:
            return "Unknown result";
    }
}

int32_t rs3_create(const rs3_options *options, rs3_context **out)
{
    if (out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    *out = NULL;
    if (options != NULL && (options->struct_size != sizeof *options || options->reserved != 0))
    {
        return RS3_E_ARGUMENT;
    }
    rs3_context *context = calloc(1, sizeof *context);
    if (context == NULL)
    {
        return RS3_E_MEMORY;
    }
    InitializeSRWLock(&context->lifecycle_lock);
    InitializeSRWLock(&context->latest_frame_lock);
    InitializeSRWLock(&context->event_queue_lock);
    context->target.requested_pid = options != NULL ? options->pid : 0;
    context->retry.interval_ms =
        options != NULL && options->retry_ms != 0 ? options->retry_ms : DEFAULT_RETRY_INTERVAL_MS;
    context->read_memory = windows_process_read;
    context->read_memory_user_data = context;
    context->hit_events = calloc(HIT_EVENT_CAPACITY, sizeof(rs3_event));
    context->npc_action_events = calloc(NPC_ACTION_EVENT_CAPACITY, sizeof(rs3_npc_action_event));
    context->player_hit_events = calloc(PLAYER_HIT_EVENT_CAPACITY, sizeof(rs3_player_hit_event));
    context->npcs.identities = calloc(RS3_MAX_NPCS, sizeof(npc_identity));
    context->working_frame = calloc(1, sizeof(rs3_snapshot));
    context->latest_frame = calloc(1, sizeof(rs3_snapshot));
    context->inventory = inventory_create();
    if (context->hit_events == NULL || context->npc_action_events == NULL ||
        context->player_hit_events == NULL || context->npcs.identities == NULL ||
        context->working_frame == NULL || context->latest_frame == NULL ||
        context->inventory == NULL)
    {
        rs3_destroy(context);
        return RS3_E_MEMORY;
    }
    catalog_load_default(&context->inventory->catalog);
    *out = context;
    return RS3_OK;
}

int32_t rs3_start(rs3_context *context, uint32_t hz)
{
    if (context == NULL || hz == 0 || hz > MAX_WORKER_HZ)
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockExclusive(&context->lifecycle_lock);
    worker_state *worker = &context->worker;
    if (worker->thread != NULL)
    {
        ReleaseSRWLockExclusive(&context->lifecycle_lock);
        return RS3_E_STATE;
    }
    worker->stop_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    /* Prefer a high-resolution timer, then fall back to a regular waitable timer. */
    worker->timer =
        CreateWaitableTimerExW(NULL, NULL, HIGH_RESOLUTION_TIMER_FLAG, TIMER_ALL_ACCESS);
    if (worker->timer == NULL)
    {
        worker->timer = CreateWaitableTimerExW(NULL, NULL, 0, TIMER_ALL_ACCESS);
    }
    worker->hz = hz;
    if (worker->stop_event != NULL && worker->timer != NULL)
    {
        worker->thread = CreateThread(NULL, 0, worker_main, context, 0, NULL);
    }
    int32_t result = worker->thread != NULL ? RS3_OK : RS3_E_OS;
    if (worker->thread == NULL)
    {
        if (worker->timer != NULL)
        {
            CloseHandle(worker->timer);
        }
        if (worker->stop_event != NULL)
        {
            CloseHandle(worker->stop_event);
        }
        worker->timer = NULL;
        worker->stop_event = NULL;
    }
    ReleaseSRWLockExclusive(&context->lifecycle_lock);
    return result;
}

int32_t rs3_stop(rs3_context *context)
{
    if (context == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockExclusive(&context->lifecycle_lock);
    worker_state *worker = &context->worker;
    if (worker->thread != NULL)
    {
        SetEvent(worker->stop_event);
        WaitForSingleObject(worker->thread, INFINITE);
        CloseHandle(worker->thread);
        CloseHandle(worker->stop_event);
        CloseHandle(worker->timer);
        worker->thread = NULL;
        worker->stop_event = NULL;
        worker->timer = NULL;
    }
    ReleaseSRWLockExclusive(&context->lifecycle_lock);
    return RS3_OK;
}

void rs3_destroy(rs3_context *context)
{
    if (context == NULL)
    {
        return;
    }
    rs3_stop(context);
    if (context->target.process_handle != NULL)
    {
        CloseHandle(context->target.process_handle);
    }
    free(context->hit_events);
    free(context->npc_action_events);
    free(context->player_hit_events);
    free(context->npcs.identities);
    free(context->working_frame);
    free(context->latest_frame);
    free(context->bucket_buffer);
    inventory_destroy(context->inventory);
    free(context);
}

int32_t rs3_sample(rs3_context *context, rs3_snapshot *out)
{
    if (context == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockExclusive(&context->lifecycle_lock);
    if (context->worker.thread != NULL)
    {
        ReleaseSRWLockExclusive(&context->lifecycle_lock);
        return RS3_E_STATE;
    }
    sample_and_publish(context, out);
    ReleaseSRWLockExclusive(&context->lifecycle_lock);
    return RS3_OK;
}

int32_t rs3_copy_latest(rs3_context *context, rs3_snapshot *out)
{
    if (context == NULL || out == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockShared(&context->latest_frame_lock);
    copy_frame(out, context->latest_frame);
    ReleaseSRWLockShared(&context->latest_frame_lock);
    return RS3_OK;
}

int32_t rs3_load_catalog(rs3_context *context, const char *path)
{
    if (context == NULL || path == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    int wide_length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (wide_length < 1 || wide_length > 32768)
    {
        return RS3_E_ARGUMENT;
    }
    wchar_t *wide_path = malloc((size_t)wide_length * sizeof *wide_path);
    if (wide_path == NULL)
    {
        return RS3_E_MEMORY;
    }
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide_path, wide_length);

    AcquireSRWLockExclusive(&context->lifecycle_lock);
    int32_t result = context->worker.thread != NULL
                         ? RS3_E_STATE
                         : catalog_load_file(&context->inventory->catalog, wide_path);
    ReleaseSRWLockExclusive(&context->lifecycle_lock);
    free(wide_path);
    return result;
}
