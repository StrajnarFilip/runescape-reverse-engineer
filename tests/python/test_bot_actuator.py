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
    KeyBinding,
    NativeSnapshotDevice,
    SendInputActuator,
)
from rs3_reader.bot.actuator_serial import SerialActuator, SerialKeyboardDevice
from rs3_reader.bot.intents import (
    ActionLane,
    ActivateAbility,
    ConsumeSlot,
    Evidence,
    Intent,
    Priority,
    SetPrayer,
)
from rs3_reader.bot.world_state import Observation, SessionIdentity, WorldState

FIXTURE = Path(__file__).parent / "fixtures/bot/synthetic_capture.jsonl"
MS = 1_000_000


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
            if channel:
                channel.fail_tap = True
            else:
                device.fail = True
            self.assertFalse(backend.send(self.intent(state, clock)).accepted)
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
    def test_private_snapshot_and_real_abi_calls_against_fake_dll(self):
        from rs3_reader.native import CombatState, Info

        info, combat = Info(), CombatState()
        info.pid, info.process_creation_time, info.sequence = 111, 222, 2
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
