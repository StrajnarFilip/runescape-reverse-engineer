/* Time sources used for sample timestamps and ages. */
#include "clock.h"

#include <windows.h>

#define NANOSECONDS_PER_SECOND        1000000000ull
#define NANOSECONDS_PER_FILETIME_UNIT 100ull
/** FILETIME counts 100 ns intervals from 1601; this is 1970-01-01 in those units. */
#define FILETIME_UNIX_EPOCH 116444736000000000ull

uint64_t clock_monotonic_ns(void)
{
    LARGE_INTEGER counter;
    LARGE_INTEGER frequency;
    QueryPerformanceCounter(&counter);
    QueryPerformanceFrequency(&frequency);

    /* Scale whole seconds separately to avoid multiplying the full counter by one billion. */
    uint64_t ticks = (uint64_t)counter.QuadPart;
    uint64_t ticks_per_second = (uint64_t)frequency.QuadPart;
    uint64_t whole_seconds = ticks / ticks_per_second;
    uint64_t remaining_ticks = ticks % ticks_per_second;
    return whole_seconds * NANOSECONDS_PER_SECOND +
           remaining_ticks * NANOSECONDS_PER_SECOND / ticks_per_second;
}

uint64_t clock_unix_ns(void)
{
    FILETIME file_time;
    ULARGE_INTEGER intervals;
    GetSystemTimePreciseAsFileTime(&file_time);
    intervals.LowPart = file_time.dwLowDateTime;
    intervals.HighPart = file_time.dwHighDateTime;
    return (intervals.QuadPart - FILETIME_UNIX_EPOCH) * NANOSECONDS_PER_FILETIME_UNIT;
}
