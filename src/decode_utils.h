/*
 * Helpers for interpreting bytes copied out of the client process.
 *
 * Client fields can be unaligned inside bulk-read buffers, so every multi-byte
 * value is copied with memcpy. The target is little-endian Windows x64.
 */
#ifndef RS3_DECODE_UTILS_H
#define RS3_DECODE_UTILS_H

#include <stdint.h>
#include <string.h>

/** Lowest address the reader treats as a valid user-mode pointer. */
#define REMOTE_POINTER_MINIMUM 0x10000ull
/** Exclusive upper bound of the Windows x64 user-mode address space. */
#define REMOTE_POINTER_LIMIT 0x7fffffffffffull

static inline uint32_t decode_u32(const void *bytes)
{
    uint32_t value;
    memcpy(&value, bytes, sizeof value);
    return value;
}

static inline int32_t decode_i32(const void *bytes)
{
    int32_t value;
    memcpy(&value, bytes, sizeof value);
    return value;
}

static inline uint64_t decode_u64(const void *bytes)
{
    uint64_t value;
    memcpy(&value, bytes, sizeof value);
    return value;
}

static inline void encode_u32(void *bytes, uint32_t value)
{
    memcpy(bytes, &value, sizeof value);
}

/**
 * Returns later - earlier as a signed tick count.
 * Subtracting before interpreting the sign lets nearby ticks span a 32-bit wrap.
 */
static inline int32_t tick_difference(uint32_t later, uint32_t earlier)
{
    return (int32_t)(later - earlier);
}

/** Returns nonzero when address lies inside the user-mode address space. */
static inline int is_valid_remote_pointer(uint64_t address)
{
    return address >= REMOTE_POINTER_MINIMUM && address < REMOTE_POINTER_LIMIT;
}

/** Returns nonzero when text[0..length) is well-formed UTF-8. An empty range is valid. */
int is_valid_utf8(const char *text, uint32_t length);

#endif /* RS3_DECODE_UTILS_H */
