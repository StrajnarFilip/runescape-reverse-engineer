"""Both A1 backends are exercised without loading an input DLL or opening a port."""

import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from rs3_reader.bot.actuator_sendinput import (
    CTRL,
    Bindings,
    DeviceFault,
    InputPolicy,
    InputRejected,
    KeyBinding,
    MinimapBinding,
    NativeSnapshotDevice,
    NpcClickBinding,
    SendInputActuator,
    observed_binding,
)
from rs3_reader.bot.actuator_serial import SerialActuator, SerialKeyboardDevice
from rs3_reader.bot.intents import (
    ActionLane,
    ActivateAbility,
    ConsumeSlot,
    Evidence,
    Interact,
    Intent,
    MoveTo,
    Priority,
    SetPrayer,
)
from rs3_reader.bot.movement import MinimapCalibration, MinimapRegion
from rs3_reader.bot.world_state import EntityRef, Observation, SessionIdentity, Tile, WorldState

FIXTURE = Path(__file__).parent / "fixtures/bot/synthetic_capture.jsonl"
MS = 1_000_000


def minimap_binding(world):
    region = MinimapRegion(
        world.session, world.now_ns, "synthetic_view", -500, 200, 100, "fixture", True
    )
    return MinimapBinding(
        MoveTo(Tile(3202, 3202, 0)),
        world.player.tile.value,
        MinimapCalibration(region, 0, 4, 0, 0, -4, 0, 6),
        region,
        world.sample_sequence,
        world.raw["executable_sha256"],
        True,
    )


def npc_binding(world):
    return NpcClickBinding(
        Interact(EntityRef("npc", 99, 7, 42), "select_target"),
        world.session,
        world.sample_sequence,
        world.raw["executable_sha256"],
        world.now_ns,
        "synthetic_view",
        -490,
        204,
        "synthetic_exact_default_hit_test",
        True,
    )


def pointer_world(world):
    npc = replace(world.npcs.value[0], tile=Observation(Tile(3203, 3202, 0), 0, "fixture"))
    return replace(
        world,
        npcs=Observation((npc,), 0, "fixture"),
        player=replace(world.player, target=Observation(EntityRef("none"), 0, "fixture")),
    )


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class FakeDLL:
    """Model the DLL's balanced atomic chord, with an injectable OS fault."""

    def __init__(self):
        self.actions, self.releases, self.closes = [], 0, 0
        self.fail, self.hook = False, None
        self.chords_supported = False
        self.pointers_supported = False

    def supports(self, action, binding):
        if isinstance(action, (MoveTo, Interact)) and not self.pointers_supported:
            raise InputRejected("native_pointer_api_unavailable")
        if isinstance(action, ConsumeSlot):
            if not self.chords_supported:
                raise InputRejected("native_item_key_api_unmapped")
            if not action.expected_tracking_token:
                raise InputRejected("native_item_tracking_token_required")
        if isinstance(action, ActivateAbility) and binding.modifiers and not self.chords_supported:
            raise InputRejected("native_modified_ability_api_unmapped")

    def dispatch(self, action, binding, world):
        if self.hook:
            self.hook()
        if self.fail:
            raise DeviceFault("fake_native_os_failure")
        self.actions.append((action, binding))

    def release_all(self):
        self.releases += 1

    def close(self):
        self.closes += 1


class FakeChannel:
    """Byte-level serial fake: fragmented writes/replies and real held-key state."""

    def __init__(self):
        self.input, self.output = bytearray(), bytearray()
        self.commands, self.taps, self.held = [], [], set()
        self.closed, self.fail_tap, self.no_reply, self.hook = False, False, False, None
        self.io_now, self.maximum_write = 0.0, 999
        self.greeting = b"RS3_KEYBOARD 1"

    def write(self, data):
        if self.closed:
            raise OSError("closed fake")
        data = data[: self.maximum_write]
        self.input.extend(data)
        while b"\n" in self.input:
            line, _, tail = self.input.partition(b"\n")
            self.input = bytearray(tail)
            command = bytes(line)
            self.commands.append(command)
            response = b"OK"
            if command == b"HELLO":
                response = self.greeting
            elif command == b"RELEASE_ALL":
                self.held.clear()
            elif command.startswith(b"PRESS "):
                self.held.add(int(command[6:], 16))
            elif command.startswith(b"TAP "):
                if self.hook:
                    self.hook()
                if self.no_reply:
                    continue
                if self.fail_tap:
                    response = b"ERR KEY"
                else:
                    self.taps.append(int(command[4:], 16))
            if not self.no_reply:
                self.output.extend(response + b"\r\n")
        return len(data)

    def read(self, size):
        if not self.output:
            self.io_now += 0.1
            return b""
        chunk = bytes(self.output[:size])
        del self.output[:size]
        return chunk

    def close(self):
        self.closed = True
        self.held.clear()  # Sketch DTR-low / watchdog release.


class BackendTests(unittest.TestCase):
    def world(self):
        row = json.loads(FIXTURE.read_text().splitlines()[0])
        row["executable_sha256"] = "a" * 64
        row["game_tick"] = {"value": {"index": 20}, "source": "synthetic_fixture", "age_ms": 5}
        row["inventory"]["slots"] = [
            {"slot": 0, "item_id": 200, "tracking_token": 50},
            {"slot": 1, "item_id": 201, "tracking_token": 51},
        ]
        row["abilities"]["entries"].append({"struct_id": 104})
        return WorldState.from_telemetry(row)

    def make_backend(self, kind, **options):
        world = self.world()
        state = [world]
        clock = FakeClock(world.now_ns)
        bindings = Bindings(
            prayers={
                name: KeyBinding(key, CTRL, True, "fixture calibration")
                for name, key in (("magic", 0x70), ("soul_split", 0x72), ("melee", 0x73))
            },
            abilities={
                103: KeyBinding(0x74, 0, True, "fixture"),
                104: KeyBinding(0x75, 0, True, "fixture"),
            },
            consumables={
                ("eat", 200): KeyBinding(0x76, 0, True, "fixture"),
                ("drink", 201): KeyBinding(0x77, 0, True, "fixture"),
            },
        )
        if kind == "native":
            device, channel, cls = FakeDLL(), None, SendInputActuator
        else:
            channel = FakeChannel()
            device = SerialKeyboardDevice(channel, io_clock=lambda: channel.io_now)
            cls = SerialActuator
        backend = cls(
            device,
            world_provider=lambda: state[0],
            clock=clock,
            session=world.session,
            bindings=bindings,
            guard=options.pop("guard", lambda *args: True),
            enabled=options.pop("enabled", True),
            **options,
        )
        self.addCleanup(backend.close)
        return backend, state, clock, device, channel

    def intent(self, state, clock, ident="one", action=None, lane=ActionLane.PRAYER):
        world = state[0]
        return Intent(
            ident,
            "fixture",
            action or SetPrayer("magic"),
            Priority.PRAYER,
            lane,
            clock(),
            clock() + 100 * MS,
            world.session,
            world.sample_sequence,
            (Evidence("health", world.player.health.current),),
            world.observed_ns,
        )

    def test_pointer_requires_explicit_provider_and_serial_rejects_without_fault(self):
        for kind in ("native", "serial"):
            backend, state, clock, device, channel = self.make_backend(kind)
            state[0] = pointer_world(state[0])
            binding = minimap_binding(state[0])
            intent = self.intent(state, clock, action=binding.action, lane=ActionLane.POINTER)
            self.assertEqual(backend.send(intent).reason, "pointer_mapping_unavailable")
            backend.binding_provider = lambda *args: binding
            if kind == "serial":
                self.assertEqual(backend.send(intent).reason, "serial_pointer_unsupported")
                self.assertFalse(channel.taps)
                self.assertIsNone(backend.fault)
            else:
                self.assertEqual(backend.send(intent).reason, "native_pointer_api_unavailable")
                self.assertFalse(device.actions)

    def test_native_accepts_scoped_pointer_and_prayer_in_the_same_tick(self):
        backend, state, clock, device, _ = self.make_backend("native")
        state[0] = pointer_world(state[0])
        binding = [minimap_binding(state[0])]
        backend.binding_provider = lambda action, world: (
            binding[0]
            if isinstance(action, (MoveTo, Interact))
            else backend.bindings.resolve(action)
        )
        device.pointers_supported = True
        for index, value in enumerate((binding[0], npc_binding(state[0]))):
            binding[0] = value
            self.assertTrue(
                backend.send(
                    self.intent(state, clock, str(index), value.action, ActionLane.POINTER)
                ).accepted
            )
        self.assertTrue(backend.send(self.intent(state, clock, "prayer")).accepted)
        self.assertEqual(len(device.actions), 3)

    def test_pointer_frame_geometry_path_and_target_preconditions(self):
        backend, state, clock, device, _ = self.make_backend("native")
        state[0] = pointer_world(state[0])
        device.pointers_supported = True
        move = minimap_binding(state[0])
        click = npc_binding(state[0])
        cases = (
            (replace(move, path_validated=False), "pointer_geometry_or_path_unvalidated"),
            (
                replace(move, region=replace(move.region, validated=False)),
                "pointer_geometry_or_path_unvalidated",
            ),
            (replace(move, sample_sequence=2), "pointer_binding_frame_mismatch"),
            (replace(move, executable_sha256="b" * 64), "pointer_binding_frame_mismatch"),
            (replace(move, anchor=Tile(3201, 3201, 0)), "pointer_minimap_or_anchor_changed"),
            (
                replace(move, region=replace(move.region, view_signature="changed")),
                "pointer_minimap_or_anchor_changed",
            ),
            (
                replace(move, region=replace(move.region, observed_ns=clock() - 151 * MS)),
                "pointer_binding_stale_or_foreign",
            ),
            (replace(click, validated=False), "pointer_hit_test_unvalidated"),
            (replace(click, observed_ns=clock() + 1), "pointer_binding_stale_or_foreign"),
            (
                replace(click, session=SessionIdentity(111, 999, 3)),
                "pointer_binding_stale_or_foreign",
            ),
            (
                replace(click, action=Interact(EntityRef("npc", 99, 7, 43), "default")),
                "pointer_npc_changed_or_already_targeted",
            ),
        )
        for binding, reason in cases:
            with self.subTest(reason=reason, binding=binding):
                backend.binding_provider = lambda *args: binding
                result = backend.send(
                    self.intent(state, clock, action=binding.action, lane=ActionLane.POINTER)
                )
                self.assertEqual(result.reason, reason)
                self.assertIsNone(backend.fault)
        self.assertFalse(device.actions)

    def test_changed_pointer_before_dispatch_rejects_and_recycled_npc_does_not_match(self):
        backend, state, clock, device, _ = self.make_backend("native")
        state[0] = pointer_world(state[0])
        device.pointers_supported = True
        click = npc_binding(state[0])
        bindings = iter((click, replace(click, screen_x=click.screen_x + 1)))
        backend.binding_provider = lambda *args: next(bindings)
        intent = self.intent(state, clock, action=click.action, lane=ActionLane.POINTER)
        self.assertEqual(backend.send(intent).reason, "binding_changed_during_dispatch")
        backend.binding_provider = lambda *args: click
        npc = replace(state[0].npcs.value[0], instance_id=Observation(100, 0, "fixture"))
        state[0] = replace(state[0], npcs=Observation((npc,), 0, "fixture"))
        self.assertEqual(backend.send(intent).reason, "pointer_npc_changed_or_already_targeted")
        self.assertFalse(device.actions)

    def test_pointer_rejects_unknown_or_duplicate_npc_and_already_targeted(self):
        backend, state, clock, device, _ = self.make_backend("native")
        original = pointer_world(state[0])
        click = npc_binding(original)
        backend.binding_provider = lambda *args: click
        device.pointers_supported = True
        cases = (
            (replace(original, npcs=Observation()), "pointer_npc_identity_unavailable"),
            (
                replace(original, npcs=Observation(original.npcs.value * 2, 0, "fixture")),
                "pointer_npc_changed_or_already_targeted",
            ),
            (
                replace(
                    original,
                    player=replace(
                        original.player, target=Observation(click.action.target, 0, "fixture")
                    ),
                ),
                "pointer_npc_changed_or_already_targeted",
            ),
        )
        for world, reason in cases:
            state[0] = world
            result = backend.send(
                self.intent(state, clock, action=click.action, lane=ActionLane.POINTER)
            )
            self.assertEqual(result.reason, reason)
        self.assertFalse(device.actions)

    def test_pointer_constructor_rejects_objects_menu_options_and_invalid_physical_points(self):
        binding = npc_binding(pointer_world(self.world()))
        for arguments in (
            {"action": Interact(EntityRef("object", 99), "default")},
            {"action": Interact(binding.action.target, "open_menu")},
            {"screen_x": 2**31},
            {"screen_y": True},
            {"view_signature": ""},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                replace(binding, **arguments)

    def test_both_accept_prayer_and_ability_in_same_tick(self):
        for kind in ("native", "serial"):
            with self.subTest(kind=kind):
                backend, state, clock, device, channel = self.make_backend(kind)
                self.assertTrue(backend.send(self.intent(state, clock)).accepted)
                self.assertTrue(
                    backend.send(
                        self.intent(state, clock, "ability", ActivateAbility(103), ActionLane.GCD)
                    ).accepted
                )
                self.assertTrue(
                    backend.send(
                        self.intent(
                            state, clock, "off_gcd", ActivateAbility(104), ActionLane.OFF_GCD
                        )
                    ).accepted
                )
                if channel:
                    self.assertEqual(channel.taps, [0x3A, 0x3E, 0x3F])
                    self.assertFalse(channel.held)
                else:
                    self.assertEqual(len(device.actions), 3)

    def test_disabled_missing_provenance_and_expired_input_are_rejected(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, channel = self.make_backend(kind, enabled=False)
            intent = self.intent(state, clock)
            self.assertEqual(backend.send(intent).reason, "disabled")
            backend.enabled = True
            self.assertEqual(
                backend.send(replace(intent, observed_ns=None)).reason, "stale_intent_snapshot"
            )
            clock.value += 101 * MS
            self.assertEqual(backend.send(intent).reason, "deadline_expired")
            if channel:
                self.assertEqual(channel.taps, [])

    def test_unknown_server_tick_does_not_fall_back_to_client_logic_tick(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, _ = self.make_backend(kind)
            state[0] = replace(state[0], game_tick=Observation())
            self.assertEqual(backend.send(self.intent(state, clock)).reason, "server_tick_unknown")

    def test_session_changes_latch_a_fault(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, channel = self.make_backend(kind)
            intent = self.intent(state, clock)
            original = state[0]
            state[0] = replace(original, session=SessionIdentity(111, 333, 3))
            self.assertEqual(backend.send(intent).reason, "session_changed")
            state[0] = original
            self.assertEqual(backend.send(intent).reason, "closed_or_faulted")
            if channel:
                self.assertFalse(channel.held)

    def test_snapshot_and_evidence_age_checked_at_dispatch(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, _ = self.make_backend(kind)
            intent = self.intent(state, clock)
            bad = replace(intent, evidence=(Evidence("stale", Observation(1, 151, "fixture")),))
            self.assertEqual(backend.send(bad).reason, "stale_or_unknown_evidence")
            clock.value += 200 * MS
            bad = replace(
                intent, issued_ns=clock(), deadline_ns=clock() + 100 * MS, observed_ns=clock()
            )
            self.assertEqual(backend.send(bad).reason, "stale_world")

    def test_budget_and_duplicate_intents_do_not_toggle_again(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, _ = self.make_backend(kind)
            intent = self.intent(state, clock)
            self.assertTrue(backend.send(intent).accepted)
            self.assertEqual(backend.send(intent).reason, "duplicate_intent")
            for index in range(3):
                self.assertTrue(backend.send(self.intent(state, clock, str(index))).accepted)
            self.assertEqual(
                backend.send(self.intent(state, clock, "excess")).reason, "tick_budget_exhausted"
            )
            state[0] = replace(state[0], game_tick=Observation({"index": 21}, 5, "fixture"))
            self.assertTrue(backend.send(self.intent(state, clock, "next_tick")).accepted)

    def test_already_active_prayer_and_focus_loss_send_no_action(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, channel = self.make_backend(kind)
            self.assertEqual(
                backend.send(self.intent(state, clock, action=SetPrayer("melee"))).reason,
                "already_active",
            )
            backend.guard = lambda *args: False
            self.assertEqual(
                backend.send(self.intent(state, clock)).reason, "foreground_or_user_key_mismatch"
            )
            if channel:
                self.assertEqual(channel.taps, [])

    def test_fault_releases_and_close_is_idempotent(self):
        for kind in ("native", "serial"):
            backend, state, clock, device, channel = self.make_backend(kind)
            self.assertIsNone(backend.fault)
            if channel:
                channel.fail_tap = True
            else:
                device.fail = True
            self.assertFalse(backend.send(self.intent(state, clock)).accepted)
            self.assertIsInstance(backend.fault, str)
            self.assertTrue(backend.fault)
            self.assertEqual(
                backend.send(self.intent(state, clock, "retry")).reason, "closed_or_faulted"
            )
            backend.close()
            backend.close()
            self.assertFalse(backend.send(self.intent(state, clock, "closed")).accepted)
            if channel:
                self.assertTrue(channel.closed)
                self.assertFalse(channel.held)
            else:
                self.assertGreaterEqual(device.releases, 1)
                self.assertEqual(device.closes, 1)

    def test_ordinary_rejection_does_not_report_transport_uncertainty(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, _ = self.make_backend(kind, enabled=False)
            self.assertEqual(backend.send(self.intent(state, clock)).reason, "disabled")
            self.assertIsNone(backend.fault)

    def test_fifo_waiter_is_revalidated_after_waiting(self):
        for kind in ("native", "serial"):
            backend, state, clock, device, channel = self.make_backend(kind)
            entered, resume = threading.Event(), threading.Event()

            def block():
                entered.set()
                if not resume.wait(2):
                    raise DeviceFault("fake_test_wait_timeout")

            if channel:
                channel.hook = block
            else:
                device.hook = block
            results = []
            first = threading.Thread(
                target=lambda: results.append(backend.send(self.intent(state, clock, "first")))
            )
            waiting_intent = self.intent(
                state, clock, "queued", ActivateAbility(103), ActionLane.GCD
            )
            second = threading.Thread(target=lambda: results.append(backend.send(waiting_intent)))
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            deadline = time.monotonic() + 1
            while not backend._queue and time.monotonic() < deadline:
                threading.Event().wait(0.001)
            self.assertTrue(backend._queue)
            clock.value += 500 * MS
            resume.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(
                [r.reason for r in results if r.intent_id == "queued"], ["deadline_expired"]
            )

    def test_serial_food_potion_ability_and_prayer_can_share_tick(self):
        backend, state, clock, _, channel = self.make_backend("serial")
        actions = [
            (ConsumeSlot(0, 200, "eat", 50), ActionLane.FOOD),
            (ConsumeSlot(1, 201, "drink", 51), ActionLane.POTION),
            (ActivateAbility(103), ActionLane.GCD),
            (SetPrayer("magic"), ActionLane.PRAYER),
        ]
        for index, (action, lane) in enumerate(actions):
            self.assertTrue(
                backend.send(self.intent(state, clock, str(index), action, lane)).accepted
            )
        self.assertEqual(channel.taps, [0x40, 0x41, 0x3E, 0x3A])

    def test_changed_item_dose_or_tracking_token_rejects_consumption(self):
        backend, state, clock, _, channel = self.make_backend("serial")
        for item in (
            {"slot": 0, "item_id": 202, "tracking_token": 50},
            {"slot": 0, "item_id": 200, "tracking_token": 999},
        ):
            state[0] = replace(
                state[0], inventory=Observation((item,), 5, "fixture", complete=True)
            )
            intent = self.intent(
                state, clock, action=ConsumeSlot(0, 200, "eat", 50), lane=ActionLane.FOOD
            )
            self.assertEqual(backend.send(intent).reason, "inventory_precondition_changed")
        self.assertEqual(channel.taps, [])

    def test_native_reports_missing_generic_key_api(self):
        backend, state, clock, device, _ = self.make_backend("native")
        intent = self.intent(
            state, clock, action=ConsumeSlot(0, 200, "eat", 50), lane=ActionLane.FOOD
        )
        self.assertEqual(backend.send(intent).reason, "native_item_key_api_unmapped")
        self.assertEqual(device.actions, [])

    def test_both_backends_support_independent_lanes_with_native_chord_api(self):
        for kind in ("native", "serial"):
            backend, state, clock, device, channel = self.make_backend(kind)
            if kind == "native":
                device.chords_supported = True
            actions = (
                (ConsumeSlot(0, 200, "eat", 50), ActionLane.FOOD),
                (ConsumeSlot(1, 201, "drink", 51), ActionLane.POTION),
                (ActivateAbility(103), ActionLane.GCD),
                (SetPrayer("magic"), ActionLane.PRAYER),
            )
            for index, (action, lane) in enumerate(actions):
                self.assertTrue(
                    backend.send(self.intent(state, clock, str(index), action, lane)).accepted
                )
            self.assertEqual(len(device.actions) if kind == "native" else len(channel.taps), 4)

    def test_native_requires_exact_item_token_and_supports_modified_abilities(self):
        backend, state, clock, device, _ = self.make_backend("native")
        device.chords_supported = True
        tokenless = self.intent(
            state, clock, action=ConsumeSlot(0, 200, "eat"), lane=ActionLane.FOOD
        )
        self.assertEqual(backend.send(tokenless).reason, "native_item_tracking_token_required")
        backend.bindings = Bindings(abilities={103: KeyBinding(0x47, 4, True, "fixture")})
        ability = self.intent(state, clock, "ability", ActivateAbility(103), ActionLane.GCD)
        self.assertTrue(backend.send(ability).accepted)
        self.assertEqual(device.actions[-1][1].modifiers, 4)

    def test_changed_binding_rejects_before_native_or_serial_dispatch(self):
        for kind in ("native", "serial"):
            count = [0]

            def provider(action, world):
                count[0] += 1
                return KeyBinding(0x70 if count[0] == 1 else 0x71, CTRL, True, "fixture")

            backend, state, clock, device, channel = self.make_backend(
                kind, binding_provider=provider
            )
            result = backend.send(self.intent(state, clock))
            self.assertEqual(result.reason, "binding_changed_during_dispatch")
            self.assertEqual(device.actions if kind == "native" else channel.taps, [])

    def test_serial_binding_change_after_modifier_press_releases_without_tap(self):
        current_key = [0x70]
        backend, state, clock, _, channel = self.make_backend(
            "serial",
            binding_provider=lambda action, world: KeyBinding(
                current_key[0], CTRL, True, "fixture"
            ),
        )
        original_write = channel.write

        def change_after_press(data):
            result = original_write(data)
            if b"PRESS E0" in data:
                current_key[0] = 0x71
            return result

        channel.write = change_after_press
        result = backend.send(self.intent(state, clock))
        self.assertEqual(result.reason, "binding_changed_during_dispatch")
        self.assertFalse(channel.held or channel.taps)

    def test_queue_capacity_and_shutdown_wake_waiters(self):
        for kind in ("native", "serial"):
            backend, state, clock, device, channel = self.make_backend(
                kind, policy=InputPolicy(maximum_queue=1)
            )
            entered, resume = threading.Event(), threading.Event()

            def block():
                entered.set()
                if not resume.wait(2):
                    raise DeviceFault("test_wait_timeout")

            if channel:
                channel.hook = block
            else:
                device.hook = block
            results = []
            first = threading.Thread(
                target=lambda: results.append(backend.send(self.intent(state, clock, "first")))
            )
            queued = threading.Thread(
                target=lambda: results.append(backend.send(self.intent(state, clock, "queued")))
            )
            first.start()
            self.assertTrue(entered.wait(1))
            queued.start()
            limit = time.monotonic() + 1
            while not backend._queue and time.monotonic() < limit:
                threading.Event().wait(0.001)
            self.assertTrue(backend._queue)
            self.assertEqual(backend.send(self.intent(state, clock, "excess")).reason, "queue_full")
            closer = threading.Thread(target=backend.close)
            closer.start()
            queued.join(1)
            self.assertFalse(queued.is_alive())
            self.assertEqual(
                [result.reason for result in results if result.intent_id == "queued"],
                ["closed_or_faulted"],
            )
            resume.set()
            first.join(2)
            closer.join(2)
            self.assertFalse(first.is_alive() or closer.is_alive())

    def test_known_but_invalid_field_cannot_authorize_input(self):
        for kind in ("native", "serial"):
            backend, state, clock, _, _ = self.make_backend(kind)
            state[0] = replace(
                state[0],
                player=replace(
                    state[0].player,
                    active_prayer=Observation("melee", 0, "fixture", reason="invalid"),
                ),
            )
            self.assertEqual(backend.send(self.intent(state, clock)).reason, "prayer_state_unknown")


class ObservedBindingTests(unittest.TestCase):
    def world(self, rows, *, age=5, reason=None):
        return replace(
            BackendTests().world(),
            action_bar=Observation(tuple(rows), age, "native_memory", reason=reason),
        )

    def row(self, kind="prayer", identity=14594, key="Ctrl+F1", **updates):
        row = {
            "kind": kind,
            "item_id" if kind == "item" else "ability_struct_id": identity,
            "key": key,
            "reason": None,
            "binding_reason": None,
            "source": "native_memory",
        }
        return dict(row, **updates)

    def resolve(self, action, world, *, now=None):
        return observed_binding(
            action,
            world,
            prayer_struct_ids={"magic": 14594},
            now_ns=world.now_ns if now is None else now,
        )

    def test_current_native_prayer_ability_and_item_rows_translate_function_keys(self):
        for action, row in (
            (SetPrayer("magic"), self.row()),
            (ActivateAbility(103), self.row("ability", 103)),
            (ConsumeSlot(0, 200, "eat", 50), self.row("item", 200)),
        ):
            binding = self.resolve(action, self.world([row]))
            self.assertEqual((binding.virtual_key, binding.modifiers), (0x70, CTRL))
            self.assertTrue(binding.validated)
        plain = self.resolve(SetPrayer("magic"), self.world([self.row(key="F12")]))
        self.assertEqual((plain.virtual_key, plain.modifiers), (0x7B, 0))

    def test_missing_duplicate_and_wrong_kind_rows_never_fall_back(self):
        for rows in ([], [self.row(), self.row()], [self.row("ability")]):
            with self.assertRaisesRegex(InputRejected, "binding_missing_or_ambiguous"):
                self.resolve(SetPrayer("magic"), self.world(rows))

    def test_undecoded_modifier_label_and_invalid_rows_reject(self):
        for row in (
            self.row(key=None, modifier_code=6),
            self.row(key="Alt+G"),
            self.row(key="Ctrl+F13"),
            self.row(binding_reason="unmapped"),
            self.row(reason="unmapped"),
            self.row(source=None),
        ):
            with self.assertRaises(InputRejected):
                self.resolve(SetPrayer("magic"), self.world([row]))
        with self.assertRaises(InputRejected):
            self.resolve(SetPrayer("magic"), self.world([123]))

    def test_binding_age_includes_time_waiting_since_world_construction(self):
        world = self.world([self.row()], age=100)
        with self.assertRaisesRegex(InputRejected, "action_bar_unavailable_or_stale"):
            self.resolve(SetPrayer("magic"), world, now=world.now_ns + 51 * MS)
        with self.assertRaises(InputRejected):
            self.resolve(SetPrayer("magic"), world, now=world.now_ns - 1)
        for world in (
            self.world([self.row()], age=151),
            self.world([self.row()], reason="invalid"),
        ):
            with self.assertRaises(InputRejected):
                self.resolve(SetPrayer("magic"), world)

    def test_binding_provider_uses_changed_observed_key_instead_of_static_mapping(self):
        state = [self.world([self.row(key="Ctrl+F2")])]
        clock, device = FakeClock(state[0].now_ns), FakeDLL()
        backend = SendInputActuator(
            device,
            world_provider=lambda: state[0],
            clock=clock,
            session=state[0].session,
            bindings=Bindings(prayers={"magic": KeyBinding(0x70, CTRL, True, "static fixture")}),
            binding_provider=lambda action, world: self.resolve(action, world),
            guard=lambda *args: True,
            enabled=True,
        )
        self.addCleanup(backend.close)
        self.assertTrue(backend.send(BackendTests().intent(state, clock)).accepted)
        self.assertEqual(device.actions[-1][1].virtual_key, 0x71)


class SerialProtocolTests(unittest.TestCase):
    def test_fragmented_writes_and_replies(self):
        channel = FakeChannel()
        channel.maximum_write = 2
        device = SerialKeyboardDevice(channel, io_clock=lambda: channel.io_now)
        self.addCleanup(device.close)
        device.tap(KeyBinding(0x70, CTRL, True, "fixture"), lambda: None)
        self.assertEqual(
            channel.commands, [b"HELLO", b"RELEASE_ALL", b"PRESS E0", b"TAP 3A", b"RELEASE_ALL"]
        )
        self.assertFalse(channel.held)

    def test_timeout_disconnects_without_retrying_or_consuming_late_reply(self):
        channel = FakeChannel()
        device = SerialKeyboardDevice(channel, io_clock=lambda: channel.io_now)
        channel.no_reply = True
        with self.assertRaises(DeviceFault):
            device.tap(KeyBinding(0x70, CTRL, True, "fixture"), lambda: None)
        self.assertTrue(channel.closed)
        self.assertFalse(channel.held)
        self.assertEqual(channel.commands[-1], b"PRESS E0")
        self.assertEqual(channel.commands.count(b"PRESS E0"), 1)

    def test_wrong_device_and_unknown_letter_positions_are_rejected(self):
        channel = FakeChannel()
        channel.greeting = b"SOMETHING ELSE"
        with self.assertRaises(DeviceFault):
            SerialKeyboardDevice(channel, io_clock=lambda: channel.io_now)
        self.assertTrue(channel.closed)
        with self.assertRaisesRegex(Exception, "hid_key_position_unmapped"):
            SerialKeyboardDevice.usage(KeyBinding(0x41, 0, True, "not a layout calibration"))


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class NativeAdapterTests(unittest.TestCase):
    def device(self, *, chord_api=True, owned_copy=False, pointer_api=False, minimap_size=64):
        from rs3_reader.native import CombatState, Info

        info, combat, calls = Info(), CombatState(), []
        info.pid, info.process_creation_time, info.sequence = 111, 222, 1
        info.executable_sha256 = b"a" * 64
        combat.scene_epoch = 3

        def allocate(out):
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1234)
            return 0

        def copy(source, out):
            ctypes.memmove(out, ctypes.byref(source), ctypes.sizeof(source))
            return 0

        functions = {
            "rs3_snapshot_create": allocate,
            "rs3_copy_latest": lambda *args: 0,
            "rs3_snapshot_info": lambda handle, out, size: copy(info, out),
            "rs3_snapshot_combat": lambda handle, out, size: copy(combat, out),
            "rs3_activate_ability": lambda *args: calls.append(("legacy", args[1:])) or 0,
            "rs3_set_prayer": lambda *args: calls.append(("prayer", args[1:])) or 0,
            "rs3_snapshot_destroy": lambda *args: None,
        }
        if chord_api:
            functions.update(
                {
                    "rs3_activate_ability_chord": lambda *args: calls.append(("ability", args[1:]))
                    or 0,
                    "rs3_activate_item_chord": lambda *args: calls.append(("item", args[1:])) or 0,
                }
            )
        copies = []
        if owned_copy:
            functions["rs3_snapshot_copy"] = (
                lambda source, target: copies.append((source.value, target.value)) or 0
            )
        if pointer_api:

            def move(snapshot, x, y, plane, out):
                value = out._obj
                calls.append(
                    (
                        "move",
                        (x, y, plane),
                        tuple(getattr(value, name) for name, _ in value._fields_),
                    )
                )
                return 0

            functions.update(
                {
                    "rs3_sizeof": lambda structure: minimap_size if structure == 12 else 0,
                    "rs3_move_to_tile": move,
                    "rs3_interact": lambda *args: calls.append(("interact", args[1:])) or 0,
                }
            )
        library = SimpleNamespace(
            **{name: FakeFunction(function) for name, function in functions.items()}
        )
        library.copy_sources = copies
        reader = SimpleNamespace(
            library=library,
            context=ctypes.c_void_p(42),
            snapshot=ctypes.c_void_p(9876),
            check_result=lambda result: self.assertEqual(result, 0),
        )
        device = NativeSnapshotDevice(reader)
        self.addCleanup(device.close)
        return device, info, calls

    def test_typed_native_pointer_calls_preserve_coordinates_axes_and_instance(self):
        device, _, calls = self.device(pointer_api=True, owned_copy=True)
        world = pointer_world(BackendTests().world())
        move, click = minimap_binding(world), npc_binding(world)
        device.dispatch(move.action, move, world)
        device.dispatch(click.action, click, world)
        self.assertEqual(
            calls,
            [
                ("move", (3202, 3202, 0), (1, 3200, 3201, 0, -500, 200, 4, 0, 0, -4, 100, 0)),
                ("interact", (99, 1, -490, 204)),
            ],
        )
        self.assertEqual(ctypes.sizeof(device.move_to_tile.argtypes[-1]._type_), 64)
        self.assertIs(device.interact.argtypes[1], ctypes.c_uint64)
        self.assertIs(device.interact.argtypes[-1], ctypes.c_int32)
        self.assertEqual(device.library.copy_sources, [(9876, 1234), (9876, 1234)])

    def test_pointer_capability_and_layout_rejection_do_not_guess_a_fallback(self):
        device, _, calls = self.device()
        world = pointer_world(BackendTests().world())
        for binding, reason in (
            (minimap_binding(world), "native_movement_api_unavailable"),
            (npc_binding(world), "native_interaction_api_unavailable"),
        ):
            with self.assertRaisesRegex(InputRejected, reason):
                device.dispatch(binding.action, binding, world)
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(DeviceFault, "native_minimap_layout_mismatch"):
            self.device(pointer_api=True, minimap_size=56)

    def test_pointer_input_result_and_copied_frame_failures_propagate(self):
        device, info, calls = self.device(pointer_api=True, owned_copy=True)
        world = pointer_world(BackendTests().world())
        click = npc_binding(world)
        info.sequence = 2
        with self.assertRaisesRegex(InputRejected, "native_snapshot_advanced"):
            device.dispatch(click.action, click, world)
        info.sequence = 1
        device.interact.callback = lambda *args: -7
        with self.assertRaisesRegex(InputRejected, "native_rejected:-7"):
            device.dispatch(click.action, click, world)
        device.interact.callback = lambda *args: -6
        with self.assertRaisesRegex(DeviceFault, "native_input_fault:-6"):
            device.dispatch(click.action, click, world)
        self.assertFalse(calls)

    def test_owned_policy_copy_is_independent_of_worker_latest_frame(self):
        device, _, calls = self.device(owned_copy=True)

        def newer_worker(*args):
            self.fail("Worker latest must not replace the already examined consumer frame")

        device.library.rs3_copy_latest.callback = newer_worker
        world = BackendTests().world()
        device.dispatch(SetPrayer("magic"), KeyBinding(0x70, CTRL, True, "fixture"), world)
        self.assertEqual(device.library.copy_sources, [(9876, 1234)])
        self.assertEqual(calls, [("prayer", (3, 0x70, CTRL))])

    def test_advanced_or_missing_consumer_copy_does_not_send_an_old_decision(self):
        device, info, calls = self.device(owned_copy=True)
        world = BackendTests().world()
        binding = KeyBinding(0x70, CTRL, True, "fixture")
        info.sequence = 2
        with self.assertRaisesRegex(InputRejected, "native_snapshot_advanced"):
            device.dispatch(SetPrayer("magic"), binding, world)
        device.reader.snapshot = ctypes.c_void_p()
        with self.assertRaisesRegex(DeviceFault, "native_source_snapshot_unavailable"):
            device.dispatch(SetPrayer("magic"), binding, world)
        self.assertEqual(calls, [])

    def test_modified_ability_and_exact_item_use_typed_chord_exports(self):
        device, _, calls = self.device()
        world = BackendTests().world()
        binding = KeyBinding(0x47, 4, True, "fixture")
        device.dispatch(ActivateAbility(103), binding, world)
        device.dispatch(ConsumeSlot(0, 200, "eat", 50), binding, world)
        self.assertEqual(calls, [("ability", (103, 0x47, 4)), ("item", (0, 200, 50, 0x47, 4))])
        self.assertIs(device.item_chord.argtypes[3], ctypes.c_uint64)
        self.assertIs(device.item_chord.argtypes[2], ctypes.c_int32)

    def test_old_dll_keeps_unmodified_ability_and_rejects_new_operations(self):
        device, _, calls = self.device(chord_api=False)
        world = BackendTests().world()
        device.dispatch(ActivateAbility(103), KeyBinding(0x70, 0, True, "fixture"), world)
        with self.assertRaisesRegex(InputRejected, "native_modified_ability_api_unmapped"):
            device.dispatch(ActivateAbility(103), KeyBinding(0x70, CTRL, True, "fixture"), world)
        with self.assertRaisesRegex(InputRejected, "native_item_key_api_unmapped"):
            device.dispatch(
                ConsumeSlot(0, 200, "eat", 50), KeyBinding(0x70, 0, True, "fixture"), world
            )
        self.assertEqual(calls, [("legacy", (103, 0x70))])

    def test_new_native_sample_requires_policy_revalidation_and_hash_change_faults(self):
        device, info, calls = self.device()
        world = BackendTests().world()
        binding = KeyBinding(0x70, CTRL, True, "fixture")
        info.sequence = 2
        with self.assertRaisesRegex(InputRejected, "native_snapshot_advanced"):
            device.dispatch(SetPrayer("magic"), binding, world)
        info.sequence = 1
        info.executable_sha256 = b"b" * 64
        with self.assertRaisesRegex(DeviceFault, "native_snapshot_executable_changed"):
            device.dispatch(SetPrayer("magic"), binding, world)
        self.assertEqual(calls, [])

    def test_private_snapshot_and_real_abi_calls_against_fake_dll(self):
        from rs3_reader.native import CombatState, Info

        info, combat = Info(), CombatState()
        info.pid, info.process_creation_time, info.sequence = 111, 222, 1
        info.executable_sha256 = b"a" * 64
        combat.scene_epoch = 3
        calls = []

        def allocate(out):
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1234)
            return 0

        def copy(source, target):
            ctypes.memmove(target, ctypes.byref(source), ctypes.sizeof(source))
            return 0

        library = SimpleNamespace(
            **{
                name: FakeFunction(function)
                for name, function in {
                    "rs3_snapshot_create": allocate,
                    "rs3_copy_latest": lambda *args: 0,
                    "rs3_snapshot_info": lambda handle, out, size: copy(info, out),
                    "rs3_snapshot_combat": lambda handle, out, size: copy(combat, out),
                    "rs3_activate_ability": lambda *args: calls.append(("ability", args[1:])) or 0,
                    "rs3_set_prayer": lambda *args: calls.append(("prayer", args[1:])) or 0,
                    "rs3_snapshot_destroy": lambda *args: calls.append(("destroy", ())),
                }.items()
            }
        )
        reader = SimpleNamespace(
            library=library,
            context=ctypes.c_void_p(42),
            check_result=lambda result: self.assertEqual(result, 0),
        )
        device = NativeSnapshotDevice(reader)
        self.addCleanup(device.close)
        world = BackendTests().world()
        device.dispatch(SetPrayer("magic"), KeyBinding(0x70, CTRL, True, "fixture"), world)
        self.assertEqual(calls[0], ("prayer", (3, 0x70, CTRL)))
        combat.scene_epoch = 4
        with self.assertRaises(DeviceFault):
            device.dispatch(ActivateAbility(103), KeyBinding(0x74, 0, True, "fixture"), world)
        self.assertEqual(len(calls), 1)
        library.rs3_copy_latest.callback = lambda *args: -5
        library.rs3_snapshot_info.callback = lambda *args: self.fail(
            "Accessor ran after failed copy"
        )
        with self.assertRaisesRegex(DeviceFault, "native_snapshot_failed"):
            device.dispatch(SetPrayer("magic"), KeyBinding(0x70, CTRL, True, "fixture"), world)
        self.assertEqual(len(calls), 1)
        device.close()
        device.close()
        self.assertEqual(calls[-1][0], "destroy")


class FirmwareTests(unittest.TestCase):
    def test_actual_sketch_parser_dtr_and_watchdog_with_host_stubs(self):
        configured = os.environ.get("RS3_FIRMWARE_COMPILER")
        compiler = json.loads(configured) if configured else None
        if compiler is None:
            found = shutil.which("clang++") or shutil.which("g++")
            compiler = [found] if found else None
        if not compiler:
            self.skipTest("Set RS3_FIRMWARE_COMPILER to a JSON compiler command array")
        if (
            not isinstance(compiler, list)
            or not compiler
            or not all(isinstance(v, str) for v in compiler)
        ):
            self.fail("Invalid firmware host-test compiler command")
        directory = Path(__file__).resolve().parents[2] / "arduino/serial_keyboard/tests"
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / ("firmware.exe" if os.name == "nt" else "firmware")
            subprocess.run(
                [
                    *compiler,
                    "-std=c++11",
                    "-I",
                    str(directory / "stubs"),
                    str(directory / "firmware.cpp"),
                    "-o",
                    str(executable),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
            subprocess.run([str(executable)], check=True, capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
