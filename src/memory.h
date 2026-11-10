/*
 * Bounded, accounted remote-memory reads.
 *
 * Every remote dereference goes through memory_read(), which rejects invalid
 * ranges, counts the attempt for the per-sample diagnostics and records a
 * failure reason on the context. No target code is ever executed.
 */
#ifndef RS3_MEMORY_H
#define RS3_MEMORY_H

#include <stddef.h>
#include <stdint.h>

#include "context.h"

/** Largest single remote read accepted by memory_read(). */
#define MAX_REMOTE_READ_BYTES (16u * 1024u * 1024u)

/** memory_reader implementation that uses ReadProcessMemory on the attached process. */
int windows_process_read(void *user_data, uint64_t address, void *output, size_t size);

/** Reads size bytes at address. Returns 0 and records RS3_READ_FAILED on failure. */
int memory_read(rs3_context *context, uint64_t address, void *output, size_t size);

/**
 * Reads a pointer at address. A null pointer is accepted; any other value must
 * lie in the user-mode address space.
 */
int memory_read_pointer(rs3_context *context, uint64_t address, uint64_t *pointer);

#endif /* RS3_MEMORY_H */
