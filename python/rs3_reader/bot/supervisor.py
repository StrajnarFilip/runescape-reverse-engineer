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
DEFAULT_KILL_KEY = "Ctrl+Shift+F12"
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


class P1Attempt:
    """Bounded supervised experiment using C observations and the D6 batch writer.

    The user owns movement and encounter entry. The coordinator is created only
    after a dormant Telos is observed, so an outside-to-instance scene change
    cannot pin the wrong encounter. Unknown first-use cooldowns are logged as
    unknown and may be attempted; they never become castable=True or GCD zero.
    Native acknowledgements, not OS acceptance, advance the ordered rotation.
    No prayer timing policy, movement backend or unattended kill mode is enabled.
    """

    def __init__(self, writer, read_label, clock, kill_switch, focus_checker):
        from .rotation import OrderedP1Rotation

        self.template = OrderedP1Rotation()
        self.writer, self.read_label, self.clock = writer, read_label, clock
        self.kill_switch, self.focus_checker = kill_switch, focus_checker
        self.engine = None
        self.locations, self.pending = {}, None
        self.identity, self.previous = None, None
        self.stopped, self.reason = False, "waiting_for_dormant_telos"
        self.sent, self.confirmed = [], []
        self.last_log, self.blocked_since = {}, None
        self.armed_ns = None

    @staticmethod
    def _text(value):
        return value.decode("utf-8", "strict") if isinstance(value, bytes) else value

    def _matches(self, action, label):
        name = self._text(label.name)
        if action["kind"] == "equip":
            return label.item_id == action["item_id"]
        if action.get("role") == "adrenaline_renewal":
            return name == "Adrenaline renewal"
        if action.get("role") == "vulnerability_bomb":
            return label.item_id == action["item_id"] and name == "Vulnerability bomb"
        return name == action.get("name") and label.item_id == -1

    def discover_bindings(self):
        """Find unique current action roles; no input and no raw memory reads in Python."""
        actions = [
            action
            for step in self.template.document["steps"]
            for action in step["actions"]
            if action["kind"] != "target_cycle"
        ]
        found = {action["id"]: [] for action in actions}
        for bar in range(1, 6):
            for slot in range(1, 15):
                try:
                    label = self.read_label(bar, slot)
                except RuntimeError:
                    continue
                for action in actions:
                    if label.flags == 15 and self._matches(action, label):
                        found[action["id"]].append((bar, slot, self._text(label.key_label)))
        missing = [name for name, matches in found.items() if len(matches) != 1]
        if missing:
            raise ValueError("p1_binding_missing_or_ambiguous:" + ",".join(missing))
        self.locations = {name: matches[0] for name, matches in found.items()}
        return dict(self.locations)

    def _fresh(self, observation):
        return observation.is_fresh(150) and observation.reason is None and bool(observation.source)

    def _ability(self, action, world):
        if not self._fresh(world.abilities):
            return None
        entries = [
            entry
            for entry in world.abilities.value
            if entry.get("struct_id") == action.get("ability_id")
        ]
        return entries[0] if len(entries) == 1 else None

    def _item(self, action, world):
        if not self._fresh(world.inventory):
            return None
        matches = []
        for item in world.inventory.value:
            expected = action.get("item_id")
            match = (
                item.get("item_id") == expected
                if expected is not None
                else (
                    isinstance(item.get("name"), str)
                    and item["name"].startswith("Adrenaline renewal potion (")
                )
            )
            if (
                match
                and _integer(item.get("quantity"), 1)
                and _integer(item.get("tracking_token"), 1)
                and item.get("identity_ambiguous") is False
            ):
                matches.append(item)
        return matches[0] if len(matches) == 1 else None

    def _equipped(self, world, slot, item_id):
        return self._fresh(world.equipment) and any(
            item.get("slot") == slot
            and item.get("item_id") == item_id
            and item.get("quantity") == 1
            and item.get("identity_ambiguous") is False
            for item in world.equipment.value.get("slots", ())
        )

    def _ready(self, action, world):
        kind = action["kind"]
        if kind == "target_cycle":
            return True
        if kind in ("item", "equip"):
            return self._item(action, world) is not None
        entry = self._ability(action, world)
        if (
            entry is None
            or entry.get("in_ability_book") is not True
            or entry.get("castable") is False
            or entry.get("equipment_match") != "allowed"
        ):
            return False
        cooldown = entry.get("cooldown") or {}
        remaining, reason = cooldown.get("remaining_ms"), cooldown.get("reason")
        # A missing first-use timer permits an experimental attempt, not a ready claim.
        if reason == "not_set":
            if remaining is not None:
                return False
        elif kind in ("weapon_special", "eof_special") and reason == "timer_mapping_pending":
            pass
        elif reason is not None or not _integer(remaining) or remaining != 0:
            return False
        if not self._fresh(world.player.adrenaline_percent):
            return False
        cost = (entry.get("requirements") or {}).get("adrenaline_raw")
        if not _integer(cost) or world.player.adrenaline_percent.value < cost / 10:
            return False
        if action.get("required_weapon_id") and not self._equipped(
            world, 3, action["required_weapon_id"]
        ):
            return False
        if action.get("required_amulet_id") and not self._equipped(
            world, 2, action["required_amulet_id"]
        ):
            return False
        gcd = world.player.gcd
        return self._fresh(gcd) and (
            (gcd.value.get("reason") is None and gcd.value.get("remaining_ms") == 0)
            or (self.engine.index < 2 and gcd.value.get("reason") == "not_set")
        )

    def prepare(self, action, world):
        """Re-read the C label and exact slot identity immediately before D6 sends."""
        from .actuator_sendinput import NativeKeyAction

        descriptor = NativeKeyAction()
        kind = action["kind"]
        if kind == "target_cycle":
            target = self.engine._encounter_npc(world)
            if target is None:
                raise ValueError("p1_cycle_target_disappeared")
            descriptor.kind, descriptor.virtual_key = 3, ord("A")
            descriptor.expected_instance_id = target.instance_id
            descriptor.expected_config_id = target.config_id
            return descriptor, None
        bar, slot, _ = self.locations[action["id"]]
        label = self.read_label(bar, slot)
        if not self._matches(action, label) or not self._ready(action, world):
            raise ValueError("p1_role_or_readiness_changed:" + action["id"])
        descriptor.virtual_key, descriptor.modifiers = label.virtual_key, label.modifiers
        if kind in ("item", "equip"):
            item = self._item(action, world)
            descriptor.kind, descriptor.slot = 2, item["slot"]
            descriptor.expected_item_id = item["item_id"]
            descriptor.expected_token = item["tracking_token"]
        else:
            descriptor.kind, descriptor.ability_id = 1, action["ability_id"]
        return descriptor, label

    def _acknowledged(self, action, before, world):
        kind = action["kind"]
        if kind == "target_cycle":
            return self._fresh(world.player.target) and (
                world.player.target.value == self.engine._encounter_npc(world)
            )
        if kind == "equip":
            return self._equipped(
                world, {"main_hand": 3, "neck": 2}[action["equipment_slot"]], action["item_id"]
            )
        if kind == "item":
            previous = self._item(action, before)
            if previous is None or not self._fresh(world.inventory):
                return False
            current = [
                item for item in world.inventory.value if item.get("slot") == previous["slot"]
            ]
            if len(current) != 1 or current[0].get("identity_ambiguous") is not False:
                return False
            current = current[0]
            if action["id"] == "vulnerability":
                return (
                    current.get("item_id") == previous["item_id"]
                    and current.get("tracking_token") == previous["tracking_token"]
                    and current.get("quantity") == previous["quantity"] - 1
                )
            # Explicit decoded potion names/IDs are evidence, not inferred family metadata.
            prefix = "Adrenaline renewal potion ("
            old_name, new_name = previous.get("name", ""), current.get("name", "")
            return (
                isinstance(new_name, str)
                and new_name.startswith(prefix)
                and old_name.endswith(")")
                and new_name.endswith(")")
                and old_name[len(prefix) : -1].isdigit()
                and new_name[len(prefix) : -1].isdigit()
                and int(old_name[len(prefix) : -1]) - 1 == int(new_name[len(prefix) : -1])
                and current.get("item_id") != previous["item_id"]
                and current.get("quantity") == previous["quantity"]
            )
        entry, old = self._ability(action, world), self._ability(action, before)
        if entry is None or old is None:
            return False
        cooldown, old_timer = entry.get("cooldown") or {}, old.get("cooldown") or {}
        return (
            cooldown.get("reason") is None
            and _integer(cooldown.get("remaining_ms"), 1)
            and cooldown.get("start_tick") != old_timer.get("start_tick")
        )

    def stop(self, reason):
        """Latch a stop; another encounter requires a new explicitly started process."""
        if not self.stopped:
            self.stopped, self.reason = True, reason
            self.writer.close()

    def _guard(self, world):
        now = self.clock()
        if self.kill_switch():
            return "kill_switch"
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or not _integer(world.observed_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0 <= now - world.observed_ns <= 150 * NS_PER_MS
            or world.raw.get("executable_sha256") != self.template.document["client_sha256"]
        ):
            return "snapshot_identity_or_age_unavailable"
        identity = world.session.pid, world.session.process_creation_time
        if self.identity is not None and identity != self.identity:
            return "process_changed"
        self.identity = identity
        if self.previous and (world.sample_sequence <= self.previous[0] or now < self.previous[1]):
            return "sample_or_clock_regressed"
        self.previous = world.sample_sequence, now
        for name in NATIVE_EVENT_STREAMS:
            stream = getattr(world.events, name)
            if (
                not stream.records.known
                or stream.records.reason is not None
                or type(stream.lost.value) is not int
                or stream.lost.value != 0
                or stream.lost.reason is not None
                or not stream.lost.source
            ):
                return "event_queue_loss_or_unknown:" + name
        if self.engine is not None:
            if world.session != self.engine.session:
                return "encounter_session_changed"
            if self.focus_checker(world) is not True:
                return "window_focus_lost_or_unknown"
            if (
                not self._fresh(world.player.active_prayer)
                or world.player.active_prayer.value != "melee"
            ):
                return "melee_protection_lost_or_unknown"
            hp, maximum = world.player.health.current, world.player.health.maximum
            if not self._fresh(hp) or not self._fresh(maximum) or hp.value <= maximum.value / 2:
                return "health_below_half_or_unknown"
            if self._fresh(world.telos.phase) and world.telos.phase.value != 1:
                return "phase_one_ended"
            if now - self.armed_ns > 60_000 * NS_PER_MS:
                return "armed_time_cap"
        return None

    def step(self, world):
        """Observe once, confirm pending actions, then offer at most one ordered batch."""
        events = []
        if self.stopped:
            return self.last_log
        guard = self._guard(world)
        if guard:
            self.stop(guard)
        elif self.engine is None:
            dormant = [
                npc
                for npc in world.npcs.value or ()
                if self._fresh(npc.config_id) and npc.config_id.value == 22892
            ]
            ready = (
                len(dormant) == 1
                and self.focus_checker(world) is True
                and self._fresh(world.player.active_prayer)
                and world.player.active_prayer.value == "melee"
                and self._fresh(world.player.adrenaline_percent)
                and world.player.adrenaline_percent.value >= 115
                and self._equipped(world, 3, 52061)
                and self._equipped(world, 5, 52063)
                and self._fresh(world.game_tick)
            )
            if ready:
                from .rotation import OrderedP1Rotation

                self.engine, self.armed_ns = OrderedP1Rotation(), self.clock()
                self.engine.session = world.session
                events.append({"event": "armed", "prayer_policy": "hold_melee_no_flicks"})
            else:
                self.reason = "waiting_for_dormant_telos_melee_dual_lengs_115_adrenaline_and_tick"
        if self.engine is not None and not self.stopped:
            if self.pending:
                batch, before, sent_ns = self.pending
                for action in batch.actions:
                    if self._acknowledged(action, before, world) and self.engine.confirm(
                        action["id"],
                        Observation(True, 0, "native_action_specific_transition"),
                        world,
                    ):
                        self.confirmed.append(action["id"])
                        events.append({"event": "confirmed", "action": action["id"]})
                if self.engine.complete or self.engine.current_step["id"] != batch.step_id:
                    self.pending, self.blocked_since = None, None
            if self.engine.complete:
                self.stop("p1_sequence_confirmed")
            else:
                readiness = {
                    action["id"]: Observation(True, 0, "supervised_attempt_permission")
                    for action in self.engine.current_step["actions"]
                    if self._ready(action, world)
                }
                batch = self.engine.step(world, readiness)
                if self.engine.fault:
                    self.stop(self.engine.fault)
                elif batch is not None:
                    try:
                        accepted, sent_ns = self.writer.send(batch, world, self.prepare)
                        self.engine.note_sent(batch, accepted, sent_ns)
                        self.pending = batch, world, sent_ns
                        self.sent.extend(accepted)
                        self.reason = "awaiting_individual_native_confirmations"
                        events.append(
                            {
                                "event": "batch_accepted",
                                "actions": list(accepted),
                                "server_same_tick_confirmed": False,
                            }
                        )
                        self.blocked_since = None
                    except Exception as error:
                        self.stop("dispatch_stopped:" + str(error))
                else:
                    self.reason = ",".join(self.engine.reasons)
                    if (
                        self.pending is None
                        and "waiting_for_native_wake_candidate" not in self.engine.reasons
                    ):
                        if self.blocked_since is None:
                            self.blocked_since = self.clock()
                        elif self.clock() - self.blocked_since > 6000 * NS_PER_MS:
                            self.stop("readiness_timeout:" + self.reason)
        self.last_log = {
            "kind": "supervised_p1",
            "sample_sequence": world.sample_sequence,
            "observed_ns": world.observed_ns,
            "stopped": self.stopped,
            "reason": self.reason,
            "step": (
                self.engine.current_step["id"] if self.engine and not self.engine.complete else None
            ),
            "events": events,
            "sent": list(self.sent),
            "confirmed": list(self.confirmed),
            "adrenaline": world.player.adrenaline_percent.value,
            "health": world.player.health.current.value,
            "phase": world.telos.phase.value,
            "castability_is_fully_known": False,
            "prayer_flicking_enabled": False,
        }
        return self.last_log


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _result_observation(world):
    """Age a validated result envelope in the same way for diagnostics and the supervisor."""
    raw = world.raw.get("encounter_result")
    if (
        not isinstance(raw, Mapping)
        or not _integer(world.now_ns)
        or not _integer(world.observed_ns)
    ):
        return Observation(reason="encounter_result_unavailable")
    age = raw.get("age_ms")
    if type(age) not in (int, float) or not math.isfinite(age) or age < 0:
        return Observation(reason="encounter_result_age_unknown")
    captured_age = _field(world.raw.get("native"), "snapshot_age_ms")
    captured_age = captured_age if type(captured_age) in (int, float) else 0
    age += max(0, (world.now_ns - world.observed_ns) / NS_PER_MS - captured_age)
    result = raw.get("value")
    if (
        not isinstance(result, Mapping)
        or result.get("validated") is not True
        or result.get("kind") not in ("kill", "death")
        or type(result.get("id")) not in (str, int)
        or not result.get("id")
    ):
        return Observation(reason="encounter_result_unvalidated")
    return Observation(result, age, raw.get("source"), raw.get("reason"))


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
    """Translate a stop chord; default is the user's tested Ctrl+Shift+F12."""
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
        observation = _result_observation(world)
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


def readiness_record(world, *, maximum_age_ms=150):
    """Inspect one sample's evidence without constructing controllers or an actuator.

    Availability is not live validation, complete attack coverage or permission
    to dispatch. Empty NPC collections do not validate an active Telos target.
    The output stays tied to this one process/scene/sample, never a union of
    successful fields from unrelated frames or encounters.
    """
    if not isinstance(world, WorldState):
        raise TypeError("Readiness inspection requires WorldState")

    def fresh(value):
        return bool(value.source) and value.reason is None and value.is_fresh(maximum_age_ms)

    checks = {}

    def check(name, available, reason, owner):
        checks[name] = {
            "available": bool(available),
            "reason": None if available else reason,
            "owner": owner,
        }

    digest = world.raw.get("executable_sha256")
    hash_known = (
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )
    age_ms = _field(world.raw.get("native"), "snapshot_age_ms")
    frame = (
        world.session.complete
        and hash_known
        and world.status in ("available", "partial")
        and _integer(world.sample_sequence, 1)
        and _integer(world.now_ns)
        and _integer(world.observed_ns)
        and 0 <= world.now_ns - world.observed_ns <= maximum_age_ms * NS_PER_MS
        and type(age_ms) in (int, float)
        and math.isfinite(age_ms)
        and 0 <= age_ms <= maximum_age_ms
    )
    check("frame_identity_and_age", frame, "snapshot_identity_or_age_unavailable", "S0a/S0b")
    resources = (
        world.player.health.current,
        world.player.health.maximum,
        world.player.prayer.current,
        world.player.prayer.maximum,
        world.player.adrenaline_percent,
    )
    check(
        "player_resources",
        all(
            fresh(value)
            and type(value.value) in (int, float)
            and math.isfinite(value.value)
            and value.value >= 0
            for value in resources
        ),
        "resources_unavailable_or_stale",
        "native",
    )
    check(
        "global_cooldown",
        fresh(world.player.gcd)
        and _field(world.player.gcd.value, "reason") is None
        and _integer(_field(world.player.gcd.value, "remaining_ms")),
        "gcd_unavailable_or_stale",
        "native",
    )
    check(
        "player_tile_and_facing",
        fresh(world.player.tile) and fresh(world.player.facing),
        "pose_unavailable_or_stale",
        "P1",
    )
    check("player_entity_target", fresh(world.player.target), "target_unavailable_or_stale", "P1")
    tick = world.game_tick
    check(
        "received_tick_ordinal",
        fresh(tick) and _integer(_field(tick.value, "index")),
        "received_tick_unavailable_or_stale",
        "P2",
    )
    for name in NATIVE_EVENT_STREAMS:
        batch = getattr(world.events, name)
        check(
            "queue_" + name,
            # Queue batches retain per-event timestamps rather than one shared age.
            # Availability/loss belongs to this root frame; consumers age each event.
            batch.records.known
            and batch.records.reason is None
            and bool(batch.records.source)
            and batch.lost.reason is None
            and bool(batch.lost.source)
            and type(batch.lost.value) is int
            and batch.lost.value == 0,
            "queue_loss_or_unknown",
            "native/S0a",
        )
    for name in ("phase", "enrage", "meter", "freedom"):
        check(
            "telos_" + name, fresh(getattr(world.telos, name)), "encounter_field_not_observed", "P2"
        )
    check("inventory", fresh(world.inventory), "inventory_unavailable_or_stale", "native")
    check("equipment", fresh(world.equipment), "equipment_unavailable_or_stale", "native")
    check("player_effects", fresh(world.player.effects), "effects_unavailable_or_stale", "P3")
    check("action_bar", fresh(world.action_bar), "action_bar_unavailable_or_stale", "P5")

    abilities = world.abilities.value if fresh(world.abilities) else ()
    castability_known = sum(
        isinstance(value, Mapping) and type(value.get("castable")) is bool
        for value in abilities or ()
    )
    check(
        "sampled_ability_castability",
        bool(abilities) and castability_known == len(abilities),
        "full_castability_unmapped_or_not_observed",
        "native/D4",
    )
    npcs = world.npcs.value if fresh(world.npcs) else ()
    active_telos = [
        npc
        for npc in npcs or ()
        if fresh(npc.config_id) and npc.config_id.value == ACTIVE_TELOS_CONFIG
    ]
    target = active_telos[0].target if len(active_telos) == 1 else None
    local_index = _field(world.raw.get("player_hit_splats"), "player_index")
    check(
        "active_telos_local_player_reference",
        target is not None
        and fresh(target)
        and _field(target.value, "kind") == "player"
        and _integer(local_index)
        and _field(target.value, "player_index") == local_index,
        "active_telos_local_target_not_observed",
        "P1",
    )
    check(
        "validated_encounter_result",
        fresh(_result_observation(world)),
        "encounter_result_unmapped_or_not_observed",
        "P2/D7",
    )
    if not frame:
        # Do not let fresh-looking nested envelopes rescue an invalid root frame.
        for value in checks.values():
            value.update(available=False, reason="snapshot_identity_or_age_unavailable")
    return {
        "kind": "readiness",
        "schema_version": 1,
        "session": {
            "pid": world.session.pid,
            "process_creation_time": world.session.process_creation_time,
            "scene_epoch": world.session.scene_epoch,
            "executable_sha256": digest,
        },
        "sample_sequence": world.sample_sequence,
        "checks": checks,
        "unobserved_fields": [name for name, value in checks.items() if not value["available"]],
        "counts": {
            "sampled_abilities": len(abilities or ()),
            "known_castability": castability_known,
            "loaded_npcs": len(npcs or ()),
            "active_telos": len(active_telos),
        },
        "availability_is_live_validation": False,
        "attack_timing_assessed": False,
        "dispatch_enabled": False,
        "input_sent": False,
    }


def _write_readiness(arguments, output):
    """Bounded read-only diagnostic; no producer, hotkey or device is constructed."""

    def emit(world):
        record = readiness_record(world)
        record["inspection_source"] = "capture_replay" if arguments.capture else "live_native"
        output.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")

    if arguments.capture:
        with arguments.capture.open(encoding="utf-8-sig") as stream:
            for index, (_, record) in enumerate(read_capture(stream)):
                emit(WorldState.from_telemetry(record))
                if index + 1 >= arguments.readiness_samples:
                    break
        return
    from ..telemetry import NativeTelemetry

    telemetry = NativeTelemetry(arguments.pid, 100)
    started = time.monotonic()
    try:
        for index in range(arguments.readiness_samples):
            emit(WorldState.from_telemetry(telemetry.sample(False)))
            if time.monotonic() - started >= arguments.max_seconds:
                break
            if index + 1 < arguments.readiness_samples:
                time.sleep(0.05)
    finally:
        telemetry.close()


def _run_p1_attempt(arguments, output):
    """Run one opt-in experiment; all acquisition and balanced input are native C."""
    from ..telemetry import NativeTelemetry
    from .actuator_sendinput import NativeSnapshotDevice
    from .arbiter import P1BatchArbiter

    telemetry = device = hotkey = attempt = None

    def emit(record):
        output.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        output.flush()

    started = time.monotonic()
    try:
        hotkey = GlobalKillSwitch(arguments.kill_key)
        telemetry = NativeTelemetry(arguments.pid, 0)
        device = NativeSnapshotDevice(telemetry.reader)
        writer = P1BatchArbiter(
            device, telemetry.reader.monotonic_ns, hotkey, foreground_matches, enabled=True
        )
        attempt = P1Attempt(
            writer,
            telemetry.reader.read_action_label,
            telemetry.reader.monotonic_ns,
            hotkey,
            foreground_matches,
        )
        telemetry.sample(False)
        bindings = attempt.discover_bindings()
        emit(
            {
                "kind": "p1_preflight",
                "bindings": bindings,
                "input_sent": False,
                "movement_owner": "user",
                "prayer_policy": "hold_melee_no_flicks",
                "mode": "supervised_p1_experiment",
                "ladder_pass": False,
            }
        )
        while not attempt.stopped:
            frame_start = time.monotonic()
            record = telemetry.sample(False)
            world = WorldState.from_telemetry(record, now_ns=telemetry.reader.monotonic_ns())
            line = dict(attempt.step(world))
            # Keep action-specific evidence locally for replay/debugging. No images.
            wanted = {
                a.get("ability_id")
                for s in attempt.template.document["steps"]
                for a in s["actions"]
            }
            record["abilities"] = dict(record.get("abilities") or {})
            record["abilities"]["entries"] = [
                a for a in record["abilities"].get("entries", ()) if a.get("struct_id") in wanted
            ]
            record["action_bar"] = None  # Fresh labels are read separately by the writer.
            line["telemetry"] = record
            emit(line)
            if time.monotonic() - started >= arguments.max_seconds:
                attempt.stop("wall_time_cap")
            time.sleep(max(0, 0.02 - (time.monotonic() - frame_start)))
        emit(
            {
                "kind": "p1_report",
                "reason": attempt.reason,
                "sent": attempt.sent,
                "confirmed": attempt.confirmed,
                "ladder_pass": False,
                "prayer_flicking_enabled": False,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return 0
    finally:
        if attempt:
            attempt.stop("closed")
        if device:
            device.close()
        if telemetry:
            telemetry.close()
        if hotkey:
            hotkey.close()


def main(argv=None):
    """Run bounded shadow capture/live observation; live actuation belongs to L."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mode", choices=("shadow", "assist", "full", "supervised-p1"), default="shadow"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path)
    source.add_argument("--pid", type=int)
    parser.add_argument("--enable", default=",".join(PRODUCER_MODULES))
    parser.add_argument("--max-seconds", type=float, default=1800)
    parser.add_argument("--max-kills", type=int, default=1)
    parser.add_argument("--kill-key", default=DEFAULT_KILL_KEY)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--readiness",
        action="store_true",
        help="Inspect bounded field availability without controllers or input backends",
    )
    parser.add_argument("--readiness-samples", type=int, default=40)
    parser.add_argument(
        "--arbitrate",
        action="store_true",
        help="Include D6 shadow decisions; dispatch remains disabled",
    )
    arguments = parser.parse_args(argv)
    if arguments.mode not in ("shadow", "supervised-p1"):
        parser.error("assist/full require track L authorization; use shadow")
    if arguments.mode == "supervised-p1" and (
        not arguments.pid
        or arguments.readiness
        or arguments.arbitrate
        or arguments.capture
        or arguments.max_seconds > 600
    ):
        parser.error("supervised-p1 requires a live PID and a cap of at most 600 seconds")
    if not 1 <= arguments.readiness_samples <= 10000:
        parser.error("readiness-samples must be 1..10000")
    if not math.isfinite(arguments.max_seconds) or arguments.max_seconds <= 0:
        parser.error("max-seconds must be finite and positive")
    if (
        arguments.capture
        and arguments.output
        and arguments.capture.resolve() == arguments.output.resolve()
    ):
        parser.error("Capture and output must differ")
    output, telemetry, hotkey, supervisor = None, None, None, None
    try:
        if arguments.mode == "supervised-p1":
            output = (
                arguments.output.open("x", encoding="utf-8") if arguments.output else sys.stdout
            )
            return _run_p1_attempt(arguments, output)
        if arguments.readiness:
            output = (
                arguments.output.open("x", encoding="utf-8") if arguments.output else sys.stdout
            )
            _write_readiness(arguments, output)
            return 0
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
