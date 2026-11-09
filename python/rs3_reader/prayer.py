"""ctypes consumer of the C prayer planner; timing policy lives in src/prayer.c."""
import ctypes as C
from enum import IntEnum
from native import load_library, fields, U32, U64, I32


class Prayer(IntEnum):
    UNKNOWN = 0
    NONE = 1
    MELEE = 2
    MAGIC = 3
    RANGED = 4
    SOUL_SPLIT = 5
    NECROMANCY = 6


class Config(C.Structure):
    _fields_ = fields(U32, 'struct_size idle_prayer lead_ms hold_ms min_idle_ms ack_timeout_ms max_age_ms reserved')


class Frame(C.Structure):
    _fields_ = fields(U64, 'now_ns observed_ns session_id sequence coverage_until_ns') + fields(
        U32, 'flags active_prayer available_mask prayer_points')


class Threat(C.Structure):
    _fields_ = fields(U64, 'event_id source_instance check_earliest_ns check_latest_ns') + fields(U32, 'prayer flags')


class Decision(C.Structure):
    _fields_ = fields(U64, 'command_id threat_id next_wakeup_ns check_earliest_ns check_latest_ns') + fields(
        U32, 'action prayer reason flags')


def prayer_id(value):
    return Prayer[value.upper()] if isinstance(value, str) else Prayer(value)


class Planner:
    """One serialized planner; close it explicitly or use a context manager.

    step() proposes only. note_sent() records accepted input, never assumes
    that a key reached the server. This class does not send any keyboard input.
    """
    def __init__(self, settings, library=None):
        self.lib = load_library(library)
        self.handle = C.c_void_p()
        pointer = C.c_void_p
        signatures = {
            'sizeof': (U32, [U32]), 'reason_string': (C.c_char_p, [U32]),
            'create': (I32, [C.POINTER(Config), C.POINTER(pointer)]), 'destroy': (None, [pointer]),
            'reset': (I32, [pointer]), 'step': (I32, [pointer, C.POINTER(Frame), C.POINTER(Threat), U32,
                                                  C.POINTER(Decision), U32]),
            'note_sent': (I32, [pointer, U64, U64]),
        }
        for name, (restype, args) in signatures.items():
            fn = getattr(self.lib, 'rs3_prayer_' + name)
            fn.restype, fn.argtypes = restype, args
        for i, struct in enumerate((Config, Frame, Threat, Decision), 1):
            if self.lib.rs3_prayer_sizeof(i) != C.sizeof(struct):
                raise RuntimeError('C/Python prayer layout mismatch: ' + struct.__name__)
        timing = settings['timing']
        self.config = Config(C.sizeof(Config), prayer_id(settings.get('idle_prayer', 'none')),
                             *(timing[k] for k in ('lead_ms', 'hold_ms', 'min_idle_ms', 'ack_timeout_ms', 'max_age_ms')), 0)
        self._check(self.lib.rs3_prayer_create(C.byref(self.config), C.byref(self.handle)))

    def _check(self, result):
        if result:
            raise RuntimeError(self.lib.rs3_result_string(result).decode())

    def step(self, frame, threats=()):
        if not self.handle:
            raise RuntimeError('Planner is closed')
        if len(threats) > 128:
            raise ValueError('At most 128 pending threats are supported')
        buffer = (Threat * len(threats))(*threats)
        out = Decision()
        self._check(self.lib.rs3_prayer_step(self.handle, C.byref(frame), buffer, len(buffer),
                                           C.byref(out), C.sizeof(out)))
        return out

    def note_sent(self, command_id, sent_at_ns):
        if not self.handle:
            raise RuntimeError('Planner is closed')
        self._check(self.lib.rs3_prayer_note_sent(self.handle, command_id, sent_at_ns))

    def reset(self):
        if not self.handle:
            raise RuntimeError('Planner is closed')
        self._check(self.lib.rs3_prayer_reset(self.handle))

    def describe(self, decision):
        return {'action': ('blocked', 'hold', 'set', 'wait_ack', 'fault')[decision.action],
                'prayer': Prayer(decision.prayer).name.lower(),
                'reason': self.lib.rs3_prayer_reason_string(decision.reason).decode(),
                'command_id': decision.command_id, 'threat_id': decision.threat_id,
                'deadline_missed': bool(decision.flags & 1), 'next_wakeup_ns': decision.next_wakeup_ns}

    def close(self):
        if self.handle:
            self.lib.rs3_prayer_destroy(self.handle)
            self.handle = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
