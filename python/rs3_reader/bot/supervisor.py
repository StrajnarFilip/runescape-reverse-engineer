"""D8 shadow supervisor, bounded replay/live observation and explicit stop paths.

Shadow mode owns only a RecordingActuator. Assist/full are recognized but remain
blocked until track L supplies reviewed enablement. Missing producer modules
are absent, never replaced with guessed decisions. All clocks for game data
remain in the native domain; wall-clock limits are separate.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Mapping

from .actuator import RecordingActuator
from .intents import Intent
from .replay import read_capture
from .world_state import Observation, WorldState

NS_PER_MS = 1_000_000
DEFAULT_KILL_KEY = "Ctrl+Alt+Pause"
PRODUCER_MODULES = {
    "encounter": "encounter_tracker",
    "prayer": "prayer_executor",
    "survival": "survival",
    "rotation": "rotation",
    "mechanics": "mechanics",
    "trip": "trip_loop",
}
NATIVE_EVENT_STREAMS = ("npc_actions", "npc_hits", "player_hits", "inventory_changes")
MAXIMUM_PROPOSALS = 256
ACTIVE_TELOS_CONFIG = 22891
NORMAL_STOPS = {"kill_count_cap", "wall_time_cap", "keyboard_interrupt", "capture_complete"}


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def load_producers(names):
    """Load available trusted modules; do not hide errors inside installed modules."""
    producers, absent = {}, []
    for name in names:
        if name not in PRODUCER_MODULES:
            raise ValueError("Unknown producer: " + name)
        module_name = "rs3_reader.bot." + PRODUCER_MODULES[name]
        if importlib.util.find_spec(module_name) is None:
            absent.append(name)
            continue
        module = importlib.import_module(module_name)
        factory = getattr(module, "create_stack", None)
        if not callable(factory):
            raise ValueError("Producer has no create_stack: " + name)
        producers[name] = factory()
    return producers, tuple(absent)


def hotkey_codes(label):
    """Translate an explicit stop chord; default is configurable Ctrl+Alt+Pause."""
    pieces = label.split("+")
    keys = {"Pause": 0x13, **{"F" + str(index): 0x6F + index for index in range(1, 13)}}
    modifiers = {"Ctrl": 2, "Alt": 1, "Shift": 4, "Win": 8}
    if (
        len(set(pieces)) != len(pieces)
        or not pieces
        or pieces[-1] not in keys
        or any(part not in modifiers for part in pieces[:-1])
    ):
        raise ValueError("Use Ctrl+Alt+Pause or an explicit modified F1-F12 stop chord")
    mask = sum(modifiers[part] for part in pieces[:-1])
    if not mask:
        raise ValueError("The global stop key requires a modifier")
    return mask | 0x4000, keys[pieces[-1]]  # MOD_NOREPEAT


class GlobalKillSwitch:
    """Thread-owned Windows hotkey. Construct only for an explicit live observer."""

    def __init__(self, label=DEFAULT_KILL_KEY):
        from ctypes import wintypes

        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user.RegisterHotKey.restype = wintypes.BOOL
        self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.UnregisterHotKey.restype = wintypes.BOOL
        self.user.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user.PeekMessageW.restype = wintypes.BOOL
        self.message, self.identifier, self.registered = wintypes.MSG(), 0x5253, False
        modifiers, virtual_key = hotkey_codes(label)
        if not self.user.RegisterHotKey(None, self.identifier, modifiers, virtual_key):
            raise OSError(ctypes.get_last_error(), "Global kill-switch registration failed")
        self.registered = True

    def __call__(self):
        while self.user.PeekMessageW(ctypes.byref(self.message), None, 0x312, 0x312, 1):
            if self.message.wParam == self.identifier:
                return True
        return False

    def close(self):
        if self.registered:
            self.user.UnregisterHotKey(None, self.identifier)
            self.registered = False


def foreground_matches(world):
    """Read foreground ownership without moving focus or sending input."""
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    window = user.GetForegroundWindow()
    return bool(
        window
        and user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        and pid.value == world.session.pid
    )


class Supervisor:
    """Pure frame watchdogs plus recording-only composition, no background input.

    A kill result needs a fresh explicit validated encounter_result envelope;
    NPC HP zero, despawn or a raw stage never increments the kill count. Shadow
    replay has no foreground requirement unless a focus checker is injected.
    Every stop closes the arbiter/backend once. No fault auto-resumes.
    """

    def __init__(
        self,
        *,
        mode="shadow",
        producers=None,
        actuator=None,
        arbiter=None,
        native_clock=None,
        wall_clock=time.monotonic,
        kill_switch=None,
        focus_checker=None,
        attack_validator=None,
        maximum_age_ms=150,
        maximum_seconds=1800,
        maximum_kills=1,
        missing=(),
    ):
        if mode not in ("shadow", "assist", "full"):
            raise ValueError("Unknown run mode")
        if mode != "shadow":
            raise ValueError("Live modes require track L authorization; only shadow is available")
        if (
            not _integer(maximum_age_ms, 1)
            or not _integer(maximum_kills, 1)
            or type(maximum_seconds) not in (int, float)
            or not math.isfinite(maximum_seconds)
            or maximum_seconds <= 0
        ):
            raise ValueError("Supervisor caps must be positive and finite")
        self.world = None
        self.native_clock = native_clock or (lambda: self.world.now_ns)
        self.wall_clock, self.started = wall_clock, wall_clock()
        self.kill_switch, self.focus_checker, self.attack_validator = (
            kill_switch or (lambda: False),
            focus_checker,
            attack_validator,
        )
        self.producers, self.missing = dict(producers or {}), tuple(missing)
        self.actuator = (
            actuator if actuator is not None else RecordingActuator(clock=self.native_clock)
        )
        if not isinstance(self.actuator, RecordingActuator):
            raise ValueError("Shadow mode accepts only the recording fake actuator")
        if arbiter is not None and arbiter.actuator is not self.actuator:
            raise ValueError("Supervisor and arbiter must share the recording actuator")
        self.arbiter = arbiter
        self.maximum_age_ms, self.maximum_seconds, self.maximum_kills = (
            maximum_age_ms,
            maximum_seconds,
            maximum_kills,
        )
        self.identity, self.sequence, self.last_time = None, None, None
        self.stopped, self.reason, self._closed = False, None, False
        self.kills, self.deaths, self.samples = 0, 0, 0
        self.watchdog_trips, self.kill_reports, self.results_seen = [], [], set()
        self.last_log = None

    def stop(self, reason):
        """Latch a stop and release all device-owned keys; never silently resume."""
        if not self.stopped:
            self.stopped, self.reason = True, reason
            if reason not in NORMAL_STOPS:
                self.watchdog_trips.append(reason)
        self.close()

    def close(self):
        """Close the optional arbiter or the recording backend, idempotently."""
        if not self._closed:
            self._closed = True
            try:
                if self.arbiter is not None:
                    self.arbiter.close()
                else:
                    self.actuator.close()
            except Exception:
                self.stopped, self.reason = True, "backend_close_failed_input_state_unknown"
                self.watchdog_trips.append(self.reason)
            for name, producer in self.producers.items():
                close = getattr(producer, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        self.stopped, self.reason = True, "producer_close_failed:" + name
                        self.watchdog_trips.append(self.reason)

    def _fresh(self, observation, now, world):
        return (
            observation.source
            and observation.reason is None
            and observation.is_fresh(self.maximum_age_ms)
            and observation.age_ms + (now - world.now_ns) / NS_PER_MS <= self.maximum_age_ms
        )

    def _watchdog(self, world, now):
        if self.kill_switch():
            return "kill_switch"
        if self.wall_clock() - self.started >= self.maximum_seconds:
            return "wall_time_cap"
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or not _integer(now)
            or not _integer(world.observed_ns)
            or not _integer(world.now_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0 <= now - world.observed_ns <= self.maximum_age_ms * NS_PER_MS
            or world.now_ns > now
        ):
            return "snapshot_stale_or_unavailable"
        identity = (world.session, world.raw.get("executable_sha256"))
        if self.identity is not None and self.identity != identity:
            return "session_or_build_changed"
        if self.last_time is not None and (
            now < self.last_time or world.sample_sequence < self.sequence
        ):
            return "clock_or_sample_regressed"
        if self.focus_checker is not None and self.focus_checker(world) is not True:
            return "window_focus_lost_or_unknown"
        for name in NATIVE_EVENT_STREAMS:
            batch = getattr(world.events, name)
            loss = batch.lost
            if (
                not batch.records.known
                or batch.records.reason is not None
                or type(loss.value) is not int
                or loss.value != 0
                or loss.reason is not None
                or not loss.source
            ):
                return "event_queue_loss_or_unknown:" + name
        actors = {
            npc.instance_id.value
            for npc in world.npcs.value or ()
            if npc.config_id.value == ACTIVE_TELOS_CONFIG
            or npc.name.value
            and npc.name.value.partition(",")[0].casefold() == "telos"
        }
        for event in world.events.npc_actions.records.value or ():
            if event.get("instance_id") in actors and (
                event.get("pid"),
                event.get("process_creation_time"),
                event.get("scene_epoch"),
            ) == (
                world.session.pid,
                world.session.process_creation_time,
                world.session.scene_epoch,
            ):
                if self.attack_validator is None or self.attack_validator(event, world) is not True:
                    return "unknown_telos_sequence"
        return None

    def _producer_fault(self):
        for name, producer in self.producers.items():
            for state in (
                getattr(producer, "last_report", None),
                getattr(producer, "last_log", None),
                getattr(producer, "state", None),
            ):
                if _field(state, "fault"):
                    return "producer_fault:" + name
                if any(
                    "unlabelled" in reason or "overflow" in reason or "event_sequence_gap" in reason
                    for reason in _field(state, "reasons", ())
                ):
                    return "encounter_evidence_gap:" + name
        return None

    def _result(self, world, now):
        raw = world.raw.get("encounter_result")
        if not isinstance(raw, dict) and not hasattr(raw, "get"):
            return
        age = raw.get("age_ms")
        captured_age = world.raw.get("native", {}).get("snapshot_age_ms")
        captured_age = captured_age if type(captured_age) in (int, float) else 0
        if type(age) in (int, float):
            age += max(0, (world.now_ns - world.observed_ns) / NS_PER_MS - captured_age)
        observation = Observation(raw.get("value"), age, raw.get("source"), raw.get("reason"))
        if not self._fresh(observation, now, world):
            return
        result = observation.value
        if (
            not hasattr(result, "get")
            or result.get("validated") is not True
            or result.get("kind") not in ("kill", "death")
            or not result.get("id")
        ):
            return
        identity = (world.session, result["id"])
        if identity in self.results_seen:
            return
        self.results_seen.add(identity)
        if result["kind"] == "death":
            self.deaths += 1
            self.stop("player_death")
            return
        self.kills += 1
        self.kill_reports.append(
            {
                "kind": "kill_report",
                "ordinal": self.kills,
                "verified": True,
                "source": observation.source,
                "samples": self.samples,
                "recorded_acceptances": sum(
                    attempt.result.accepted for attempt in self.actuator.attempts
                ),
                "watchdog_trips": len(self.watchdog_trips),
            }
        )
        if self.kills >= self.maximum_kills:
            self.stop("kill_count_cap")

    def step(self, world):
        """Consume one frame; return shadow proposals for the S0a replay harness."""
        if not isinstance(world, WorldState):
            raise TypeError("Supervisor requires WorldState")
        self.world, self.samples = world, self.samples + 1
        proposals, now = (), world.now_ns
        try:
            now = self.native_clock()
            if not self.stopped:
                reason = self._watchdog(world, now)
                if reason:
                    self.stop(reason)
            if not self.stopped:
                self.identity = (world.session, world.raw.get("executable_sha256"))
                self.sequence, self.last_time = world.sample_sequence, now
                values = []
                for name, producer in self.producers.items():
                    for intent in producer.step(world):
                        if not isinstance(intent, Intent) or len(values) >= MAXIMUM_PROPOSALS:
                            raise ValueError("Invalid producer proposals: " + name)
                        values.append(intent)
                proposals = tuple(values)
                reason = self._producer_fault()
                if reason:
                    self.stop(reason)
                if not self.stopped and self.arbiter is not None:
                    result = self.arbiter.step(world, proposals)
                    if result.fault:
                        self.stop("arbiter_fault:" + result.fault)
                if not self.stopped:
                    self._result(world, now)
        except Exception as error:
            self.stop("supervisor_exception:" + type(error).__name__)
        self.last_log = {
            "schema_version": 1,
            "kind": "supervisor_frame",
            "mode": "shadow",
            "sample_sequence": world.sample_sequence,
            "observed_ns": world.observed_ns,
            "now_ns": now,
            "stopped": self.stopped,
            "stop_reason": self.reason,
            "missing_producers": list(self.missing),
            "intents": [intent.to_record() for intent in proposals],
            "input_sent": False,
        }
        if self.arbiter is not None:
            arbitration = getattr(self.arbiter, "last_log", None)
            self.last_log["arbitration"] = (
                arbitration.to_record() if arbitration is not None else None
            )
            self.last_log["dispatch_enabled"] = False
        return proposals

    def report(self):
        """Return compact session totals, never infer a successful kill from despawn."""
        report = {
            "schema_version": 1,
            "kind": "session_report",
            "mode": "shadow",
            "samples": self.samples,
            "verified_kills": self.kills,
            "verified_deaths": self.deaths,
            "stop_reason": self.reason,
            "watchdog_trips": list(self.watchdog_trips),
            "missing_producers": list(self.missing),
            "kill_reports": list(self.kill_reports),
            "input_sent": False,
        }
        if self.arbiter is not None:
            report["shadow_arbitration"] = True
            report["dispatch_enabled"] = False
        return report


class _EncounterConsumer:
    """Pass same-frame D1 state to a consumer while preserving its D6 hooks."""

    def __init__(self, producer, observer):
        self.producer, self.observer = producer, observer

    def step(self, world):
        return self.producer.step(world, encounter=self.observer.state)

    def __getattr__(self, name):
        return getattr(self.producer, name)


def create_shadow_supervisor(*, arbitrate=False, **options):
    """Compose optional D6 logging with one recording actuator and dispatch disabled.

    Import D6 only when requested. It may not yet be merged on a standalone D8
    branch. The clock resolves through the supervisor on each call so an explicit
    live observer can subsequently install its native monotonic clock.
    """
    if type(arbitrate) is not bool:
        raise ValueError("Shadow arbitration must be an explicit boolean")
    if arbitrate and any(name in options for name in ("actuator", "arbiter")):
        raise ValueError("Shadow composition owns one recording actuator and arbiter")
    producers = dict(options.get("producers") or {})
    observer = producers.get("encounter")
    if observer is not None:
        producers = {
            "encounter": observer,
            **{name: producer for name, producer in producers.items() if name != "encounter"},
        }
        prayer = producers.get("prayer")
        if prayer is not None and hasattr(prayer, "encounter_provider"):
            prayer.encounter_provider = lambda: observer.state
        if "survival" in producers:
            producers["survival"] = _EncounterConsumer(producers["survival"], observer)
    options["producers"] = producers
    supervisor = Supervisor(**options)
    if arbitrate:
        try:
            from .arbiter import Arbiter

            supervisor.arbiter = Arbiter(
                supervisor.actuator,
                clock=lambda: supervisor.native_clock(),
                producers=supervisor.producers,
                enabled=False,
            )
        except ModuleNotFoundError as error:
            supervisor.close()
            if error.name == "rs3_reader.bot.arbiter":
                raise RuntimeError(
                    "D6 is required for --arbitrate; integrate its module first"
                ) from error
            raise
        except Exception:
            supervisor.close()
            raise
    return supervisor


def main(argv=None):
    """Run bounded shadow capture/live observation; live actuation belongs to L."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("shadow", "assist", "full"), default="shadow")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path)
    source.add_argument("--pid", type=int)
    parser.add_argument("--enable", default=",".join(PRODUCER_MODULES))
    parser.add_argument("--max-seconds", type=float, default=1800)
    parser.add_argument("--max-kills", type=int, default=1)
    parser.add_argument("--kill-key", default=DEFAULT_KILL_KEY)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--arbitrate",
        action="store_true",
        help="Include D6 shadow decisions; dispatch remains disabled",
    )
    arguments = parser.parse_args(argv)
    if arguments.mode != "shadow":
        parser.error("assist/full require track L authorization; use shadow")
    if (
        arguments.capture
        and arguments.output
        and arguments.capture.resolve() == arguments.output.resolve()
    ):
        parser.error("Capture and output must differ")
    output, telemetry, hotkey, supervisor = None, None, None, None
    try:
        hotkey_codes(arguments.kill_key)
        producers, missing = load_producers(
            tuple(name for name in arguments.enable.split(",") if name)
        )
        supervisor = create_shadow_supervisor(
            arbitrate=arguments.arbitrate,
            producers=producers,
            missing=missing,
            maximum_seconds=arguments.max_seconds,
            maximum_kills=arguments.max_kills,
        )
        output = arguments.output.open("x", encoding="utf-8") if arguments.output else sys.stdout

        def emit(record):
            output.write(
                json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            )

        if arguments.capture:
            with arguments.capture.open(encoding="utf-8-sig") as stream:
                for _, row in read_capture(stream):
                    supervisor.step(WorldState.from_telemetry(row))
                    emit(supervisor.last_log)
                    if supervisor.stopped:
                        break
        else:
            # These imports occur only for an explicitly requested read-only PID.
            from ..telemetry import NativeTelemetry

            hotkey = GlobalKillSwitch(arguments.kill_key)
            telemetry = NativeTelemetry(arguments.pid, 100)
            supervisor.native_clock = telemetry.reader.monotonic_ns
            supervisor.kill_switch, supervisor.focus_checker = hotkey, foreground_matches
            while not supervisor.stopped:
                supervisor.step(WorldState.from_telemetry(telemetry.sample(False)))
                emit(supervisor.last_log)
                time.sleep(0.05)
        if not supervisor.stopped:
            supervisor.stop("capture_complete")
        emit(supervisor.report())
        return 0
    except KeyboardInterrupt:
        if supervisor:
            supervisor.stop("keyboard_interrupt")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        if supervisor:
            supervisor.close()
        if telemetry:
            telemetry.close()
        if hotkey:
            hotkey.close()
        if output is not None and output is not sys.stdout:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())
