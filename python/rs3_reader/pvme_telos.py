"""Inspect and run a calibrated, supervised PVME melee Telos keyboard rotation.

Default invocation prints a plan without loading the DLL or opening a device.
Live input requires a matching track-L validation record, explicit bindings and
timing. Phase/font/target/movement checkpoints remain operator observations.
This is not an encounter recognizer, prayer flicker or unattended kill loop.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import re
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from .rotation_keys import (
    KEYS,
    POLL_SECONDS,
    USER_MODIFIERS,
    ArduinoBackend,
    SendInputBackend,
    bind,
    parse_chord,
    windows_library,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT / "profiles" / "pvme_telos.json"
MAX_FRAME_AGE_NS = 150_000_000
START_KEY = KEYS["F8"]
STOP_KEY = KEYS["F12"]
MIN_CLEAN_RUNS = 2
MAX_BINDING_KEYS = 16
MAX_STEPS = 128
MAX_STEP_TICKS = 300
MAX_OFFSET_MS = 60_000
MAX_LATENESS_MS = 150
PHASE_ORDER = ("wars", "p1", "p2", "p3", "p4", "font1", "font2", "font3")
ENRAGE_BANDS = ((300, 999), (1000, 1999), (2000, 4000))
SCHEMA_VERSION = 1


def number(value, name, minimum, maximum, *, integer=False):
    """Validate bounded, finite numbers; JSON booleans are not numeric settings."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
        or (integer and not isinstance(value, int))
    ):
        raise ValueError(
            f"{name} must be {'an integer' if integer else 'a number'} " f"in {minimum}..{maximum}"
        )
    return value


def digest(value):
    """Hash canonical JSON so whitespace edits do not invalidate a calibration."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path):
    """Read a JSON object, rejecting duplicate keys and non-finite literals."""

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Invalid JSON numeric value: {value}")

    result = json.loads(
        Path(path).read_text(encoding="utf-8-sig"),
        object_pairs_hook=unique_object,
        parse_constant=invalid_constant,
    )
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def load_profile(path):
    """Validate the entire recipe, including currently unselected branches."""
    profile = read_json(path)
    if profile.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported rotation profile schema")
    number(profile["tick_seconds"], "tick_seconds", 0.1, 2)
    number(profile["hold_ms"], "hold_ms", 10, 250, integer=True)
    number(profile["max_lateness_ms"], "max_lateness_ms", 1, MAX_LATENESS_MS, integer=True)
    actions = profile["actions"]
    choices = profile["choices"]
    segments = profile["segments"]
    if not all(isinstance(value, dict) for value in (actions, choices, segments)):
        raise ValueError("actions, choices and segments must be objects")
    for action_name, action in actions.items():
        if not isinstance(action, dict) or not isinstance(action.get("label"), str):
            raise ValueError(f"Missing label: {action_name}")
        binding = action.get("binding")
        if binding is None:
            continue
        if not isinstance(binding, list) or not 1 <= len(binding) <= MAX_BINDING_KEYS:
            raise ValueError(f"{action_name}.binding must be a nonempty list or null")
        previous_end = 0
        for press in binding:
            offset = number(press["at_ms"], "binding.at_ms", 0, MAX_OFFSET_MS, integer=True)
            if offset < previous_end:
                raise ValueError(f"Overlapping or unsorted binding: {action_name}")
            previous_end = offset + profile["hold_ms"]
            chord = parse_chord(press["key"])
            if chord[-1] in (START_KEY, STOP_KEY):
                raise ValueError("F8 and F12 are reserved runner controls")
            if KEYS["CTRL"] in chord and chord[-1].name in ("F1", "F2", "F3", "F4"):
                raise ValueError(
                    "The configured prayer bindings are reserved; no prayer automation"
                )
    for name, choice in choices.items():
        options = choice["options"]
        if (
            not isinstance(options, list)
            or not options
            or len(set(options)) != len(options)
            or any(option not in actions for option in options)
            or choice["selected"] not in options
        ):
            raise ValueError(f"Invalid action choice: {name}")
    required_segments = set(PHASE_ORDER) | {"p5_red", "p5_green"}
    if not required_segments.issubset(segments):
        raise ValueError("The recipe must contain Wars, phases 1-5 and all three fonts")
    for name, segment in segments.items():
        if not isinstance(segment.get("checkpoint"), str) or not segment["checkpoint"].strip():
            raise ValueError(f"Missing manual phase checkpoint: {name}")
        steps = segment["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise ValueError(f"Invalid steps: {name}")
        identifiers = set()
        for step in steps:
            if not isinstance(step.get("id"), str) or step["id"] in identifiers:
                raise ValueError(f"Missing or duplicate step id in {name}")
            identifiers.add(step["id"])
            if step.get("after_ticks") is not None:
                number(step["after_ticks"], "after_ticks", 0, MAX_STEP_TICKS, integer=True)
            if "checkpoint" in step and not isinstance(step["checkpoint"], str):
                raise ValueError("Step checkpoint must be text")
            if not isinstance(step["actions"], list) or not step["actions"]:
                raise ValueError(f"Empty step {name}/{step['id']}")
            for entry in step["actions"]:
                action = entry["name"]
                if action not in actions and not (action.startswith("$") and action[1:] in choices):
                    raise ValueError(f"Unknown action: {action}")
                if entry.get("at_ms") is not None:
                    number(entry["at_ms"], "action.at_ms", 0, MAX_OFFSET_MS, integer=True)
    return profile


def resolve_choices(profile, overrides):
    """Resolve explicit guide alternatives without guessing from adrenaline or buffs."""
    selected = {name: choice["selected"] for name, choice in profile["choices"].items()}
    for override in overrides:
        name, separator, action = override.partition("=")
        if (
            not separator
            or name not in selected
            or action not in profile["choices"][name]["options"]
        ):
            raise ValueError(f"Unknown choice {override!r}; use one of the profile's options")
        selected[name] = action
    return selected


def resolve_action(name, choices):
    """Expand a named choice to its selected action."""
    return choices[name[1:]] if name.startswith("$") else name


def plan_digest(profile, segment, choices):
    """Bind calibration to the full profile plus the selected segment and branches."""
    return digest({"profile": profile, "segment": segment, "choices": choices})


def compile_segment(profile, segment_name, choices):
    """Compile key deadlines between manual checkpoints; never invent missing timing."""
    segment = profile["segments"][segment_name]
    chunks = []
    events = []
    checkpoint = segment["checkpoint"]
    tick_ms = profile["tick_seconds"] * 1000
    elapsed_ms = 0
    for step in segment["steps"]:
        if step.get("checkpoint"):
            if events:
                chunks.append((checkpoint, tuple(events)))
                events = []
            checkpoint = step["checkpoint"]
            elapsed_ms = 0
        delay = step.get("after_ticks")
        if delay is None:
            raise ValueError(f"{segment_name}/{step['id']}: after_ticks needs calibration")
        elapsed_ms += delay * tick_ms
        for entry in step["actions"]:
            action_name = resolve_action(entry["name"], choices)
            offset = entry.get("at_ms")
            if offset is None:
                raise ValueError(f"{segment_name}/{step['id']}/{action_name}: at_ms is unset")
            binding = profile["actions"][action_name].get("binding")
            if not binding:
                raise ValueError(f"{action_name}: binding is unset")
            for press in binding:
                deadline = elapsed_ms + offset + press["at_ms"]
                if events and deadline < events[-1][0] + profile["hold_ms"]:
                    raise ValueError(f"Overlapping/unsorted keys in {segment_name}/{step['id']}")
                events.append((deadline, action_name, press["key"], parse_chord(press["key"])))
    if events:
        chunks.append((checkpoint, tuple(events)))
    return tuple(chunks)


def validation_errors(report, profile, segments, choices, backend, enrage, now=None):
    """Check externally recorded live evidence; this runner never grants a ladder pass."""
    now = now or datetime.now(timezone.utc)
    errors = []
    if report.get("schema_version") != SCHEMA_VERSION or report.get("kind") != "pvme_rotation":
        return ["Expected a track-L pvme_rotation validation report (schema_version 1)"]
    if report.get("backend") != backend:
        errors.append("Validation backend does not match")
    if not re.fullmatch(r"[0-9a-f]{64}", str(report.get("client_sha256", ""))):
        errors.append("Validation needs the pinned executable SHA-256")
    if not isinstance(report.get("evidence"), str) or not report["evidence"].strip():
        errors.append("Validation needs a compact evidence/report reference")
    try:
        recorded = datetime.fromisoformat(report["recorded_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(report["expires_at"].replace("Z", "+00:00"))
        if recorded.tzinfo is None or expires.tzinfo is None or not recorded <= now < expires:
            errors.append("Validation is expired, future-dated or lacks a timezone")
    except (KeyError, ValueError, AttributeError, TypeError):
        errors.append("Validation needs recorded_at and expires_at UTC timestamps")
    band = next((bounds for bounds in ENRAGE_BANDS if bounds[0] <= enrage <= bounds[1]), None)
    if not band or report.get("enrage_band") != list(band):
        errors.append("Validation does not cover the selected enrage band")
    passes = report.get("rungs", {})
    if not isinstance(passes, dict):
        return errors + ["rungs must be an object"]
    for rung in range(1, 5):
        result = passes.get(str(rung), {})
        if not isinstance(result, dict):
            result = {}
        count = result.get("clean_runs")
        if (
            result.get("status") != "passed"
            or type(count) is not int
            or count < MIN_CLEAN_RUNS
            or type(result.get("watchdog_trips")) is not int
            or result["watchdog_trips"] != 0
            or type(result.get("event_queue_losses")) is not int
            or result["event_queue_losses"] != 0
        ):
            errors.append(f"Track L rung {rung} has not passed repeated clean live runs")
    hashes = report.get("plans", {})
    for segment in segments:
        if not isinstance(hashes, dict) or hashes.get(segment) != plan_digest(
            profile, segment, choices
        ):
            errors.append(f"No matching calibrated plan hash for {segment}")
    return errors


class LiveGuard:
    """Gate each key against the pinned native session, age, focus and stop key."""

    def __init__(self, reader, expected_hash, evidence_check):
        self.reader, self.expected_hash = reader, expected_hash
        self.evidence_check = evidence_check
        self.identity = None
        self.scene = None
        self.window = None
        library = windows_library("user32")
        self.key_state = bind(library, "GetAsyncKeyState", ctypes.c_int16, [ctypes.c_int])
        self.foreground = bind(library, "GetForegroundWindow", ctypes.c_void_p, [])
        self.window_pid = bind(
            library,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)],
        )
        self.visible = bind(library, "IsWindowVisible", ctypes.c_int, [ctypes.c_void_p])
        self.enabled = bind(library, "IsWindowEnabled", ctypes.c_int, [ctypes.c_void_p])
        self.iconic = bind(library, "IsIconic", ctypes.c_int, [ctypes.c_void_p])

    def check(self, *, arming=False):
        """Reject stale/changed/dead sessions; a checkpoint alone may re-pin the scene."""
        from .native import Status, Variable, decode_text

        if self.key_state(STOP_KEY.virtual_key) < 0:
            raise RuntimeError("F12 emergency stop")
        self.evidence_check()
        self.reader.sample()
        info, player = self.reader.info, self.reader.player
        age = self.reader.monotonic_ns() - info.monotonic_ns
        if (
            info.status not in (Status.AVAILABLE, Status.PARTIAL)
            or not 0 <= age <= MAX_FRAME_AGE_NS
            or player.clock_stalled_ns > MAX_FRAME_AGE_NS
        ):
            raise RuntimeError("Native sample is unavailable, stale or the client clock stalled")
        if decode_text(info.executable_sha256) != self.expected_hash:
            raise RuntimeError("Executable hash differs from live validation")
        identity = (info.pid, info.process_creation_time)
        if not all(identity) or (self.identity is not None and identity != self.identity):
            raise RuntimeError("Game process identity changed")
        self.identity = identity
        if (player.variable(Variable.HEALTH) or 0) <= 0:
            raise RuntimeError("Player health is unknown or zero")
        scene = self.reader.combat.scene_epoch
        if not scene or (not arming and self.scene is not None and scene != self.scene):
            raise RuntimeError("Scene changed or is unknown")
        window = self.foreground()
        owner = ctypes.c_uint32()
        self.window_pid(window, ctypes.byref(owner))
        focused = (
            owner.value == info.pid
            and self.visible(window)
            and self.enabled(window)
            and not self.iconic(window)
        )
        if not arming and (not focused or window != self.window):
            raise RuntimeError("RuneScape lost foreground focus")
        return window if focused else None

    def check_idle_keys(self, chord):
        """Reject user-held modifiers or chord keys instead of interfering with them."""
        self.check()
        keys = set(USER_MODIFIERS) | {key.virtual_key for key in chord}
        if any(self.key_state(key) < 0 for key in keys):
            raise RuntimeError("Release held modifiers and action keys before running")

    def checkpoint(self, message):
        """Require a fresh F8 press/release in the game; held keys do not auto-start."""
        print(
            f"CHECKPOINT: {message}\nConfirm chat/dialogs are closed and the correct bar is active."
            "\nFocus RuneScape; press and release F8. F12 stops.",
            flush=True,
        )
        released = False
        pressed_window = None
        pressed_scene = None
        previous_window = None
        while True:
            window = self.check(arming=True)
            down = self.key_state(START_KEY.virtual_key) < 0
            if not window or window != previous_window:
                released = False
                pressed_window = None
            previous_window = window
            if window and not down:
                if pressed_window == window and pressed_scene == self.reader.combat.scene_epoch:
                    self.window = window
                    self.scene = self.reader.combat.scene_epoch
                    self.check()
                    return
                released = True
                pressed_window = None
            elif window and released:
                pressed_window = window
                pressed_scene = self.reader.combat.scene_epoch
                released = False
            time.sleep(POLL_SECONDS)


def wait_until(deadline, check, *, clock=time.monotonic, sleep=time.sleep):
    """Cooperatively wait using monotonic time, checking cancellation during holds."""
    while True:
        check()
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleep(min(POLL_SECONDS, remaining))


def execute_chunks(chunks, profile, backend, guard, *, clock=time.monotonic, sleep=time.sleep):
    """Dispatch absolute deadlines. Abort late keys rather than bursting to catch up."""
    hold = profile["hold_ms"] / 1000
    lateness = profile["max_lateness_ms"] / 1000
    for checkpoint, events in chunks:
        guard.checkpoint(checkpoint)
        origin = clock()
        for offset_ms, action, key_label, chord in events:
            deadline = origin + offset_ms / 1000
            wait_until(deadline, guard.check, clock=clock, sleep=sleep)
            guard.check_idle_keys(chord)
            if clock() - deadline > lateness:
                raise RuntimeError(
                    f"Missed deadline for {action}; rotation stopped without catch-up"
                )

            def check_dispatch(dispatch_deadline=deadline, action_name=action):
                guard.check()
                if clock() - dispatch_deadline > lateness:
                    raise RuntimeError(f"Dispatch deadline exceeded for {action_name}")

            backend.tap(
                chord,
                hold,
                check_dispatch,
                lambda duration: wait_until(
                    clock() + duration, guard.check, clock=clock, sleep=sleep
                ),
            )
            print(f"INPUT ACCEPTED: {action} [{key_label}]; cast outcome unconfirmed", flush=True)


def print_plan(profile, segments, choices):
    """Show unresolved timing, bindings, checkpoints and every chosen guide action."""
    print(f"Source: {profile['source_url']}\nGuide-derived recipe; no live success is implied.")
    for segment in segments:
        data = profile["segments"][segment]
        print(f"\n{segment}: {data['checkpoint']}")
        print(f"  plan_sha256: {plan_digest(profile, segment, choices)}")
        for step in data["steps"]:
            if step.get("checkpoint"):
                print(f"  CHECKPOINT: {step['checkpoint']}")
            delay = step.get("after_ticks")
            timing = "UNSET timing" if delay is None else f"+{delay} ticks"
            labels = []
            for entry in step["actions"]:
                action = resolve_action(entry["name"], choices)
                binding = profile["actions"][action].get("binding")
                keys = ", ".join(press["key"] for press in binding) if binding else "UNBOUND"
                offset = entry.get("at_ms")
                offset_label = "UNSET" if offset is None else f"{offset}ms"
                labels.append(f"{action} [{keys}; offset={offset_label}]")
            print(f"  {step['id']} ({timing}): {' + '.join(labels)}")
            if step.get("note"):
                print(f"    {step['note']}")


def main(argv=None):
    """CLI: print a dry plan by default; check or run explicitly selected segments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check", "run"), nargs="?", default="plan")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--segment", action="append", help="Repeat for ordered segments; default all"
    )
    parser.add_argument("--beam", choices=("red", "green"), default="red")
    parser.add_argument("--choice", action="append", default=[], metavar="NAME=ACTION")
    parser.add_argument("--backend", choices=("sendinput", "arduino"), default="sendinput")
    parser.add_argument("--port", help="Arduino serial port, for example COM5")
    parser.add_argument("--validation", type=Path, help="Matching track-L live validation JSON")
    parser.add_argument("--enrage", type=int, help="Manually confirmed current enrage (300-4000)")
    parser.add_argument("--pid", type=int, help="Select a specific client process")
    arguments = parser.parse_args(argv)
    try:
        profile = load_profile(arguments.profile)
        choices = resolve_choices(profile, arguments.choice)
        segments = arguments.segment or list(PHASE_ORDER) + [f"p5_{arguments.beam}"]
        if len(set(segments)) != len(segments):
            raise ValueError("Repeated segments are not allowed")
        if {"p5_red", "p5_green"}.issubset(segments):
            raise ValueError("Select one P5 beam route per run")
        for segment in segments:
            if segment not in profile["segments"]:
                raise ValueError(f"Unknown segment {segment!r}: {', '.join(profile['segments'])}")
        if arguments.command == "plan":
            print_plan(profile, segments, choices)
            return 0
        errors = []
        compiled = []
        for segment in segments:
            try:
                compiled.extend(compile_segment(profile, segment, choices))
            except ValueError as error:
                errors.append(str(error))
        if arguments.enrage is None or not 300 <= arguments.enrage <= 4000:
            errors.append("--enrage must specify the observed enrage (300-4000)")
        if arguments.backend == "arduino" and not arguments.port:
            errors.append("--port is required for Arduino")
        elif arguments.backend == "arduino" and not re.fullmatch(
            r"COM[1-9][0-9]*", arguments.port.upper()
        ):
            errors.append("--port must be a Windows COM port such as COM5")
        if not arguments.validation:
            errors.append("--validation is required; track L rungs 1-4 are not yet passed")
            report = {}
        else:
            report = read_json(arguments.validation)
            errors.extend(
                validation_errors(
                    report, profile, segments, choices, arguments.backend, arguments.enrage or 0
                )
            )
        if errors:
            print("Live execution blocked:\n- " + "\n- ".join(errors), file=sys.stderr)
            return 2
        if arguments.command == "check":
            print("Configuration and report match. Runtime focus/session checks still apply.")
            return 0
        # No Reader, COM port or SendInput backend is created before all checks pass.
        from .native import Reader

        def check_evidence():
            faults = validation_errors(
                report, profile, segments, choices, arguments.backend, arguments.enrage
            )
            if faults:
                raise RuntimeError("Live validation no longer applies: " + "; ".join(faults))

        with ExitStack() as resources:
            reader = resources.enter_context(Reader(pid=arguments.pid))
            guard = LiveGuard(reader, report["client_sha256"], check_evidence)
            guard.check(arming=True)
            backend = (
                ArduinoBackend(arguments.port)
                if arguments.backend == "arduino"
                else SendInputBackend()
            )
            resources.callback(backend.close)
            execute_chunks(compiled, profile, backend, guard)
        print("Requested key sequence ended. Confirm the encounter outcome in game.")
        return 0
    except KeyboardInterrupt:
        print("Stopped by Ctrl+C.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"Rotation stopped: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
