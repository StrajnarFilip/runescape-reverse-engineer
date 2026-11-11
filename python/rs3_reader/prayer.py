"""ctypes binding of the deterministic C prayer planner (``include/rs3/prayer.h``).

The timing policy lives in ``src/prayer.c``. This module never sends input:
``Planner.step`` proposes only, and ``Planner.note_sent`` records input that
the caller's input layer accepted.
"""

from __future__ import annotations

import ctypes
from enum import IntEnum, IntFlag

from .native import load_library

MAX_THREATS = 128
OVERHEAD_PRAYER_MASK = 0x7C


class Prayer(IntEnum):
    """enum rs3_prayer_id."""

    UNKNOWN = 0
    NONE = 1
    MELEE = 2
    MAGIC = 3
    RANGED = 4
    SOUL_SPLIT = 5
    NECROMANCY = 6


class FrameFlags(IntFlag):
    """enum rs3_prayer_frame_flags."""

    CONNECTED = 1
    ACTIVE_KNOWN = 2
    POINTS_KNOWN = 4
    COVERAGE_KNOWN = 8


class ThreatFlags(IntFlag):
    """enum rs3_prayer_threat_flags."""

    TIMING_VALIDATED = 1


class Action(IntEnum):
    """enum rs3_prayer_action."""

    BLOCKED = 0
    HOLD = 1
    SET = 2
    WAIT_ACK = 3
    FAULT = 4


class DecisionFlags(IntFlag):
    """enum rs3_prayer_decision_flags."""

    DEADLINE_MISSED = 1


class PlannerStructure(IntEnum):
    """enum rs3_prayer_structure: identifiers accepted by rs3_prayer_sizeof()."""

    CONFIG = 1
    FRAME = 2
    THREAT = 3
    DECISION = 4


class Config(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("idle_prayer", ctypes.c_uint32),
        ("lead_ms", ctypes.c_uint32),
        ("hold_ms", ctypes.c_uint32),
        ("min_idle_ms", ctypes.c_uint32),
        ("ack_timeout_ms", ctypes.c_uint32),
        ("max_age_ms", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class Frame(ctypes.Structure):
    _fields_ = [
        ("now_ns", ctypes.c_uint64),
        ("observed_ns", ctypes.c_uint64),
        ("session_id", ctypes.c_uint64),
        ("sequence", ctypes.c_uint64),
        ("coverage_until_ns", ctypes.c_uint64),
        ("flags", ctypes.c_uint32),
        ("active_prayer", ctypes.c_uint32),
        ("available_mask", ctypes.c_uint32),
        ("prayer_points", ctypes.c_uint32),
    ]


class Threat(ctypes.Structure):
    _fields_ = [
        ("event_id", ctypes.c_uint64),
        ("source_instance", ctypes.c_uint64),
        ("check_earliest_ns", ctypes.c_uint64),
        ("check_latest_ns", ctypes.c_uint64),
        ("prayer", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class Decision(ctypes.Structure):
    _fields_ = [
        ("command_id", ctypes.c_uint64),
        ("threat_id", ctypes.c_uint64),
        ("next_wakeup_ns", ctypes.c_uint64),
        ("check_earliest_ns", ctypes.c_uint64),
        ("check_latest_ns", ctypes.c_uint64),
        ("action", ctypes.c_uint32),
        ("prayer", ctypes.c_uint32),
        ("reason", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


PLANNER_STRUCTURES = {
    PlannerStructure.CONFIG: Config,
    PlannerStructure.FRAME: Frame,
    PlannerStructure.THREAT: Threat,
    PlannerStructure.DECISION: Decision,
}

# Keys of the "timing" object in profiles/prayer_flick.json, in Config order.
TIMING_SETTINGS = ("lead_ms", "hold_ms", "min_idle_ms", "ack_timeout_ms", "max_age_ms")


def prayer_id(value) -> Prayer:
    """Return a Prayer from its name (case-insensitive) or its numeric ID."""
    return Prayer[value.upper()] if isinstance(value, str) else Prayer(value)


def _declare_planner_functions(library):
    handle = ctypes.c_void_p
    u32, i32, u64 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint64
    prototypes = {
        "sizeof": (u32, [u32]),
        "reason_string": (ctypes.c_char_p, [u32]),
        "create": (i32, [ctypes.POINTER(Config), ctypes.POINTER(handle)]),
        "destroy": (None, [handle]),
        "reset": (i32, [handle]),
        "step": (
            i32,
            [
                handle,
                ctypes.POINTER(Frame),
                ctypes.POINTER(Threat),
                u32,
                ctypes.POINTER(Decision),
                u32,
            ],
        ),
        "note_sent": (i32, [handle, u64, u64]),
    }
    for name, (result_type, argument_types) in prototypes.items():
        function = getattr(library, "rs3_prayer_" + name)
        function.restype = result_type
        function.argtypes = argument_types
    for structure_id, structure in PLANNER_STRUCTURES.items():
        if library.rs3_prayer_sizeof(structure_id) != ctypes.sizeof(structure):
            raise RuntimeError("C/Python prayer layout mismatch: " + structure.__name__)


class Planner:
    """One serialized planner; close it explicitly or use it as a context manager."""

    def __init__(self, settings, library=None):
        self.library = load_library(library)
        _declare_planner_functions(self.library)
        self.handle = ctypes.c_void_p()
        timing = settings["timing"]
        self.config = Config(
            ctypes.sizeof(Config),
            prayer_id(settings.get("idle_prayer", "none")),
            *(timing[key] for key in TIMING_SETTINGS),
            0,
        )
        self._check_result(
            self.library.rs3_prayer_create(ctypes.byref(self.config), ctypes.byref(self.handle))
        )

    def _check_result(self, result):
        if result:
            raise RuntimeError(self.library.rs3_result_string(result).decode())

    def _require_open(self):
        if not self.handle:
            raise RuntimeError("Planner is closed")

    def step(self, frame, threats=()):
        """Return the Decision for a frame and the complete list of pending threats."""
        self._require_open()
        if len(threats) > MAX_THREATS:
            raise ValueError(f"At most {MAX_THREATS} pending threats are supported")
        threat_array = (Threat * len(threats))(*threats)
        decision = Decision()
        self._check_result(
            self.library.rs3_prayer_step(
                self.handle,
                ctypes.byref(frame),
                threat_array,
                len(threat_array),
                ctypes.byref(decision),
                ctypes.sizeof(decision),
            )
        )
        return decision

    def note_sent(self, command_id, sent_at_ns):
        """Record that the input layer accepted the proposal's key chord."""
        self._require_open()
        self._check_result(self.library.rs3_prayer_note_sent(self.handle, command_id, sent_at_ns))

    def reset(self):
        """Clear pending input and latched faults after re-observing the game."""
        self._require_open()
        self._check_result(self.library.rs3_prayer_reset(self.handle))

    def describe(self, decision):
        """Return a JSON-ready description of a Decision."""
        return {
            "action": Action(decision.action).name.lower(),
            "prayer": Prayer(decision.prayer).name.lower(),
            "reason": self.library.rs3_prayer_reason_string(decision.reason).decode(),
            "command_id": decision.command_id,
            "threat_id": decision.threat_id,
            "deadline_missed": bool(decision.flags & DecisionFlags.DEADLINE_MISSED),
            "next_wakeup_ns": decision.next_wakeup_ns,
        }

    def close(self):
        if self.handle:
            self.library.rs3_prayer_destroy(self.handle)
            self.handle = ctypes.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.close()
