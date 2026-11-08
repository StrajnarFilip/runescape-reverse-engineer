"""Read-only connection lifecycle shared by the CLI and desktop tracker."""
import sys
import time
from winmem import ProcessMemory, processes, MemoryReadError
from rs3 import RS3, ProfileError

class Telemetry:
    def __init__(self, pid=None):
        self.pid = pid
        self.memory = self.decoder = None
        self.retry_after = 0
        self.last_error = None

    def close(self):
        if self.memory is not None:
            self.memory.close()
        self.memory = self.decoder = None

    def sample(self, include_raw=False):
        try:
            if self.memory is not None and not self.memory.alive():
                self.close()
                self.retry_after = 0
            if self.memory is None:
                if time.perf_counter() < self.retry_after:
                    return self.unavailable(self.last_error or "Waiting for a client")
                pids = [p for p in processes() if self.pid is None or p == self.pid]
                if len(pids) != 1:
                    raise MemoryReadError("No RS3 client" if not pids else "Multiple clients: specify --pid")
                self.memory = ProcessMemory(pids[0])
                try:
                    self.decoder = RS3(self.memory)
                except BaseException:
                    self.close()
                    raise
                print("Attached to verified RS3 client PID %d" % self.memory.pid, file=sys.stderr, flush=True)
            record = self.decoder.snapshot(include_raw)
            self.last_error = None
            return record
        except (OSError, ProfileError) as error:
            message = str(error)
            if message != self.last_error:
                print(message, file=sys.stderr, flush=True)
            self.last_error = message
            if self.memory is None or isinstance(error, ProfileError):
                self.close()
                self.retry_after = time.perf_counter()+1
            return self.unavailable(message)

    def unavailable(self, message):
        return {"schema_version": 1, "timestamp_ms": time.time_ns()//1000000,
                "pid": None if self.memory is None else self.memory.pid,
                "status": "unavailable", "reason": message, "health": None, "prayer": None,
                "adrenaline_percent": None, "gcd": None, "telos": None, "npcs": None}
