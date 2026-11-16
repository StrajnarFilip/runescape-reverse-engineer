"""Recorder queue/Windows layouts are checked without installing hooks or opening a game."""

from collections import deque
import ctypes
import importlib.util
from pathlib import Path
import sys
import threading
import unittest

WORK = Path(__file__).resolve().parents[2] / "work"
sys.path.insert(0, str(WORK))
spec = importlib.util.spec_from_file_location(
    "record_manual_opening", WORK / "record_manual_opening.py"
)
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


class ManualRecorderTests(unittest.TestCase):
    def test_input_queue_preserves_order_and_reports_overflow(self):
        capture = recorder.ManualInputs.__new__(recorder.ManualInputs)
        capture.queue, capture.lock = deque(), threading.Lock()
        capture.sequence = capture.lost = capture.errors = 0
        for index in range(recorder.QUEUE_CAPACITY + 2):
            capture._append(dict(kind="keyboard", key=65, received_ns=1000 + index))
        events, lost, errors = capture.drain()
        self.assertEqual(
            [event["sequence"] for event in events], list(range(1, recorder.QUEUE_CAPACITY + 1))
        )
        self.assertEqual(lost, 2)
        self.assertEqual(errors, 0)
        events.clear()
        self.assertEqual(capture.drain(), ([], 2, 0))

    @unittest.skipUnless(
        sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8, "Windows x64 hook ABI"
    )
    def test_windows_x64_input_structures_do_not_truncate_pointer_fields(self):
        self.assertEqual(ctypes.sizeof(recorder.KeyboardEvent), 24)
        self.assertEqual(ctypes.sizeof(recorder.MouseEvent), 32)
        self.assertEqual(recorder.KeyboardEvent.extra.offset, 16)
        self.assertEqual(recorder.MouseEvent.extra.offset, 24)


if __name__ == "__main__":
    unittest.main()
