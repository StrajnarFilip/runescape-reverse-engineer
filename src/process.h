/*
 * Read-only client discovery, attachment and fingerprinting.
 *
 * A client is attached only after its executable SHA-256 selects a pinned
 * profile and every loaded-code signature of that profile matches.
 */
#ifndef RS3_PROCESS_H
#define RS3_PROCESS_H

#include <stdint.h>

#include "context.h"

/** Returns the Windows FILETIME creation time of a process, or 0 on failure. */
uint64_t process_creation_time(HANDLE process);

/**
 * Discovers, opens and fingerprints the client.
 * Returns nonzero when attached; otherwise the context records the reason.
 */
int process_attach(rs3_context *context);

/** Closes the process handle and resets every per-process tracking state. */
void process_detach(rs3_context *context);

/** Remembers the current failure, detaches and defers the next attachment attempt. */
void process_schedule_retry(rs3_context *context, uint64_t now_ns);

/**
 * Selects the profile whose executable hash equals sha256 and verifies its
 * code signatures in the attached module. Sets target.profile on success.
 */
int process_select_profile(rs3_context *context, const char *sha256);

#endif /* RS3_PROCESS_H */
