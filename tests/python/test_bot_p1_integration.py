"""Combined P1 ordering, priority and scope checks with the existing native-shape fixture."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import test_bot_supervisor as fixtures
from rs3_reader.bot.p1_rotation import IntegratedP1Rotation
from rs3_reader.bot.world_state import WorldState
from rs3_reader.rotation_keys import (
    BatchInputError,
    SendInputBackend,
    SharedKeyBatchDevice,
    native_chord,
    parse_chord,
)


class IntegratedP1Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.P1AttemptTests()
        self.fixture.setUp()
        self.opening = IntegratedP1Rotation(
            self.fixture.writer, self.fixture.label, lambda: self.fixture.now
        )
        self.opening.discover_bindings()
        self.fixture.attempt = self.opening

    def world(self, **kwargs):
        record = self.fixture.frame(**kwargs)
        return record, self.use(record)

    def use(self, record):
        self.fixture.current = WorldState.from_telemetry(record)
        return self.fixture.current

    def first_two_batches(self):
        _, world = self.world()
        self.opening.observe(world)
        self.opening.dispatch(world)
        record, _ = self.world()
        self.fixture.cooldown(record, 25028)
        world = self.use(record)
        self.opening.observe(world)
        self.opening.dispatch(world)
        self.assertEqual([len(batch) for batch in self.fixture.calls], [1, 2])
        # Berserk precedes the renewal, even when adrenaline has remained at 120.
        self.assertEqual(self.fixture.calls[-1][0].ability_id, 14707)
        self.assertEqual(self.fixture.calls[-1][1].expected_item_id, 49079)
        record, _ = self.world()
        self.fixture.cooldown(record, 14707)
        self.opening.observe(self.use(record))
        self.assertEqual(self.opening.engine.index, 2)

    def test_dormant_npc_outside_instance_does_not_arm_or_send(self):
        record, _ = self.world()
        record["encounter"]["raw_candidates"]["6359"]["value"] = 1000
        world = self.use(record)
        self.opening.observe(world)
        self.opening.dispatch(world)
        self.assertIsNone(self.opening.engine)
        self.assertEqual(self.fixture.calls, [])

    def test_scoped_world_retains_all_p1_evidence_and_owns_its_copy(self):
        record, full = self.world()
        record["abilities"]["entries"].append({"struct_id": 999999, "name": "unrelated"})
        record["npcs"]["entities"].append({"config_id": 123, "instance_id": 99})
        record["ui"] = {"reason": None, "complete": True, "widgets": [1, 2, 3]}
        world = self.opening.world(record, now_ns=self.fixture.now)
        self.assertEqual(world.session, full.session)
        self.assertEqual(world.player.adrenaline_percent, full.player.adrenaline_percent)
        self.assertEqual(world.events, full.events)
        self.assertEqual(len(world.npcs.value), 1)
        self.assertNotIn(999999, [row["struct_id"] for row in world.abilities.value])
        self.assertNotIn("ui", world.raw)
        record["inventory"]["slots"][0]["quantity"] = 0
        self.assertEqual(world.inventory.value[0]["quantity"], 1)

    def test_observation_does_not_send_and_urgent_mechanic_defers_rotation(self):
        _, world = self.world()
        self.opening.observe(world)
        self.assertEqual(self.fixture.calls, [])
        self.opening.dispatch(world, deferred=True)
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.opening.engine.index, 0)
        self.opening.dispatch(world)
        self.assertEqual(len(self.fixture.calls), 1)
        # Accepted input without a newer cooldown does not advance or repeat it.
        _, world = self.world()
        self.opening.observe(world)
        self.opening.dispatch(world)
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(self.opening.engine.index, 0)

    def test_eof_uses_gcd_without_a_personal_cooldown_and_needs_its_cast_signal(self):
        _, world = self.world()
        self.opening.observe(world)
        self.opening.engine.index = 9
        action = self.opening.engine.current_step["actions"][0]
        record, _ = self.world(dormant=False, selected=True)
        record["equipment"]["slots"].append(
            dict(slot=2, item_id=51065, quantity=1, identity_ambiguous=False)
        )
        for ability in record["abilities"]["entries"]:
            if ability["struct_id"] == 11748:
                # A generic button timer is not an EoF-specific delay or acknowledgement.
                ability["cooldown"] = dict(remaining_ms=60000, reason=None, start_tick=900)
        current = self.use(record)
        self.assertTrue(self.opening._ready(action, current))
        self.assertFalse(self.opening._acknowledged(action, current, current))
        record["gcd"] = dict(remaining_ms=1000, reason=None)
        self.assertFalse(self.opening._ready(action, self.use(record)))

    def test_retained_adrenaline_then_asg_confirm_individually(self):
        self.first_two_batches()
        _, world = self.world(dormant=False)
        self.opening.observe(world)
        events = self.opening.dispatch(world)
        sent = next(event for event in events if event["event"] == "p1_batch_sent")
        self.assertEqual(sent["actions"], ["target_cycle", "vulnerability", "greater_barge"])
        self.assertFalse(sent["server_same_tick_confirmed"])
        self.assertEqual(
            [action.virtual_key for action in self.fixture.calls[-1]],
            [ord("A"), ord("S"), ord("G")],
        )
        record, _ = self.world(dormant=False, selected=True)
        self.fixture.cooldown(record, 14678)
        record["inventory"]["slots"][1]["quantity"] = 9
        events = self.opening.observe(self.use(record))
        self.assertEqual(self.opening.engine.index, 3)
        self.assertEqual(
            [event["action"] for event in events if event["event"] == "p1_action_confirmed"],
            ["target_cycle", "vulnerability", "greater_barge"],
        )

    def test_priority_deferral_preserves_first_wake_and_rejects_a_late_asg(self):
        self.first_two_batches()
        _, world = self.world(dormant=False)
        self.opening.observe(world)
        self.opening.dispatch(world, deferred=True)
        self.assertEqual(len(self.fixture.calls), 2)
        _, world = self.world(dormant=False, time_ms=self.fixture.now / fixtures.MS + 200)
        events = self.opening.observe(world)
        self.assertTrue(self.opening.stopped)
        self.assertEqual(self.opening.reason, "wake_window_missed")
        self.assertEqual(len(self.fixture.calls), 2)
        self.assertTrue(events)

    def test_queue_loss_and_session_changes_end_only_the_rotation(self):
        _, world = self.world()
        self.opening.observe(world)
        record, _ = self.world()
        record["npc_action_events_lost"] = 1
        self.opening.observe(self.use(record))
        self.assertTrue(self.opening.stopped)
        self.assertIn("event_queue_loss", self.opening.reason)
        self.assertFalse(self.fixture.writer.enabled)
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.fixture.releases, [True])

    def test_phase_two_does_not_invent_sequence_completion_or_close_shared_backend(self):
        backend = Mock()
        self.fixture.writer.device = SharedKeyBatchDevice(
            backend, lambda: None, lambda _: True, lambda: self.fixture.now
        )
        _, world = self.world()
        self.opening.observe(world)
        _, world = self.world(dormant=False, telos={"phase": 2})
        self.opening.observe(world)
        self.assertEqual(self.opening.reason, "phase_one_ended")
        self.assertFalse(self.opening.engine.complete)
        backend.close.assert_not_called()
        backend.release_all.assert_called_once()

    def test_prayer_defensive_and_ordered_rotation_use_one_balanced_win32_transport(self):
        sender = Mock(side_effect=lambda count, *_: count)
        backend = SendInputBackend(sender=sender)
        reports = []
        self.fixture.writer.device = SharedKeyBatchDevice(
            backend,
            lambda: None,
            lambda _: True,
            lambda: self.fixture.now,
            wait=lambda _: None,
            on_input=lambda index, action, accepted, start, end: reports.append(
                (action.virtual_key, accepted)
            ),
        )
        _, world = self.world()
        self.opening.observe(world)
        backend.tap(parse_chord("Ctrl+F4"), 0.05, lambda: None, lambda _: None)
        self.opening.dispatch(world)
        self.assertEqual(reports, [(ord("G"), True)])  # Devotion's Alt+G.
        record, _ = self.world()
        self.fixture.cooldown(record, 25028)
        world = self.use(record)
        self.opening.observe(world)
        backend.tap(parse_chord("Alt+Q"), 0.05, lambda: None, lambda _: None)
        self.opening.dispatch(world, deferred=True)
        self.assertEqual(self.opening.engine.index, 1)
        self.assertEqual(len(reports), 1)
        _, world = self.world()
        self.opening.observe(world)
        self.opening.dispatch(world)
        self.assertEqual(reports, [(ord("G"), True), (ord("F"), True), (0x22, True)])
        self.assertEqual(backend.held, [])
        self.assertEqual(backend.possible_keys, [])
        self.assertFalse(backend.closed)
        self.assertGreater(sender.call_count, 0)
        self.assertEqual(
            [call.args[0] for call in sender.call_args_list if call.args[0] > 1], [4, 4]
        )  # Alt+G, then F followed by Page Down, each one call.


class SharedBatchTests(unittest.TestCase):
    def test_win32_partial_batch_reports_only_the_inserted_prefix_and_releases_it(self):
        sender = Mock(side_effect=[3, 1])  # A down/up, S down; release S succeeds.
        backend = SendInputBackend(sender=sender)
        reports = []
        device = SharedKeyBatchDevice(
            backend,
            lambda: None,
            lambda _: True,
            lambda: 100,
            on_input=lambda index, action, accepted, start, end: reports.append((index, accepted)),
        )
        actions = [SimpleNamespace(virtual_key=ord(key), modifiers=0) for key in "ASG"]
        with self.assertRaises(BatchInputError) as caught:
            device.dispatch_key_batch(actions, object())
        self.assertEqual(caught.exception.chord_outcomes, [True, False, None])
        self.assertEqual(reports, [(0, True), (1, False)])
        self.assertEqual(sender.call_args_list[0].args[0], 6)
        self.assertEqual(sender.call_args_list[1].args[0], 1)
        self.assertEqual(backend.possible_keys, [])
        self.assertTrue(backend.failed)
        with self.assertRaises(RuntimeError):
            backend.tap_batch([parse_chord("G")], lambda: None)

    def test_win32_batch_does_not_wait_and_guard_refusal_inserts_nothing(self):
        sender = Mock(side_effect=lambda count, *_: count)
        backend = SendInputBackend(sender=sender)
        wait = Mock(side_effect=AssertionError("Batch must not sleep per key"))
        device = SharedKeyBatchDevice(backend, lambda: None, lambda _: True, lambda: 100, wait=wait)
        actions = [SimpleNamespace(virtual_key=ord(key), modifiers=0) for key in "ASG"]
        device.dispatch_key_batch(actions, object())
        sender.assert_called_once()
        self.assertEqual(sender.call_args.args[0], 6)
        wait.assert_not_called()
        device.snapshot_guard = lambda _: False
        with self.assertRaisesRegex(RuntimeError, "snapshot"):
            device.dispatch_key_batch(actions, object())
        sender.assert_called_once()

    def test_native_key_translation_supports_current_letter_page_and_modifier_bindings(self):
        self.assertEqual(native_chord(ord("G"), 4), parse_chord("Alt+G"))
        self.assertEqual(native_chord(0x21, 0), parse_chord("PageUp"))
        self.assertEqual(native_chord(0xBB, 0), parse_chord("Equals"))
        self.assertEqual(native_chord(0x73, 1), parse_chord("Ctrl+F4"))
        self.assertEqual(native_chord(ord("Z"), 0, lambda _: 0x15), parse_chord("Y"))
        for virtual, modifier in ((0xFF, 0), (ord("A"), 8), (0x11, 0)):
            with self.subTest(virtual=virtual, modifier=modifier), self.assertRaises(ValueError):
                native_chord(virtual, modifier)

    def test_shared_backend_checks_each_key_and_logs_partial_submission(self):
        reports, checks = [], []
        backend = Mock()

        def tap(chord, hold, check, wait):
            check()
            checks.append(chord[-1].name)
            if chord[-1].name == "S":
                raise RuntimeError("focus changed")

        backend.tap.side_effect = tap
        device = SharedKeyBatchDevice(
            backend,
            lambda: None,
            lambda _: True,
            lambda: 100,
            on_input=lambda index, action, accepted, start, end: reports.append(
                (index, accepted, start, end)
            ),
        )
        actions = [SimpleNamespace(virtual_key=ord(key), modifiers=0) for key in "ASG"]
        with self.assertRaisesRegex(RuntimeError, "focus changed"):
            device.dispatch_key_batch(actions, object())
        self.assertEqual(checks, ["A", "S"])
        self.assertEqual(reports, [(0, True, 100, 100), (1, False, 100, 100)])
        self.assertEqual(backend.tap.call_count, 2)

    def test_invalid_later_key_is_rejected_before_any_input(self):
        backend = Mock()
        device = SharedKeyBatchDevice(backend, lambda: None, lambda _: True, lambda: 100)
        with self.assertRaises(ValueError):
            device.dispatch_key_batch(
                [
                    SimpleNamespace(virtual_key=ord("A"), modifiers=0),
                    SimpleNamespace(virtual_key=255, modifiers=0),
                ],
                object(),
            )
        backend.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
