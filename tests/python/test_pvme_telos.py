"""Rotation/transport fault tests: fake clocks and devices, no game or HID input."""

import copy
import ctypes
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rs3_reader import pvme_telos as rotation
from rs3_reader import rotation_keys as keys
from rs3_reader.native import Info, Player, Status, Variable


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class FakeSerial:
    def __init__(self):
        self.commands = []
        self.pending = bytearray()
        self.closed = False
        self.response = b"OK\r\n"

    def write(self, payload):
        if self.closed:
            raise OSError("closed")
        self.commands.append(payload)
        self.pending.extend(self.response)

    def read(self):
        result = bytes(self.pending[:1])
        del self.pending[:1]
        return result

    def close(self):
        self.closed = True


def configured_profile():
    """Synthetic test calibration only; never writes a purported live report."""
    profile = rotation.load_profile(rotation.DEFAULT_PROFILE)
    for action in profile["actions"].values():
        action["binding"] = [{"key": "A", "at_ms": 0}]
    for segment in profile["segments"].values():
        for step in segment["steps"]:
            if step["after_ticks"] is None:
                step["after_ticks"] = 3
            for index, entry in enumerate(step["actions"]):
                entry["at_ms"] = index * 100
    return profile


def validation_report(profile, segments, choices):
    now = datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "kind": "pvme_rotation",
        "backend": "sendinput",
        "client_sha256": "a" * 64,
        "evidence": "SYNTHETIC UNIT TEST ONLY",
        "recorded_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "enrage_band": [300, 999],
        "rungs": {
            str(rung): {
                "status": "passed",
                "clean_runs": 2,
                "watchdog_trips": 0,
                "event_queue_losses": 0,
            }
            for rung in range(1, 5)
        },
        "plans": {segment: rotation.plan_digest(profile, segment, choices) for segment in segments},
    }


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.profile = configured_profile()
        self.choices = rotation.resolve_choices(self.profile, [])

    def test_all_guide_routes_compile_with_explicit_test_calibration(self):
        for segment in self.profile["segments"]:
            self.assertTrue(rotation.compile_segment(self.profile, segment, self.choices))

    def test_default_profile_is_honestly_unconfigured(self):
        profile = rotation.load_profile(rotation.DEFAULT_PROFILE)
        self.assertTrue(all(action["binding"] is None for action in profile["actions"].values()))
        for segment in profile["segments"]:
            with self.subTest(segment=segment), self.assertRaises(ValueError):
                rotation.compile_segment(profile, segment, self.choices)

    def test_p1_and_font3_preserve_source_order_and_stall_release(self):
        actions = [
            entry["name"]
            for step in self.profile["segments"]["p1"]["steps"]
            for entry in step["actions"]
        ]
        self.assertEqual(
            actions,
            [
                "devotion",
                "surge",
                "berserk",
                "adrenaline_renewal",
                "greater_barge",
                "meteor_strike",
                "assault",
                "overpower",
                "ezk_spec",
                "dragon_scimitar_spec",
            ],
        )
        font = self.profile["segments"]["font3"]["steps"]
        self.assertEqual(
            [step["actions"][0]["name"] for step in font[-3:]],
            ["ezk_stall", "ezk_release", "greater_fury"],
        )

    def test_choice_changes_only_the_selected_alternative(self):
        choices = rotation.resolve_choices(self.profile, ["red_finisher=punish"])
        chunks = rotation.compile_segment(self.profile, "p5_red", choices)
        self.assertEqual(chunks[-1][1][-1][1], "punish")
        with self.assertRaises(ValueError):
            rotation.resolve_choices(self.profile, ["red_finisher=berserk"])

    def test_missing_timing_offsets_and_overlapping_keys_are_rejected(self):
        for field in ("after_ticks", "at_ms", "overlap"):
            profile = copy.deepcopy(self.profile)
            step = profile["segments"]["p1"]["steps"][0]
            if field == "after_ticks":
                step["after_ticks"] = None
            else:
                step["actions"][1]["at_ms"] = None if field == "at_ms" else 0
            with self.subTest(field=field), self.assertRaises(ValueError):
                rotation.compile_segment(profile, "p1", self.choices)

    def test_macro_key_spacing_and_reserved_prayers_are_validated(self):
        bad_bindings = [
            [{"key": "A", "at_ms": 0}, {"key": "B", "at_ms": 20}],
            [{"key": "Ctrl+F3", "at_ms": 0}],
            [{"key": "F8", "at_ms": 0}],
            [{"key": "Shift+F12", "at_ms": 0}],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            for binding in bad_bindings:
                profile = copy.deepcopy(self.profile)
                profile["actions"]["berserk"]["binding"] = binding
                path.write_text(json.dumps(profile), encoding="utf-8")
                with self.subTest(binding=binding), self.assertRaises(ValueError):
                    rotation.load_profile(path)

    def test_invalid_numbers_and_duplicate_json_fail(self):
        for value in (True, float("nan"), float("inf"), -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rotation.number(value, "delay", 0, 300)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text('{"version":1,"version":2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                rotation.read_json(path)

    def test_plan_and_blocked_run_never_construct_input_backend(self):
        with patch.object(rotation, "SendInputBackend") as backend:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(rotation.main(["plan", "--segment", "p1"]), 0)
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(rotation.main(["run", "--segment", "p1", "--enrage", "300"]), 2)
            backend.assert_not_called()

    def test_no_run_with_both_p5_routes(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(
                rotation.main(["run", "--segment", "p5_red", "--segment", "p5_green"]), 2
            )


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.profile = configured_profile()
        self.choices = rotation.resolve_choices(self.profile, [])
        self.report = validation_report(self.profile, ["p1"], self.choices)

    def errors(self, report=None, backend="sendinput", enrage=300):
        return rotation.validation_errors(
            report or self.report, self.profile, ["p1"], self.choices, backend, enrage
        )

    def test_matching_synthetic_report_and_wrong_backend_or_band(self):
        self.assertEqual(self.errors(), [])
        self.assertTrue(self.errors(backend="arduino"))
        self.assertTrue(self.errors(enrage=1000))

    def test_binding_timing_and_choice_changes_invalidate_calibration(self):
        for mutation in ("binding", "timing", "choice"):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == "binding":
                    self.profile["actions"]["berserk"]["binding"][0]["key"] = "B"
                elif mutation == "timing":
                    self.profile["hold_ms"] = 40
                else:
                    self.choices["red_finisher"] = "punish"
                self.assertTrue(self.errors())

    def test_expired_incomplete_and_nonzero_loss_reports_fail(self):
        mutations = [
            lambda report: report.update(expires_at="2020-01-01T00:00:00+00:00"),
            lambda report: report["rungs"].pop("2"),
            lambda report: report["rungs"]["4"].update(event_queue_losses=1),
            lambda report: report["rungs"]["4"].update(watchdog_trips=1),
            lambda report: report["rungs"]["4"].update(clean_runs=True),
            lambda report: report.update(client_sha256="unknown"),
            lambda report: report.update(recorded_at="2020-01-01"),
        ]
        for mutate in mutations:
            report = copy.deepcopy(self.report)
            mutate(report)
            self.assertTrue(self.errors(report))


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.profile = configured_profile()
        self.guard = Mock()
        self.guard.checkpoint.return_value = None
        self.backend = Mock()
        self.backend.tap.side_effect = lambda chord, hold, check, wait: wait(hold)

    def run_chunks(self, chunks):
        with patch("sys.stdout", new_callable=io.StringIO):
            rotation.execute_chunks(
                chunks,
                self.profile,
                self.backend,
                self.guard,
                clock=self.clock.time,
                sleep=self.clock.sleep,
            )

    def test_absolute_deadlines_do_not_accumulate_key_hold_drift(self):
        starts = []

        def tap(chord, hold, check, wait):
            starts.append(self.clock.time())
            wait(hold)

        self.backend.tap.side_effect = tap
        chord = keys.parse_chord("A")
        self.run_chunks([("start", [(0, "a", "A", chord), (600, "b", "A", chord)])])
        self.assertEqual(starts, [0, 0.6])

    def test_late_frame_aborts_instead_of_catching_up(self):
        self.guard.check_idle_keys.side_effect = lambda chord: self.clock.sleep(0.2)
        with self.assertRaisesRegex(RuntimeError, "Missed deadline"):
            self.run_chunks([("start", [(0, "a", "A", keys.parse_chord("A"))])])
        self.backend.tap.assert_not_called()

    def test_stop_during_wait_prevents_following_key(self):
        def check():
            if self.clock.time() >= 0.1:
                raise RuntimeError("F12")

        self.guard.check.side_effect = check
        with self.assertRaisesRegex(RuntimeError, "F12"):
            self.run_chunks([("start", [(600, "a", "A", keys.parse_chord("A"))])])
        self.backend.tap.assert_not_called()

    def test_checkpoint_resets_origin_and_is_not_skipped(self):
        chord = keys.parse_chord("A")
        self.guard.checkpoint.side_effect = lambda message: self.clock.sleep(5)
        self.run_chunks([("one", [(0, "a", "A", chord)]), ("two", [(0, "b", "A", chord)])])
        self.assertEqual(self.guard.checkpoint.call_count, 2)
        self.assertEqual(self.backend.tap.call_count, 2)


class TransportTests(unittest.TestCase):
    def test_serial_win32_structure_layouts(self):
        self.assertEqual(ctypes.sizeof(keys.DeviceControlBlock), 28)
        self.assertEqual(keys.DeviceControlBlock.byte_size.offset, 18)
        self.assertEqual(ctypes.sizeof(keys.CommTimeouts), 20)

    def test_physical_chords_match_across_transports(self):
        chord = keys.parse_chord("ctrl+shift+PgDn")
        self.assertEqual([key.usage for key in chord], [0xE0, 0xE1, 0x4E])
        self.assertTrue(chord[-1].extended)
        for value in ("Ctrl", "A+B", "Ctrl+Ctrl+A", "Unknown", "A+"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                keys.parse_chord(value)

    def test_client_label_uses_the_foreground_layout_scan_code(self):
        chord = keys.resolve_layout_chord("Ctrl+Z", lambda vk: 0x15 if vk == ord("Z") else 0)
        self.assertEqual(chord, keys.parse_chord("Ctrl+Y"))
        self.assertEqual(keys.resolve_layout_chord("Up", lambda _: 0xE048), keys.parse_chord("Up"))
        with self.assertRaises(ValueError):
            keys.resolve_layout_chord("Z", lambda _: 0)

    def test_sendinput_layout_and_release_after_wait_interruption(self):
        self.assertEqual(
            ctypes.sizeof(keys.Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        )
        packets = []

        def send(count, pointer, size):
            packet = ctypes.cast(pointer, ctypes.POINTER(keys.Input)).contents.data.keyboard
            packets.append((packet.scan_code, packet.flags))
            return count

        backend = keys.SendInputBackend(sender=send)
        with self.assertRaises(KeyboardInterrupt):
            backend.tap(
                keys.parse_chord("Ctrl+PageDown"),
                0.035,
                lambda: None,
                Mock(side_effect=KeyboardInterrupt),
            )
        self.assertEqual([scan for scan, flags in packets], [0x1D, 0x51, 0x51, 0x1D])
        self.assertTrue(all(flags & keys.KEYEVENTF_KEYUP for scan, flags in packets[-2:]))
        self.assertEqual(backend.held, [])

    def test_failed_second_press_releases_both_possible_keys(self):
        sender = Mock(side_effect=[1, 0, 1, 1])
        backend = keys.SendInputBackend(sender=sender)
        with self.assertRaises(OSError):
            backend.tap(keys.parse_chord("Ctrl+A"), 0.035, lambda: None, lambda duration: None)
        self.assertEqual(sender.call_count, 4)
        self.assertEqual(backend.held, [])

    def test_failed_release_still_attempts_other_releases(self):
        sender = Mock(side_effect=[1, 1, 0, 1, 1])
        backend = keys.SendInputBackend(sender=sender)
        with self.assertRaises(RuntimeError):
            backend.tap(keys.parse_chord("Ctrl+A"), 0.035, lambda: None, lambda duration: None)
        self.assertEqual(backend.held, [keys.KEYS["A"]])
        backend.close()
        self.assertTrue(backend.closed)
        self.assertEqual(backend.held, [])

    def test_serial_chord_uses_existing_protocol_and_release_all(self):
        transport = FakeSerial()
        backend = keys.ArduinoBackend(transport=transport)
        backend.tap(keys.parse_chord("Ctrl+A"), 0.035, lambda: None, lambda duration: None)
        backend.close()
        backend.close()
        self.assertEqual(
            transport.commands,
            [b"RELEASE_ALL\n", b"PRESS E0\n", b"PRESS 04\n", b"RELEASE_ALL\n", b"RELEASE_ALL\n"],
        )
        self.assertTrue(transport.closed)

    def test_serial_err_timeout_and_overlong_reply_close_without_retry(self):
        for reply in (b"ERR KEY\r\n", b"", b"x" * 65):
            with self.subTest(reply=reply):
                transport = FakeSerial()
                clock = FakeClock()
                backend = keys.ArduinoBackend(
                    transport=transport, clock=clock.time, sleep=clock.sleep
                )
                transport.response = reply
                with self.assertRaises(OSError):
                    backend.tap(keys.parse_chord("A"), 0.035, lambda: None, lambda duration: None)
                self.assertTrue(transport.closed)
                self.assertEqual(transport.commands, [b"RELEASE_ALL\n", b"PRESS 04\n"])
                backend.close()


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.reader = SimpleNamespace(
            info=Info(),
            player=Player(),
            combat=SimpleNamespace(scene_epoch=1),
            sample=Mock(),
            monotonic_ns=lambda: 1_000_000_000,
        )
        info, player = self.reader.info, self.reader.player
        info.status = Status.AVAILABLE
        info.monotonic_ns = 1_000_000_000
        info.pid, info.process_creation_time = 123, 456
        info.executable_sha256 = b"a" * 64
        player.valid_variables = 1 << Variable.HEALTH
        player.raw_variables[Variable.HEALTH] = 100
        self.guard = rotation.LiveGuard.__new__(rotation.LiveGuard)
        self.guard.reader = self.reader
        self.guard.expected_hash = "a" * 64
        self.guard.evidence_check = lambda: None
        self.guard.identity = (123, 456)
        self.guard.scene, self.guard.window = 1, 55
        self.guard.key_state = Mock(return_value=0)
        self.guard.foreground = Mock(return_value=55)

        def window_pid(window, pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value = 123

        self.guard.window_pid = window_pid
        self.guard.visible = self.guard.enabled = lambda window: True
        self.guard.iconic = lambda window: False

    def test_guard_accepts_fresh_matching_foreground(self):
        self.assertEqual(self.guard.check(), 55)

    def test_focus_stale_scene_restart_hash_death_and_unknown_health_abort(self):
        mutations = [
            lambda: setattr(self.guard, "foreground", lambda: 56),
            lambda: setattr(self.reader.info, "monotonic_ns", 1),
            lambda: setattr(self.reader.info, "monotonic_ns", 2_000_000_000),
            lambda: setattr(self.reader.combat, "scene_epoch", 2),
            lambda: setattr(self.reader.info, "process_creation_time", 457),
            lambda: setattr(self.reader.info, "executable_sha256", b"b" * 64),
            lambda: self.reader.player.raw_variables.__setitem__(Variable.HEALTH, 0),
            lambda: setattr(self.reader.player, "valid_variables", 0),
            lambda: setattr(self.reader.player, "clock_stalled_ns", rotation.MAX_FRAME_AGE_NS + 1),
        ]
        for mutate in mutations:
            self.setUp()
            mutate()
            with self.assertRaises(RuntimeError):
                self.guard.check()

    def test_stop_and_user_held_keys_abort(self):
        self.guard.key_state.side_effect = lambda key: -32768 if key == 0x11 else 0
        with self.assertRaisesRegex(RuntimeError, "held modifiers"):
            self.guard.check_idle_keys(keys.parse_chord("A"))
        self.guard.key_state.return_value = -32768
        self.guard.key_state.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "F12"):
            self.guard.check()

    def test_held_start_key_requires_release_then_a_new_press(self):
        # The initial held F8 is a baseline, never an arm event.
        self.guard.key_state.side_effect = [0, -32768, 0, 0, 0, -32768, 0, 0, 0]
        with (
            patch.object(rotation.time, "sleep") as sleep,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.guard.checkpoint("test")
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(self.guard.window, 55)

    def test_focus_change_during_start_key_does_not_arm(self):
        # Two windows owned by the same game PID; a held key crossing between
        # them must establish a new baseline, then a fresh press/release.
        self.guard.foreground.side_effect = [55, 55, 56, 56, 56, 56, 56]
        self.guard.key_state.side_effect = [0, 0, 0, -32768, 0, -32768, 0, 0, 0, -32768, 0, 0, 0]
        with (
            patch.object(rotation.time, "sleep") as sleep,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.guard.checkpoint("test")
        self.assertEqual(sleep.call_count, 5)
        self.assertEqual(self.guard.window, 56)


if __name__ == "__main__":
    unittest.main()
