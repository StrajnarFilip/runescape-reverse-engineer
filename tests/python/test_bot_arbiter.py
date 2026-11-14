"""D6 arbitration uses fake devices, captured time and synthetic proposals only."""

import hashlib
import io
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path

from rs3_reader.bot.actuator import RecordingActuator, SendResult
from rs3_reader.bot.arbiter import Arbiter, ArbiterPolicy, ReplayArbiter
from rs3_reader.bot.intents import (
    ActionLane,
    ActivateAbility,
    ConsumeSlot,
    Evidence,
    Intent,
    MoveTo,
    Priority,
    SetPrayer,
)
from rs3_reader.bot.replay import replay
from rs3_reader.bot.world_state import Observation, SessionIdentity, Tile, WorldState

MS = 1_000_000
ROOT = Path(__file__).resolve().parents[2]


def row(sequence=1, time_ms=10000, tick=20):
    return {
        "schema_version": 1,
        "source": "synthetic_fixture",
        "status": "available",
        "pid": 1,
        "process_creation_time": 2,
        "executable_sha256": "a" * 64,
        "monotonic_ms": time_ms,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": 3, "active_prayer": "melee"},
        "health": {"current": 8000, "max": 10000},
        "prayer": {"current": 500, "max": 990},
        "position": {"x": 100, "y": 200, "plane": 0},
        "gcd": {"remaining_ms": 0, "reason": None},
        "abilities": {
            "status": "available",
            "entries": [
                {
                    "struct_id": identity,
                    "castable": True,
                    "cooldown": {"remaining_ms": 0, "reason": None},
                }
                for identity in (10, 11, 12, 13)
            ],
        },
        "inventory": {
            "status": "available",
            "slots": [
                {"slot": slot, "item_id": identity, "quantity": 2, "tracking_token": 10 + slot}
                for slot, identity in ((0, 100), (1, 200))
            ],
        },
        "game_tick": (
            {"value": {"index": tick}, "age_ms": 0, "source": "synthetic_verified_tick"}
            if tick is not None
            else None
        ),
    }


def world(**kwargs):
    return WorldState.from_telemetry(row(**kwargs))


def intent(
    value,
    ident="one",
    *,
    priority=Priority.ROTATION,
    action=None,
    lane=ActionLane.GCD,
    producer="rotation",
    lifetime_ms=100,
):
    return Intent(
        ident,
        producer,
        action or ActivateAbility(10),
        priority,
        lane,
        value.now_ns,
        value.now_ns + lifetime_ms * MS,
        value.session,
        value.sample_sequence,
        (Evidence("health", value.player.health.current),),
        value.observed_ns,
    )


class FakeActuator(RecordingActuator):
    def __init__(self, clock, accept=True):
        super().__init__(clock=clock, accept=accept)
        self.closes, self.hook, self.raises = 0, None, False

    def send(self, proposal):
        if self.hook:
            self.hook(proposal)
        if self.raises:
            raise OSError("synthetic partial transport failure")
        return super().send(proposal)

    def close(self):
        self.closes += 1
        super().close()


class Producer:
    def __init__(self, proposals=()):
        self.proposals = proposals
        self.sent, self.rejected = [], []
        self.valid = True

    def step(self, value):
        return self.proposals

    def can_send(self, proposal, value):
        return self.valid

    def note_sent(self, ident, now):
        self.sent.append((ident, now))

    def note_rejected(self, ident, reason, now):
        self.rejected.append((ident, reason, now))


class ArbiterTests(unittest.TestCase):
    def make(self, value=None, *, accept=True, **options):
        value = value or world()
        clock = [value.now_ns]
        device = FakeActuator(lambda: clock[0], accept)
        arbiter = Arbiter(
            device,
            clock=lambda: clock[0],
            enabled=options.pop("enabled", True),
            prayer_validator=options.pop("prayer_validator", lambda *_: True),
            pointer_validator=options.pop("pointer_validator", lambda *_: True),
            **options,
        )
        self.addCleanup(arbiter.close)
        return arbiter, device, clock

    def test_priority_order_never_sends_rotation_before_survival(self):
        value = world()
        proposals = [
            intent(value),
            intent(
                value,
                "mechanic",
                priority=Priority.MECHANICS,
                action=MoveTo(Tile(101, 200, 0)),
                lane=ActionLane.POINTER,
                producer="mechanics",
            ),
            intent(
                value,
                "prayer",
                priority=Priority.PRAYER,
                action=SetPrayer("magic"),
                lane=ActionLane.PRAYER,
                producer="prayer",
            ),
            intent(
                value,
                "food",
                priority=Priority.SURVIVAL,
                action=ConsumeSlot(0, 100, "eat", 10),
                lane=ActionLane.FOOD,
                producer="survival",
            ),
        ]
        arbiter, device, _ = self.make()
        log = arbiter.step(value, proposals)
        self.assertEqual(
            [attempt.intent.intent_id for attempt in device.attempts],
            ["food", "prayer", "mechanic", "one"],
        )
        self.assertTrue(all(decision.status == "sent" for decision in log.decisions))

    def test_food_potion_ability_and_prayer_share_one_verified_tick(self):
        value = world()
        proposals = [
            intent(value),
            intent(value, "food", action=ConsumeSlot(0, 100, "eat", 10), lane=ActionLane.FOOD),
            intent(
                value, "potion", action=ConsumeSlot(1, 200, "drink", 11), lane=ActionLane.POTION
            ),
            intent(value, "prayer", action=SetPrayer("magic"), lane=ActionLane.PRAYER),
        ]
        arbiter, device, _ = self.make()
        self.assertEqual(
            len([d for d in arbiter.step(value, proposals).decisions if d.status == "sent"]), 4
        )

    def test_gcd_and_off_gcd_have_separate_budgets(self):
        value = world()
        arbiter, device, _ = self.make()
        proposals = [intent(value)] + [
            intent(value, str(identity), action=ActivateAbility(identity), lane=ActionLane.OFF_GCD)
            for identity in (11, 12, 13)
        ]
        log = arbiter.step(value, proposals)
        self.assertEqual(len(device.attempts), 3)
        self.assertEqual(log.decisions[-1].reason, "tick_budget_exhausted")

    def test_unknown_tick_does_not_fall_back_to_the_client_clock(self):
        value = world(tick=None)
        arbiter, device, _ = self.make(value)
        self.assertEqual(
            arbiter.step(value, [intent(value)]).decisions[0].reason, "server_tick_unknown"
        )
        self.assertFalse(device.attempts)

    def test_disabled_constructor_never_sends(self):
        value = world()
        arbiter, device, _ = self.make(enabled=False)
        self.assertEqual(arbiter.step(value, [intent(value)]).decisions[0].reason, "disabled")
        self.assertFalse(device.attempts)

    def test_expiry_and_evidence_freshness_are_checked_before_sending(self):
        value = world()
        arbiter, device, clock = self.make()
        proposal = intent(value, lifetime_ms=10)
        clock[0] += 11 * MS
        self.assertEqual(arbiter.step(value, [proposal]).decisions[0].reason, "deadline_expired")
        bad = replace(intent(value, "bad"), evidence=(Evidence("unknown", Observation()),))
        self.assertEqual(
            arbiter.step(value, [bad]).decisions[0].reason, "evidence_unavailable_or_stale"
        )
        self.assertFalse(device.attempts)

    def test_high_priority_send_delay_cannot_cause_an_expired_lower_send(self):
        value = world()
        arbiter, device, clock = self.make()
        high = intent(
            value,
            "survival",
            priority=Priority.SURVIVAL,
            lane=ActionLane.OFF_GCD,
            action=ActivateAbility(11),
            lifetime_ms=500,
        )
        low = intent(value, "rotation", lifetime_ms=50)
        device.hook = lambda _: clock.__setitem__(0, clock[0] + 60 * MS)
        log = arbiter.step(value, [low, high])
        self.assertEqual([a.intent.intent_id for a in device.attempts], ["survival"])
        self.assertEqual(log.decisions[-1].reason, "deadline_expired")

    def test_rejection_is_reported_to_the_producer_and_result_mailbox(self):
        value = world()
        owner = Producer()
        arbiter, device, _ = self.make(accept=False, producers={"rotation": owner})
        log = arbiter.step(value, [intent(value)])
        self.assertEqual(log.decisions[0].status, "rejected")
        self.assertEqual(owner.rejected[0][1], "recording_rejected")
        self.assertEqual(arbiter.results[0][1].accepted, False)
        self.assertFalse(log.pending_ids)

    def test_pending_action_is_not_resent_even_with_a_new_intent_id(self):
        value = world()
        arbiter, device, _ = self.make()
        arbiter.step(value, [intent(value)])
        result = arbiter.step(value, [intent(value, "different_id")])
        self.assertEqual(result.decisions[0].reason, "awaiting_confirmation")
        self.assertEqual(len(device.attempts), 1)

    def test_ack_requires_newer_ability_observation_not_just_gcd(self):
        value = world()
        arbiter, device, clock = self.make()
        arbiter.step(value, [intent(value)])
        record = row(sequence=2, time_ms=10010)
        record["gcd"]["remaining_ms"] = 500
        clock[0] = 10010 * MS
        self.assertEqual(arbiter.step(WorldState.from_telemetry(record), []).observations, ())
        record["abilities"]["entries"][0]["cooldown"]["remaining_ms"] = 500
        log = arbiter.step(WorldState.from_telemetry(record), [])
        self.assertEqual(log.observations[0]["reason"], "ability_cooldown_observed")
        self.assertFalse(log.pending_ids)

    def test_prayer_requires_a_timing_validator_and_newer_matching_overhead(self):
        value = world()
        proposal = intent(value, action=SetPrayer("magic"), lane=ActionLane.PRAYER)
        arbiter, device, clock = self.make(prayer_validator=None)
        self.assertEqual(
            arbiter.step(value, [proposal]).decisions[0].reason,
            "prayer_timing_validation_unavailable",
        )
        arbiter.prayer_validator = lambda *_: True
        arbiter.step(value, [proposal])
        record = row(sequence=2, time_ms=10010)
        record["combat"]["active_prayer"] = "magic"
        clock[0] = 10010 * MS
        self.assertEqual(
            arbiter.step(WorldState.from_telemetry(record), []).observations[0]["reason"],
            "requested_prayer_observed",
        )

    def test_inventory_changes_are_not_called_success_and_partial_omissions_do_not_ack(self):
        value = world()
        arbiter, device, clock = self.make()
        proposal = intent(value, action=ConsumeSlot(0, 100, "eat", 10), lane=ActionLane.FOOD)
        arbiter.step(value, [proposal])
        clock[0] = 10010 * MS
        current = world(sequence=2, time_ms=10010)
        partial = replace(current, inventory=Observation((), 0, "fixture", complete=False))
        self.assertEqual(arbiter.step(partial, []).observations, ())
        record = row(sequence=3, time_ms=10020)
        record["inventory"]["slots"][0]["quantity"] = 1
        clock[0] = 10020 * MS
        observed = arbiter.step(WorldState.from_telemetry(record), [])
        self.assertIn("not_proof_of_consumption", observed.observations[0]["reason"])
        self.assertFalse(observed.to_record()["game_success_inferred"])

    def test_timeout_scene_change_and_clock_regression_close_the_backend(self):
        for failure in ("timeout", "scene", "clock"):
            value = world()
            arbiter, device, clock = self.make()
            arbiter.step(value, [intent(value)])
            if failure == "timeout":
                changed = world(sequence=2, time_ms=12000)
                clock[0] = changed.now_ns
            elif failure == "scene":
                changed = replace(value, session=SessionIdentity(1, 2, 4))
            else:
                changed = value
                clock[0] -= 1
            log = arbiter.step(changed, [])
            self.assertIsNotNone(log.fault)
            self.assertEqual(device.closes, 1)
            arbiter.step(value, [intent(value, "retry")])
            self.assertEqual(len(device.attempts), 1)

    def test_pointer_requires_validation_and_destination_observation(self):
        value = world()
        proposal = intent(value, action=MoveTo(Tile(101, 200, 0)), lane=ActionLane.POINTER)
        arbiter, device, clock = self.make(pointer_validator=None)
        self.assertEqual(
            arbiter.step(value, [proposal]).decisions[0].reason, "pointer_validation_unavailable"
        )
        arbiter.pointer_validator = lambda *_: True
        arbiter.step(value, [proposal])
        record = row(sequence=2, time_ms=10010)
        record["position"]["x"] = 101
        clock[0] = 10010 * MS
        self.assertEqual(
            arbiter.step(WorldState.from_telemetry(record), []).observations[0]["reason"],
            "destination_tile_observed",
        )

    def test_newest_world_and_producer_guard_are_rechecked(self):
        value = world()
        current = [value]
        owner = Producer()
        arbiter, device, _ = self.make(
            world_provider=lambda: current[0], producers={"rotation": owner}
        )
        owner.valid = False
        self.assertEqual(
            arbiter.step(value, [intent(value)]).decisions[0].reason,
            "producer_precondition_changed",
        )
        owner.valid = True
        record = row()
        record["abilities"]["entries"][0]["castable"] = False
        current[0] = WorldState.from_telemetry(record)
        self.assertEqual(
            arbiter.step(value, [intent(value)]).decisions[0].reason,
            "ability_castability_unknown_or_blocked",
        )
        self.assertFalse(device.attempts)

    def test_duplicate_id_and_budget_persist_across_samples_in_the_same_tick(self):
        value = world()
        arbiter, device, clock = self.make(accept=False)
        proposal = intent(value)
        arbiter.step(value, [proposal])
        self.assertEqual(arbiter.step(value, [proposal]).decisions[0].reason, "duplicate_intent")
        clock[0] = 10010 * MS
        newer = world(sequence=2, time_ms=10010)
        self.assertEqual(
            arbiter.step(newer, [intent(newer, "new")]).decisions[0].reason, "tick_budget_exhausted"
        )

    def test_actuator_or_callback_fault_never_retries_and_closes_keys(self):
        value = world()
        arbiter, device, _ = self.make()
        device.raises = True
        log = arbiter.step(value, [intent(value)])
        self.assertIn("input_state_unknown", log.fault)
        self.assertEqual(device.closes, 1)
        self.assertEqual(log.decisions[0].status, "fault")
        owner = Producer()

        def failed(*_):
            raise RuntimeError("synthetic callback failure")

        owner.note_sent = failed
        arbiter, device, _ = self.make(producers={"rotation": owner})
        log = arbiter.step(value, [intent(value)])
        self.assertEqual(log.decisions[0].status, "sent")
        self.assertTrue(log.pending_ids)
        self.assertIsNotNone(log.fault)
        self.assertEqual(device.closes, 1)

    def test_registered_producers_are_collected_and_logs_are_one_json_line_per_step(self):
        value = world()
        owner = Producer([intent(value)])
        stream = io.StringIO()
        arbiter, device, _ = self.make(producers={"rotation": owner}, log_stream=stream)
        arbiter.step(value)
        arbiter.step(value, [])
        self.assertEqual(len(stream.getvalue().splitlines()), 2)
        self.assertEqual(owner.sent[0][0], "one")
        bad = Producer([intent(value, producer="wrong")])
        arbiter, device, _ = self.make(producers={"rotation": bad})
        self.assertIsNotNone(arbiter.step(value).fault)
        self.assertFalse(device.attempts)

    def test_replay_adapter_is_deterministic_and_recording_only(self):
        class FixtureProducer:
            def step(self, value):
                return (intent(value),)

        logs = []
        for _ in range(2):
            stack = ReplayArbiter({"rotation": FixtureProducer()})
            output = io.StringIO()
            replay(io.StringIO(json.dumps(row()) + "\n"), output, stack)
            self.assertIsInstance(stack.actuator, RecordingActuator)
            self.assertFalse(json.loads(output.getvalue())["input_sent"])
            logs.append(output.getvalue())
        self.assertEqual(logs[0], logs[1])

    def test_real_capture_unknown_readiness_does_not_create_accepted_input(self):
        path = (
            Path(os.environ.get("RS3_LOCAL_CAPTURE_ROOT", str(ROOT / "work/prayer_research")))
            / "telos-session-native-hits-2026-11-10.jsonl"
        )
        if not path.exists():
            self.skipTest("Raw captures intentionally remain local")
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "37c9c4c99481db3f73bcb355174523cb7b2aba02a7815fd1f9e243004410af23",
        )

        class DiagnosticProbe:
            def step(self, value):
                return (
                    replace(
                        intent(value, "probe:" + str(value.sample_sequence), producer="probe"),
                        evidence=(
                            Evidence("diagnostic_probe", Observation(True, 0, "synthetic_probe")),
                        ),
                    ),
                )

        stack = ReplayArbiter({"probe": DiagnosticProbe()})
        output = io.StringIO()
        with path.open() as stream:
            replay(stream, output, stack)
        self.assertFalse(stack.actuator.attempts)
        self.assertTrue(stack.arbiter.last_log.decisions)
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "ac12cf5af97182bb4f31c8999a6b54d2b0429cd46490a6c1993967f9f7c2ac78",
        )

    def test_reported_fault_stops_even_without_new_proposals(self):
        arbiter, device, _ = self.make()
        device.fault = "transport_state_unknown"
        report = arbiter.step(world(), [])
        self.assertEqual(report.fault, "actuator_fault:transport_state_unknown")
        self.assertEqual(device.closes, 1)
        self.assertEqual(device.attempts, ())

    def test_rejected_result_with_reported_fault_closes_before_the_next_lane(self):
        value = world()
        arbiter, device, _ = self.make(accept=False)
        device.hook = lambda proposal: setattr(device, "fault", "partial_input_uncertain")
        report = arbiter.step(
            value,
            [
                intent(value, "first", priority=Priority.SURVIVAL),
                intent(value, "second", action=ActivateAbility(11), lane=ActionLane.OFF_GCD),
            ],
        )
        self.assertEqual(report.fault, "actuator_fault:partial_input_uncertain")
        self.assertEqual([item.status for item in report.decisions], ["fault", "rejected"])
        self.assertEqual(len(device.attempts), 1)
        self.assertEqual(device.closes, 1)

    def test_accepted_input_stays_recorded_when_the_backend_also_faults(self):
        value = world()
        arbiter, device, _ = self.make()
        device.hook = lambda proposal: setattr(device, "fault", "post_acceptance_cleanup_failed")
        report = arbiter.step(value, [intent(value)])
        self.assertTrue(report.to_record()["input_accepted"])
        self.assertEqual(report.decisions[0].status, "sent")
        self.assertEqual(report.fault, "actuator_fault:post_acceptance_cleanup_failed")
        self.assertEqual(device.closes, 1)

    def test_invalid_optional_fault_state_does_not_look_healthy(self):
        for fault in (False, 0, "", {}, []):
            arbiter, device, _ = self.make()
            device.fault = fault
            report = arbiter.step(world(), [intent(world())])
            self.assertEqual(report.fault, "actuator_fault_state_invalid")
            self.assertEqual(device.attempts, ())

    def test_real_a1_fault_channel_stops_real_d6_with_a_fake_native_device(self):
        from rs3_reader.bot.actuator_sendinput import (
            Bindings,
            DeviceFault,
            KeyBinding,
            SendInputActuator,
        )

        class BrokenNativeDevice:
            def __init__(self):
                self.calls = self.releases = self.closes = 0

            def dispatch(self, action, binding, value):
                self.calls += 1
                raise DeviceFault("synthetic_native_delivery_uncertain")

            def release_all(self):
                self.releases += 1

            def close(self):
                self.closes += 1

        value, device = world(), BrokenNativeDevice()
        backend = SendInputActuator(
            device,
            world_provider=lambda: value,
            clock=lambda: value.now_ns,
            session=value.session,
            bindings=Bindings(
                abilities={
                    identity: KeyBinding(0x70, 0, True, "synthetic_calibration")
                    for identity in (10, 11)
                }
            ),
            guard=lambda *args: True,
            enabled=True,
        )
        arbiter = Arbiter(backend, clock=lambda: value.now_ns, enabled=True)
        self.addCleanup(arbiter.close)
        report = arbiter.step(
            value,
            [
                intent(value, "first", priority=Priority.SURVIVAL),
                intent(value, "second", action=ActivateAbility(11), lane=ActionLane.OFF_GCD),
            ],
        )
        self.assertEqual(report.fault, "actuator_fault:synthetic_native_delivery_uncertain")
        self.assertEqual(device.calls, 1)
        self.assertGreaterEqual(device.releases, 1)
        self.assertEqual(device.closes, 1)
        self.assertEqual([item.status for item in report.decisions], ["fault", "rejected"])


if __name__ == "__main__":
    unittest.main()
