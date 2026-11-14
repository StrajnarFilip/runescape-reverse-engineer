"""A1 input policy and the existing DLL's balanced foreground-key backend.

Nothing is armed at import or construction by default. Clocks are injected in
the native monotonic domain. This module does not make combat decisions or
acknowledge game outcomes. The arbiter is the sole intended caller of send().
"""

from __future__ import annotations

import ctypes
import math
from collections import deque
from dataclasses import dataclass, field
from threading import Condition, RLock
from typing import Mapping, Optional

from .actuator import SendResult
from .intents import ActionLane, ActivateAbility, ConsumeSlot, Interact, Intent, MoveTo, SetPrayer
from .movement import MinimapCalibration, MinimapRegion
from .world_state import SessionIdentity, Tile, WorldState

NS_PER_MS = 1_000_000
CTRL, SHIFT, ALT = 1, 2, 4  # rs3_key_modifiers; these are NOT P5 game modifier codes.
PRAYER_IDS = {"melee": 2, "magic": 3, "ranged": 4, "soul_split": 5, "necromancy": 6}
NATIVE_MINIMAP_STRUCTURE = 12
NATIVE_MINIMAP_SIZE = 64
NATIVE_ENTITY_DEFAULT = 1
MAX_TILE_COORDINATE = 12800


def _pointer_frame(sequence, executable_sha256):
    return (
        type(sequence) is int
        and sequence > 0
        and isinstance(executable_sha256, str)
        and len(executable_sha256) == 64
        and all(character in "0123456789abcdef" for character in executable_sha256)
    )


class InputRejected(RuntimeError):
    """No complete action was accepted; the reason is stable machine-readable text."""


class DeviceFault(InputRejected):
    """Transport/input state is uncertain; stop and require a new backend instance."""


@dataclass(frozen=True)
class KeyBinding:
    virtual_key: int
    modifiers: int = 0
    validated: bool = False
    source: str = ""
    hid_usage: Optional[int] = None

    def __post_init__(self):
        key = self.virtual_key
        if type(key) is not int or not (
            0x30 <= key <= 0x39 or 0x41 <= key <= 0x5A or 0x70 <= key <= 0x7B
        ):
            raise ValueError("Binding requires a Windows A-Z, 0-9 or F1-F12 virtual key")
        if type(self.modifiers) is not int or self.modifiers < 0 or self.modifiers & ~7:
            raise ValueError("Invalid native modifier mask")
        if type(self.validated) is not bool or not isinstance(self.source, str):
            raise ValueError("Binding requires explicit validation provenance")
        if self.validated and not self.source.strip():
            raise ValueError("Validated binding requires a source")
        if self.hid_usage is not None and (
            type(self.hid_usage) is not int or not 4 <= self.hid_usage <= 0x73
        ):
            raise ValueError("HID usage must be a regular supported keyboard key")


@dataclass(frozen=True)
class MinimapBinding:
    """A current A2 transform plus an explicitly validated route for one action.

    The provider re-reads native geometry and validates the path each time it
    is called. A fit alone cannot validate the UI, path or current view. No
    current native provider supplies all these dependencies automatically.
    """

    action: MoveTo
    anchor: Tile
    calibration: MinimapCalibration
    region: MinimapRegion
    sample_sequence: int
    executable_sha256: str
    path_validated: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.action, MoveTo)
            or not isinstance(self.anchor, Tile)
            or not isinstance(self.calibration, MinimapCalibration)
            or not isinstance(self.region, MinimapRegion)
            or not _pointer_frame(self.sample_sequence, self.executable_sha256)
            or type(self.path_validated) is not bool
        ):
            raise ValueError("Minimap binding requires a scoped transform and route evidence")


@dataclass(frozen=True)
class NpcClickBinding:
    """One native physical point whose default action hits the exact NPC.

    ``validated`` asserts a current native hit test, including default-option
    identity, visibility and UI occlusion. A ground projection or raw menu
    candidate is insufficient. The view signature includes window/DPI, camera
    and UI changes. No built-in provider currently establishes these facts.
    """

    action: Interact
    session: SessionIdentity
    sample_sequence: int
    executable_sha256: str
    observed_ns: int
    view_signature: str
    screen_x: int
    screen_y: int
    source: str
    validated: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.action, Interact)
            or self.action.target.kind != "npc"
            or self.action.option not in ("default", "select_target")
            or type(self.action.target.instance_id) is not int
            or not 0 < self.action.target.instance_id < 2**64
            or type(self.action.target.runtime_id) is not int
            or not 0 <= self.action.target.runtime_id <= 65535
            or type(self.action.target.config_id) is not int
            or not 0 <= self.action.target.config_id < 2**31
            or not isinstance(self.session, SessionIdentity)
            or not self.session.complete
            or not _pointer_frame(self.sample_sequence, self.executable_sha256)
            or type(self.observed_ns) is not int
            or self.observed_ns < 0
            or not isinstance(self.view_signature, str)
            or not self.view_signature.strip()
            or not isinstance(self.source, str)
            or not self.source.strip()
            or type(self.validated) is not bool
            or any(
                type(value) is not int or not -(2**31) <= value < 2**31
                for value in (self.screen_x, self.screen_y)
            )
        ):
            raise ValueError("NPC click requires current physical hit-test provenance")


class _NativeMinimap(ctypes.Structure):
    """Private mirror of the existing rs3_minimap ABI; anchor is four scalars."""

    _fields_ = [
        ("anchor_valid", ctypes.c_uint32),
        ("anchor_x", ctypes.c_int32),
        ("anchor_y", ctypes.c_int32),
        ("anchor_plane", ctypes.c_int32),
        ("screen_x", ctypes.c_int32),
        ("screen_y", ctypes.c_int32),
        ("east_dx", ctypes.c_double),
        ("east_dy", ctypes.c_double),
        ("north_dx", ctypes.c_double),
        ("north_dy", ctypes.c_double),
        ("radius_pixels", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class Bindings:
    """Explicit calibrated mappings. Ability keys are catalog struct IDs.

    Consumable keys are (operation string, exact item ID), not potion family
    IDs. Dose changes and moves therefore invalidate an old ConsumeSlot intent.
    HID letter positions must be supplied explicitly; no US-layout assumption.
    """

    prayers: Mapping[str, KeyBinding] = field(default_factory=dict)
    abilities: Mapping[int, KeyBinding] = field(default_factory=dict)
    consumables: Mapping[tuple, KeyBinding] = field(default_factory=dict)

    def __post_init__(self):
        from types import MappingProxyType

        for name in ("prayers", "abilities", "consumables"):
            mapping = dict(getattr(self, name))
            if any(not isinstance(value, KeyBinding) for value in mapping.values()):
                raise TypeError("Every binding must be a KeyBinding")
            object.__setattr__(self, name, MappingProxyType(mapping))

    def resolve(self, action):
        if isinstance(action, SetPrayer):
            binding = self.prayers.get(action.prayer)
        elif isinstance(action, ActivateAbility):
            binding = self.abilities.get(action.ability_id)
        elif isinstance(action, ConsumeSlot):
            binding = self.consumables.get((action.operation.value, action.expected_item_id))
        else:
            raise InputRejected("pointer_mapping_unavailable")
        if binding is None:
            raise InputRejected("binding_unmapped")
        if not binding.validated:
            raise InputRejected("binding_unvalidated")
        return binding


def observed_binding(action, world, *, prayer_struct_ids, now_ns, maximum_age_ms=150):
    """Resolve one current P5 function-key chord, without guessing raw modifier codes.

    Pass the configured prayer-name to struct-ID mapping. Missing, duplicate,
    stale or undecoded rows reject; there is no manual-binding fallback here.
    Potion-family aliases and letter/digit layout calibration remain explicit
    caller configuration, not an inference from matching item names.
    """
    bars = world.action_bar
    if (
        type(now_ns) is not int
        or world.now_ns is None
        or now_ns < world.now_ns
        or not bars.source
        or bars.reason is not None
        or not bars.is_fresh(maximum_age_ms)
        or bars.age_ms + (now_ns - world.now_ns) / NS_PER_MS > maximum_age_ms
    ):
        raise InputRejected("action_bar_unavailable_or_stale")
    if isinstance(action, SetPrayer):
        kind, field_name = "prayer", "ability_struct_id"
        identity = prayer_struct_ids.get(action.prayer)
    elif isinstance(action, ActivateAbility):
        kind, field_name, identity = "ability", "ability_struct_id", action.ability_id
    elif isinstance(action, ConsumeSlot):
        kind, field_name, identity = "item", "item_id", action.expected_item_id
    else:
        raise InputRejected("pointer_mapping_unavailable")
    if type(identity) is not int or identity < 0:
        raise InputRejected("binding_unmapped")
    if any(not isinstance(row, Mapping) for row in bars.value):
        raise InputRejected("binding_unmapped")
    rows = [
        row for row in bars.value if row.get("kind") == kind and row.get(field_name) == identity
    ]
    if len(rows) != 1:
        raise InputRejected("binding_missing_or_ambiguous")
    row = rows[0]
    label = row.get("key")
    if (
        row.get("reason") is not None
        or row.get("binding_reason") is not None
        or not row.get("source")
    ):
        raise InputRejected("binding_unmapped")
    chords = {
        prefix + f"F{number}": (0x6F + number, modifiers)
        for prefix, modifiers in (("", 0), ("Ctrl+", CTRL))
        for number in range(1, 13)
    }
    if not isinstance(label, str) or label not in chords:
        raise InputRejected("binding_key_encoding_unmapped")
    virtual_key, modifiers = chords[label]
    return KeyBinding(virtual_key, modifiers, True, row["source"])


@dataclass(frozen=True)
class InputPolicy:
    """Multiple lanes may share a server tick. No one-action-per-tick rule."""

    maximum_age_ms: int = 150
    maximum_actions_per_tick: int = 12
    maximum_queue: int = 64
    maximum_history: int = 4096
    lane_limits: Mapping[ActionLane, int] = field(
        default_factory=lambda: {
            ActionLane.GCD: 1,
            ActionLane.OFF_GCD: 2,
            ActionLane.FOOD: 1,
            ActionLane.POTION: 1,
            ActionLane.PRAYER: 4,
            ActionLane.POINTER: 3,
        }
    )

    def __post_init__(self):
        from types import MappingProxyType

        for name in (
            "maximum_age_ms",
            "maximum_actions_per_tick",
            "maximum_queue",
            "maximum_history",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(name + " must be a positive integer")
        limits = {ActionLane(key): value for key, value in self.lane_limits.items()}
        if set(limits) != set(ActionLane) or any(
            type(v) is not int or v < 0 for v in limits.values()
        ):
            raise ValueError("Each lane requires a nonnegative integer budget")
        object.__setattr__(self, "lane_limits", MappingProxyType(limits))


class WindowsTargetGuard:
    """Read-only OS focus/process checks. Never raises or changes a window's focus."""

    def __init__(self):
        from ctypes import wintypes

        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        prototypes = {
            "GetForegroundWindow": (wintypes.HWND, []),
            "GetWindowThreadProcessId": (
                wintypes.DWORD,
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
            ),
            "IsWindowVisible": (wintypes.BOOL, [wintypes.HWND]),
            "IsIconic": (wintypes.BOOL, [wintypes.HWND]),
            "IsWindowEnabled": (wintypes.BOOL, [wintypes.HWND]),
            "GetAsyncKeyState": (ctypes.c_short, [ctypes.c_int]),
        }
        for name, (result, args) in prototypes.items():
            function = getattr(self.user, name)
            function.restype, function.argtypes = result, args
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL

    def __call__(self, session, binding=None):
        from ctypes import wintypes

        window = self.user.GetForegroundWindow()
        pid = wintypes.DWORD()
        if not (
            window
            and self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
            and pid.value == session.pid
            and self.user.IsWindowVisible(window)
            and not self.user.IsIconic(window)
            and self.user.IsWindowEnabled(window)
        ):
            return False
        process = self.kernel.OpenProcess(0x1000, False, session.pid)
        if not process:
            return False
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            exit_code = wintypes.DWORD()
            if not self.kernel.GetProcessTimes(process, *(ctypes.byref(item) for item in times)):
                return False
            creation = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
            if (
                creation != session.process_creation_time
                or not self.kernel.GetExitCodeProcess(process, ctypes.byref(exit_code))
                or exit_code.value != 259
            ):
                return False
        finally:
            self.kernel.CloseHandle(process)
        keys = (0x10, 0x11, 0x12, 0x5B, 0x5C)
        if isinstance(binding, KeyBinding):
            keys += (binding.virtual_key,)
        elif isinstance(binding, (MinimapBinding, NpcClickBinding)):
            keys += (0x01, 0x02, 0x04, 0x05, 0x06)  # All mouse buttons.
        if binding is not None and any(self.user.GetAsyncKeyState(key) < 0 for key in keys):
            return False
        return self.user.GetForegroundWindow() == window


class SerializedActuator:
    """Bounded synchronous FIFO; the caller at its head performs the I/O.

    There is no input worker, automatic retry, inferred server tick or game
    acknowledgement. Unknown tick identity rejects input. Queued intents are
    checked AFTER waiting. Close/fault wakes waiters and releases device keys.
    A new process/scene requires a new actuator, not an implicit reattachment.
    """

    def __init__(
        self,
        device,
        *,
        world_provider,
        clock,
        session,
        bindings,
        binding_provider=None,
        guard=None,
        policy=None,
        enabled=False,
    ):
        if not isinstance(session, SessionIdentity) or not session.complete:
            raise ValueError("A complete bound session is required")
        if (
            not callable(clock)
            or not callable(world_provider)
            or not isinstance(bindings, Bindings)
        ):
            raise TypeError("Supply a native clock, immutable world provider and Bindings")
        if type(enabled) is not bool:
            raise TypeError("enabled must be explicit boolean")
        if binding_provider is not None and not callable(binding_provider):
            raise TypeError("binding_provider must be callable")
        self.device, self.world_provider, self.clock = device, world_provider, clock
        self.session, self.bindings = session, bindings
        self.binding_provider = binding_provider
        self.guard, self.policy = (
            guard if guard is not None else WindowsTargetGuard()
        ), policy or InputPolicy()
        self.enabled = enabled
        self._condition, self._io = Condition(), RLock()
        self._queue, self._active = deque(), False
        self._closed, self._fault_reason = False, None
        self._history, self._tick, self._counts = {}, None, {}

    @property
    def fault(self):
        """Expose latched transport/cleanup uncertainty to D6/D8, distinct from rejection."""
        with self._io:
            return self._fault_reason

    def _now(self):
        value = self.clock()
        if type(value) is not int or value < 0:
            raise DeviceFault("invalid_native_clock")
        return value

    def _validate(self, intent, *, binding=None):
        now = self._now()
        if self._closed:
            raise InputRejected("closed")
        if self._fault_reason:
            raise InputRejected("faulted:" + self._fault_reason)
        if not self.enabled:
            raise InputRejected("disabled")
        if now < intent.issued_ns:
            raise InputRejected("before_issue_time")
        if intent.expired(now):
            raise InputRejected("deadline_expired")
        maximum_ns = self.policy.maximum_age_ms * NS_PER_MS
        if intent.observed_ns is None or not 0 <= now - intent.observed_ns <= maximum_ns:
            raise InputRejected("stale_intent_snapshot")
        world = self.world_provider()
        if not isinstance(world, WorldState):
            raise DeviceFault("invalid_world_provider")
        if world.session != self.session or intent.session != self.session:
            raise DeviceFault("session_changed")
        if (
            world.status not in ("available", "partial")
            or world.observed_ns is None
            or not (0 <= now - world.observed_ns <= maximum_ns)
            or world.now_ns is None
            or world.now_ns > now
        ):
            raise InputRejected("stale_world")
        if (
            intent.sample_sequence is None
            or world.sample_sequence is None
            or (world.sample_sequence < intent.sample_sequence)
        ):
            raise InputRejected("sample_sequence_unavailable_or_regressed")
        elapsed_ms = (now - intent.issued_ns) / NS_PER_MS
        for evidence in intent.evidence:
            observation = evidence.observation
            if (
                observation.reason is not None
                or not observation.source
                or not observation.is_fresh(self.policy.maximum_age_ms)
                or (observation.age_ms + elapsed_ms > self.policy.maximum_age_ms)
            ):
                raise InputRejected("stale_or_unknown_evidence")
        if self.guard(self.session, binding) is not True:
            raise InputRejected("foreground_or_user_key_mismatch")
        return world

    def _fresh(self, observation, world, now):
        return (
            observation.reason is None
            and observation.source
            and observation.is_fresh(self.policy.maximum_age_ms)
            and (
                observation.age_ms + (now - world.now_ns) / NS_PER_MS <= self.policy.maximum_age_ms
            )
        )

    def _resolve_binding(self, action, world):
        binding = (
            self.bindings.resolve(action)
            if self.binding_provider is None
            else self.binding_provider(action, world)
        )
        if isinstance(action, (MoveTo, Interact)):
            self._pointer_preconditions(action, binding, world)
            return binding
        if not isinstance(binding, KeyBinding) or not binding.validated:
            raise InputRejected("binding_unvalidated")
        return binding

    def _pointer_preconditions(self, action, binding, world):
        if not isinstance(binding, (MinimapBinding, NpcClickBinding)):
            raise InputRejected("pointer_mapping_unavailable")
        if (
            binding.action != action
            or binding.sample_sequence != world.sample_sequence
            or binding.executable_sha256 != world.raw.get("executable_sha256")
        ):
            raise InputRejected("pointer_binding_frame_mismatch")
        now = self._now()
        if isinstance(binding, MinimapBinding):
            region = binding.region
            if region.validated is not True or binding.path_validated is not True:
                raise InputRejected("pointer_geometry_or_path_unvalidated")
            if (
                any(not -(2**31) <= value < 2**31 for value in (region.center_x, region.center_y))
                or not 0 < region.radius < 2**32
            ):
                raise InputRejected("pointer_geometry_out_of_range")
            if not self._fresh(world.player.tile, world, now):
                raise InputRejected("pointer_player_tile_unavailable")
            if (
                binding.anchor != world.player.tile.value
                or binding.anchor == action.tile
                or any(
                    coordinate >= MAX_TILE_COORDINATE
                    for tile in (binding.anchor, action.tile)
                    for coordinate in (tile.x, tile.y)
                )
                or binding.calibration.project(binding.anchor, action.tile, region) is None
            ):
                raise InputRejected("pointer_minimap_or_anchor_changed")
            session, observed_ns = region.session, region.observed_ns
        else:
            if binding.validated is not True:
                raise InputRejected("pointer_hit_test_unvalidated")
            target = action.target
            if (
                type(target.instance_id) is not int
                or not 0 < target.instance_id < 2**64
                or type(target.runtime_id) is not int
                or not 0 <= target.runtime_id <= 65535
                or type(target.config_id) is not int
                or not 0 <= target.config_id < 2**31
                or not self._fresh(world.npcs, world, now)
                or not self._fresh(world.player.target, world, now)
                or not self._fresh(world.player.tile, world, now)
            ):
                raise InputRejected("pointer_npc_identity_unavailable")
            matches = [
                npc
                for npc in world.npcs.value
                if all(
                    self._fresh(observation, world, now)
                    and type(observation.value) is int
                    and observation.value == expected
                    for observation, expected in (
                        (npc.instance_id, target.instance_id),
                        (npc.runtime_id, target.runtime_id),
                        (npc.config_id, target.config_id),
                    )
                )
                and self._fresh(npc.tile, world, now)
                and npc.tile.value.plane == world.player.tile.value.plane
            ]
            if len(matches) != 1 or world.player.target.value == target:
                raise InputRejected("pointer_npc_changed_or_already_targeted")
            session, observed_ns = binding.session, binding.observed_ns
        if session != world.session or not (
            0 <= now - observed_ns <= self.policy.maximum_age_ms * NS_PER_MS
        ):
            raise InputRejected("pointer_binding_stale_or_foreign")

    def _preconditions(self, action, world):
        now = self._now()
        if isinstance(action, SetPrayer):
            active = world.player.active_prayer
            points = world.player.prayer.current
            if action.prayer not in PRAYER_IDS:
                raise InputRejected("prayer_none_unmapped")
            if not self._fresh(active, world, now) or not self._fresh(points, world, now):
                raise InputRejected("prayer_state_unknown")
            if active.value not in ("none", *PRAYER_IDS):
                raise InputRejected("prayer_state_unknown")
            if active.value == action.prayer:
                raise InputRejected("already_active")
            if (
                type(points.value) not in (int, float)
                or not math.isfinite(points.value)
                or points.value <= 0
            ):
                raise InputRejected("no_prayer_points")
        elif isinstance(action, ActivateAbility):
            if not self._fresh(world.abilities, world, now) or not any(
                entry.get("struct_id") == action.ability_id for entry in world.abilities.value
            ):
                raise InputRejected("ability_not_observed")
        elif isinstance(action, ConsumeSlot):
            if not self._fresh(world.inventory, world, now):
                raise InputRejected("inventory_unknown")
            matches = [entry for entry in world.inventory.value if entry.get("slot") == action.slot]
            if (
                len(matches) != 1
                or matches[0].get("item_id") != action.expected_item_id
                or (
                    action.expected_tracking_token is not None
                    and matches[0].get("tracking_token") != action.expected_tracking_token
                )
            ):
                raise InputRejected("inventory_precondition_changed")

    def _charge(self, intent, world):
        now = self._now()
        tick = world.game_tick
        if not self._fresh(tick, world, now) or tick.reason is not None:
            raise InputRejected("server_tick_unknown")
        index = tick.value.get("index") if isinstance(tick.value, Mapping) else None
        if type(index) is not int or index < 0:
            raise InputRejected("server_tick_unknown")
        if self._tick is not None and index < self._tick:
            raise DeviceFault("server_tick_regressed")
        if index != self._tick:
            self._tick, self._counts = index, {}
        if sum(self._counts.values()) >= self.policy.maximum_actions_per_tick or (
            self._counts.get(intent.lane, 0) >= self.policy.lane_limits[intent.lane]
        ):
            raise InputRejected("tick_budget_exhausted")
        self._history = {
            key: deadline for key, deadline in self._history.items() if deadline >= now
        }
        if intent.intent_id in self._history:
            raise InputRejected("duplicate_intent")
        if len(self._history) >= self.policy.maximum_history:
            raise InputRejected("intent_history_full")
        self._history[intent.intent_id] = intent.deadline_ns
        self._counts[intent.lane] = self._counts.get(intent.lane, 0) + 1

    def send(self, intent):
        if not isinstance(intent, Intent):
            raise TypeError("send requires an Intent")
        ticket = object()
        with self._condition:
            if self._closed or self._fault_reason:
                return SendResult(intent.intent_id, False, self._now(), "closed_or_faulted")
            if len(self._queue) >= self.policy.maximum_queue:
                return SendResult(intent.intent_id, False, self._now(), "queue_full")
            self._queue.append(ticket)
            while self._queue[0] is not ticket or self._active:
                if self._closed or self._fault_reason:
                    self._queue.remove(ticket)
                    return SendResult(intent.intent_id, False, self._now(), "closed_or_faulted")
                self._condition.wait(0.05)
            self._queue.popleft()
            self._active = True
        accepted, reason, sent_ns = False, None, 0
        try:
            with self._io:
                world = self._validate(intent)
                binding = self._resolve_binding(intent.action, world)
                self._preconditions(intent.action, world)
                world = self._validate(intent, binding=binding)
                if self._resolve_binding(intent.action, world) != binding:
                    raise InputRejected("binding_changed_during_dispatch")
                self._preconditions(intent.action, world)
                self._supported(intent.action, binding)
                self._charge(intent, world)
                sent_ns = self._now()
                self._dispatch(intent, binding, world)
                accepted = True
        except Exception as error:
            reason = str(error) if isinstance(error, InputRejected) else "unexpected_device_fault"
            with self._io:
                try:
                    self.device.release_all()
                except Exception:
                    reason = "release_failed_input_state_unknown"
                    self._fault_reason = reason
                if isinstance(error, DeviceFault) or not isinstance(error, InputRejected):
                    self._fault_reason = reason
            if not sent_ns:
                try:
                    sent_ns = self._now()
                except DeviceFault:
                    sent_ns = intent.issued_ns
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
        return SendResult(intent.intent_id, accepted, sent_ns, reason)

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        with self._io:
            try:
                self.device.release_all()
            finally:
                self.device.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.close()


class NativeSnapshotDevice:
    """Borrow a live Reader context; own a separate copied snapshot.

    Close the actuator before closing its Reader. DLL chords are balanced and
    partial-insertion releases are owned by src/input.c, not by Python. This
    adapter never emits a global key-up that could release a user's own keys.
    As with Reader.sample(), the caller serializes consumer refresh and dispatch.
    The native worker may run independently: it never mutates Reader.snapshot.
    """

    def __init__(self, reader):
        from ..native import CombatState, Info

        self.reader, self.library = reader, reader.library
        self.snapshot = ctypes.c_void_p()
        self.info, self.combat = Info(), CombatState()
        self.snapshot_copy = getattr(self.library, "rs3_snapshot_copy", None)
        if self.snapshot_copy is not None:
            self.snapshot_copy.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.snapshot_copy.restype = ctypes.c_int32
        function = self.library.rs3_activate_ability
        function.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        function.restype = ctypes.c_int32
        self.ability_chord = getattr(self.library, "rs3_activate_ability_chord", None)
        if self.ability_chord is not None:
            self.ability_chord.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 3
            self.ability_chord.restype = ctypes.c_int32
        self.item_chord = getattr(self.library, "rs3_activate_item_chord", None)
        if self.item_chord is not None:
            self.item_chord.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_int32,
                ctypes.c_uint64,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            self.item_chord.restype = ctypes.c_int32
        self.move_to_tile = getattr(self.library, "rs3_move_to_tile", None)
        self.interact = getattr(self.library, "rs3_interact", None)
        if self.move_to_tile is not None:
            self.library.rs3_sizeof.argtypes = [ctypes.c_uint32]
            self.library.rs3_sizeof.restype = ctypes.c_uint32
            size = self.library.rs3_sizeof(NATIVE_MINIMAP_STRUCTURE)
            if size != ctypes.sizeof(_NativeMinimap) or size != NATIVE_MINIMAP_SIZE:
                raise DeviceFault("native_minimap_layout_mismatch")
            self.move_to_tile.argtypes = (
                [ctypes.c_void_p] + [ctypes.c_int32] * 3 + [ctypes.POINTER(_NativeMinimap)]
            )
            self.move_to_tile.restype = ctypes.c_int32
        if self.interact is not None:
            self.interact.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint64,
                ctypes.c_uint32,
                ctypes.c_int32,
                ctypes.c_int32,
            ]
            self.interact.restype = ctypes.c_int32
        reader.check_result(self.library.rs3_snapshot_create(ctypes.byref(self.snapshot)))

    def supports(self, action, binding):
        """Reject unavailable old-DLL operations before spending a dispatch budget."""
        if isinstance(action, MoveTo):
            if self.move_to_tile is None:
                raise InputRejected("native_movement_api_unavailable")
            if (
                not isinstance(binding, MinimapBinding)
                or binding.action != action
                or binding.path_validated is not True
                or binding.region.validated is not True
            ):
                raise InputRejected("pointer_geometry_or_path_unvalidated")
        elif isinstance(action, Interact):
            if self.interact is None:
                raise InputRejected("native_interaction_api_unavailable")
            if (
                not isinstance(binding, NpcClickBinding)
                or binding.action != action
                or binding.validated is not True
            ):
                raise InputRejected("pointer_hit_test_unvalidated")
        elif isinstance(action, ConsumeSlot):
            if self.item_chord is None:
                raise InputRejected("native_item_key_api_unmapped")
            if (
                type(action.expected_tracking_token) is not int
                or action.expected_tracking_token <= 0
            ):
                raise InputRejected("native_item_tracking_token_required")
        elif isinstance(action, ActivateAbility):
            if binding.modifiers and self.ability_chord is None:
                raise InputRejected("native_modified_ability_api_unmapped")
        elif not isinstance(action, SetPrayer):
            raise InputRejected("pointer_mapping_unavailable")

    def dispatch(self, action, binding, world):
        self.supports(action, binding)
        lib, snapshot = self.library, self.snapshot
        if not snapshot or not self.reader.context:
            raise DeviceFault("native_device_closed")
        if self.snapshot_copy is not None:
            source = getattr(self.reader, "snapshot", None)
            if not source:
                raise DeviceFault("native_source_snapshot_unavailable")
            acquire = self.snapshot_copy
        else:
            # Older DLLs can copy only the worker's latest frame; exact metadata below
            # still rejects if it has advanced beyond the WorldState being checked.
            source, acquire = self.reader.context, lib.rs3_copy_latest
        for function, arguments in (
            (acquire, (source, snapshot)),
            (lib.rs3_snapshot_info, (snapshot, ctypes.byref(self.info), ctypes.sizeof(self.info))),
            (
                lib.rs3_snapshot_combat,
                (snapshot, ctypes.byref(self.combat), ctypes.sizeof(self.combat)),
            ),
        ):
            result = function(*arguments)
            if result != 0:
                raise DeviceFault("native_snapshot_failed")
        identity = SessionIdentity(
            self.info.pid, self.info.process_creation_time, self.combat.scene_epoch
        )
        if identity != world.session or self.info.sequence < world.sample_sequence:
            raise DeviceFault("native_snapshot_identity_changed")
        from ..native import decode_text

        expected_hash = world.raw.get("executable_sha256")
        if not expected_hash or decode_text(self.info.executable_sha256) != expected_hash:
            raise DeviceFault("native_snapshot_executable_changed")
        if self.info.sequence != world.sample_sequence:
            # Re-evaluate against a newer WorldState, including its bindings and inventory.
            raise InputRejected("native_snapshot_advanced")
        if isinstance(action, SetPrayer):
            result = lib.rs3_set_prayer(
                snapshot, PRAYER_IDS[action.prayer], binding.virtual_key, binding.modifiers
            )
        elif isinstance(action, MoveTo):
            origin, region, calibration = binding.anchor, binding.region, binding.calibration
            minimap = _NativeMinimap(
                1,
                origin.x,
                origin.y,
                origin.plane,
                region.center_x,
                region.center_y,
                calibration.east_dx,
                calibration.east_dy,
                calibration.north_dx,
                calibration.north_dy,
                region.radius,
                0,
            )
            result = self.move_to_tile(
                snapshot, action.tile.x, action.tile.y, action.tile.plane, ctypes.byref(minimap)
            )
        elif isinstance(action, Interact):
            result = self.interact(
                snapshot,
                action.target.instance_id,
                NATIVE_ENTITY_DEFAULT,
                binding.screen_x,
                binding.screen_y,
            )
        elif isinstance(action, ConsumeSlot):
            result = self.item_chord(
                snapshot,
                action.slot,
                action.expected_item_id,
                action.expected_tracking_token,
                binding.virtual_key,
                binding.modifiers,
            )
        elif self.ability_chord is not None:
            result = self.ability_chord(
                snapshot, action.ability_id, binding.virtual_key, binding.modifiers
            )
        else:
            result = lib.rs3_activate_ability(snapshot, action.ability_id, binding.virtual_key)
        if result in (-5, -7):  # RS3_E_STATE / RS3_E_TARGET: no chord was inserted.
            raise InputRejected("native_rejected:" + str(result))
        if result != 0:
            raise DeviceFault("native_input_fault:" + str(result))

    def release_all(self):
        """The DLL owns and releases every key of each balanced native call."""

    def close(self):
        if self.snapshot:
            self.library.rs3_snapshot_destroy(self.snapshot)
            self.snapshot = ctypes.c_void_p()


class SendInputActuator(SerializedActuator):
    """Use balanced native chords, with explicit capability rejection for older DLLs."""

    def _supported(self, action, binding):
        supports = getattr(self.device, "supports", None)
        if callable(supports):
            supports(action, binding)
            return
        if isinstance(action, (MoveTo, Interact)):
            raise InputRejected("native_pointer_api_unavailable")
        if isinstance(action, ConsumeSlot):
            raise InputRejected("native_item_key_api_unmapped")
        if isinstance(action, ActivateAbility) and binding.modifiers:
            raise InputRejected("native_modified_ability_api_unmapped")

    def _dispatch(self, intent, binding, world):
        self.device.dispatch(intent.action, binding, world)
