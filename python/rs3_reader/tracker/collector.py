"""Background collection: samples native telemetry, logs every record and publishes the newest."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from contextlib import closing
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..hit_history import HitHistory
from ..native import REPOSITORY_ROOT
from ..telemetry import NativeTelemetry

PACKAGE_DIRECTORY = Path(__file__).resolve().parents[1]
LOG_DIRECTORY = PACKAGE_DIRECTORY / "logs"
LOG_MAX_BYTES = 25 * 1024 * 1024
LOG_BACKUP_COUNT = 3
COLLECTION_PERIOD_SECONDS = 0.05
# Reset the schedule after a long pause to bound catch-up sampling.
MAX_SCHEDULE_LAG_SECONDS = 0.5
RECENT_INVENTORY_CHANGE_CAPACITY = 100
SUPPORTED_PROFILE_FILES = ("profile_951_3.json", "profile_6ada3f59.json")


def new_log_path():
    """Return a fresh capture path named after the start time and this process ID."""
    LOG_DIRECTORY.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("capture-%Y%m%d-%H%M%S-")
    return LOG_DIRECTORY / f"{timestamp}{os.getpid()}.jsonl"


def supported_executable_hashes():
    profiles = REPOSITORY_ROOT / "profiles"
    return {
        json.loads((profiles / name).read_text(encoding="utf-8"))["sha256"]
        for name in SUPPORTED_PROFILE_FILES
    }


class TelemetryCollector:
    """Samples on a daemon thread and hands only the newest record to the UI thread."""

    def __init__(self, pid, native_hz, history_path, log_path):
        self.pid = pid
        self.native_hz = native_hz
        self.history_path = history_path
        self.log_path = log_path
        self.stop_requested = threading.Event()
        # A one-slot queue: the UI always receives the newest view.
        self.latest_records = queue.Queue(maxsize=1)
        self.sample_count = 0
        self.hit_history = HitHistory()
        self.inventory_changes = deque(maxlen=RECENT_INVENTORY_CHANGE_CAPACITY)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self, timeout=2):
        self.stop_requested.set()
        self.thread.join(timeout=timeout)

    def take_latest(self):
        """Return (record, received perf_counter time), or None when nothing is new."""
        try:
            return self.latest_records.get_nowait()
        except queue.Empty:
            return None

    def _publish(self, record):
        try:
            self.latest_records.get_nowait()
        except queue.Empty:
            pass
        self.latest_records.put_nowait((record, time.perf_counter()))

    def _import_history(self):
        """Import hit history only from captures of a supported executable."""
        supported_hashes = supported_executable_hashes()
        with Path(self.history_path).open(encoding="utf-8") as capture:
            for line in capture:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("executable_sha256") in supported_hashes:
                    self.hit_history.observe(record)

    def _collect_once(self, telemetry, log):
        record = telemetry.sample(True)
        self.sample_count += 1
        record["sample_sequence"] = self.sample_count
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        # Log every collected record before coalescing updates for display.
        log.emit(logging.makeLogRecord({"msg": line}))
        self.hit_history.observe(record)
        inventory_changes = record.get("inventory_changes") or {}
        self.inventory_changes.extend(inventory_changes.get("newly_observed", []))
        self._publish(
            dict(
                record,
                recent_hit_splats=list(self.hit_history.events),
                recent_inventory_changes=list(self.inventory_changes),
            )
        )

    def _run(self):
        telemetry = None
        try:
            telemetry = NativeTelemetry(pid=self.pid, worker_hz=self.native_hz)
            if self.history_path:
                self._import_history()
            log_handler = RotatingFileHandler(
                self.log_path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            with closing(log_handler) as log:
                deadline = time.perf_counter()
                while not self.stop_requested.is_set():
                    self._collect_once(telemetry, log)
                    deadline += COLLECTION_PERIOD_SECONDS
                    delay = deadline - time.perf_counter()
                    if delay < -MAX_SCHEDULE_LAG_SECONDS:
                        deadline = time.perf_counter()
                    self.stop_requested.wait(max(0, delay))
        except Exception as error:
            self._publish(NativeTelemetry.unavailable("Tracker: " + str(error)))
        finally:
            if telemetry is not None:
                telemetry.close()
