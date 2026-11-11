/* Time sources used for sample timestamps and ages. */
#ifndef RS3_CLOCK_H
#define RS3_CLOCK_H

#include <stdint.h>

/** Monotonic nanoseconds from the performance counter; the rs3_monotonic_ns() time base. */
uint64_t clock_monotonic_ns(void);

/** Wall-clock nanoseconds since the Unix epoch. */
uint64_t clock_unix_ns(void);

#endif /* RS3_CLOCK_H */
