/* Bounded observation queues with independent consumer cursors. */
#include "event_ring.h"

#include <string.h>

#include "rs3/rs3.h"

int32_t event_ring_copy(const void *ring, size_t record_size, uint32_t ring_capacity,
                        uint64_t latest_sequence, uint64_t after_sequence, void *output,
                        uint32_t output_capacity, uint32_t *written, uint64_t *next_sequence,
                        uint64_t *lost)
{
    *written = 0;
    *lost = 0;
    *next_sequence = after_sequence;
    if (after_sequence > latest_sequence)
    {
        return RS3_E_ARGUMENT;
    }

    uint64_t oldest_retained =
        latest_sequence >= ring_capacity ? latest_sequence - ring_capacity + 1 : 1;
    uint64_t sequence = after_sequence + 1;
    /* Report overwritten sequences before copying the retained records. */
    if (sequence < oldest_retained)
    {
        *lost = oldest_retained - sequence;
        sequence = oldest_retained;
        *next_sequence = oldest_retained - 1;
    }

    const unsigned char *records = ring;
    unsigned char *destination = output;
    while (sequence <= latest_sequence && *written < output_capacity)
    {
        const unsigned char *record =
            records + event_ring_index(sequence, ring_capacity) * record_size;
        memcpy(destination + (size_t)*written * record_size, record, record_size);
        (*written)++;
        *next_sequence = sequence;
        sequence++;
    }
    return RS3_OK;
}
