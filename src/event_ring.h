/*
 * Bounded observation queues with independent consumer cursors.
 *
 * Each queue is a ring of fixed-size records numbered by a one-based sequence.
 * Sequence s is stored at index (s - 1) % capacity, so the newest capacity
 * records are retained and older ones are reported to consumers as lost.
 */
#ifndef RS3_EVENT_RING_H
#define RS3_EVENT_RING_H

#include <stddef.h>
#include <stdint.h>

/** Returns the storage index of a one-based sequence number. */
static inline size_t event_ring_index(uint64_t sequence, uint32_t capacity)
{
    return (size_t)((sequence - 1) % capacity);
}

/**
 * Copies records after after_sequence into output.
 *
 * ring holds ring_capacity records of record_size bytes; latest_sequence is
 * the newest stored sequence. *lost receives the number of overwritten
 * sequences skipped before the oldest retained record; *next_sequence receives
 * the cursor for the following call. A zero output_capacity still advances the
 * cursor past a gap. Returns RS3_OK, or RS3_E_ARGUMENT when after_sequence is
 * newer than latest_sequence. The caller holds the queue lock.
 */
int32_t event_ring_copy(const void *ring, size_t record_size, uint32_t ring_capacity,
                        uint64_t latest_sequence, uint64_t after_sequence, void *output,
                        uint32_t output_capacity, uint32_t *written, uint64_t *next_sequence,
                        uint64_t *lost);

#endif /* RS3_EVENT_RING_H */
