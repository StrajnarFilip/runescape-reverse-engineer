/* Public readers of the hit, item, NPC action and player hit observation queues. */
#include "context.h"
#include "event_ring.h"
#include "inventory.h"

static int cursor_arguments_are_valid(const rs3_context *context, const void *output,
                                      uint32_t capacity, const uint32_t *written,
                                      const uint64_t *next, const uint64_t *lost)
{
    return context != NULL && written != NULL && next != NULL && lost != NULL &&
           (capacity == 0 || output != NULL);
}

int32_t rs3_events_since(rs3_context *context, uint64_t after, rs3_event *out, uint32_t capacity,
                         uint32_t *written, uint64_t *next, uint64_t *lost)
{
    if (!cursor_arguments_are_valid(context, out, capacity, written, next, lost))
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockShared(&context->event_queue_lock);
    int32_t result =
        event_ring_copy(context->hit_events, sizeof *out, HIT_EVENT_CAPACITY,
                        context->hit_event_sequence, after, out, capacity, written, next, lost);
    ReleaseSRWLockShared(&context->event_queue_lock);
    return result;
}

int32_t rs3_item_events_since(rs3_context *context, uint64_t after, rs3_item_event *out,
                              uint32_t capacity, uint32_t *written, uint64_t *next, uint64_t *lost)
{
    if (!cursor_arguments_are_valid(context, out, capacity, written, next, lost))
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockShared(&context->event_queue_lock);
    const item_tracker *tracker = &context->inventory->items;
    int32_t result =
        event_ring_copy(tracker->events, sizeof *out, ITEM_EVENT_CAPACITY, tracker->event_sequence,
                        after, out, capacity, written, next, lost);
    ReleaseSRWLockShared(&context->event_queue_lock);
    return result;
}

int32_t rs3_npc_action_events_since(rs3_context *context, uint64_t after, rs3_npc_action_event *out,
                                    uint32_t capacity, uint32_t *written, uint64_t *next,
                                    uint64_t *lost)
{
    if (!cursor_arguments_are_valid(context, out, capacity, written, next, lost))
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockShared(&context->event_queue_lock);
    int32_t result = event_ring_copy(context->npc_action_events, sizeof *out,
                                     NPC_ACTION_EVENT_CAPACITY, context->npc_action_event_sequence,
                                     after, out, capacity, written, next, lost);
    ReleaseSRWLockShared(&context->event_queue_lock);
    return result;
}

int32_t rs3_player_hit_events_since(rs3_context *context, uint64_t after, rs3_player_hit_event *out,
                                    uint32_t capacity, uint32_t *written, uint64_t *next,
                                    uint64_t *lost)
{
    if (!cursor_arguments_are_valid(context, out, capacity, written, next, lost))
    {
        return RS3_E_ARGUMENT;
    }
    AcquireSRWLockShared(&context->event_queue_lock);
    int32_t result = event_ring_copy(context->player_hit_events, sizeof *out,
                                     PLAYER_HIT_EVENT_CAPACITY, context->player_hit_event_sequence,
                                     after, out, capacity, written, next, lost);
    ReleaseSRWLockShared(&context->event_queue_lock);
    return result;
}
