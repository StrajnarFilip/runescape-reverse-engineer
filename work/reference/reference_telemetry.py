"""Independent read-only Python telemetry for research and C parity benchmarks."""

import sys
import time

from reference_decoder import ProfileError, ReferenceDecoder
from reference_memory import MemoryReadError, ProcessMemory, processes, visible_client_pid

RETRY_INTERVAL_SECONDS = 1
SCHEMA_VERSION = 1


def report(message):
    print(message, file=sys.stderr, flush=True)


class PythonTelemetry:
    """Attaches to a pinned client on demand and returns schema-version-1 records."""

    def __init__(self, pid=None):
        self.pid = pid
        self.memory = None
        self.decoder = None
        self.retry_after = 0
        self.last_error = None

    def close(self):
        if self.memory is not None:
            self.memory.close()
        self.memory = None
        self.decoder = None

    def _select_pid(self):
        candidate_pids = set(processes())
        if self.pid is not None:
            candidate_pids.intersection_update((self.pid,))
        if not candidate_pids:
            raise MemoryReadError("No RS3 client")
        if len(candidate_pids) == 1:
            return next(iter(candidate_pids))
        pid = visible_client_pid(candidate_pids)
        if pid is not None:
            return pid
        # Preserve the headless fallback for Compatibility Mode helper processes.
        root_pids = candidate_pids.intersection(processes(include_children=False))
        if len(root_pids) != 1:
            raise MemoryReadError(
                "Multiple RS3 processes without a visible game window; specify --pid"
            )
        return next(iter(root_pids))

    def _attach(self):
        self.memory = ProcessMemory(self._select_pid())
        try:
            self.decoder = ReferenceDecoder(self.memory)
        except BaseException:
            self.close()
            raise
        report(f"Attached to verified RS3 client PID {self.memory.pid}")

    def sample(self, include_raw=False):
        """Return one record; failures produce an unavailable record and a retry delay."""
        try:
            if self.memory is not None and not self.memory.alive():
                self.close()
                self.retry_after = 0
            if self.memory is None:
                if time.perf_counter() < self.retry_after:
                    return self.unavailable(self.last_error or "Waiting for a client")
                self._attach()
            record = self.decoder.snapshot(include_raw)
            self.last_error = None
            return record
        except (OSError, ProfileError) as error:
            message = str(error)
            if message != self.last_error:
                report(message)
            self.last_error = message
            if self.memory is None or isinstance(error, ProfileError):
                self.close()
                self.retry_after = time.perf_counter() + RETRY_INTERVAL_SECONDS
            return self.unavailable(message)

    def unavailable(self, message):
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp_ms": time.time_ns() // 1_000_000,
            "pid": None if self.memory is None else self.memory.pid,
            "status": "unavailable",
            "reason": message,
            "health": None,
            "prayer": None,
            "adrenaline_percent": None,
            "gcd": None,
            "telos": None,
            "npcs": None,
            "hit_splats": None,
            "position": None,
        }
