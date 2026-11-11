"""Decision boundaries preserve unknowns, provenance and independent action lanes."""

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from rs3_reader.bot import (
    ActionLane,
    ActivateAbility,
    Actuator,
    ConsumeOperation,
    ConsumeSlot,
    EntityRef,
    Evidence,
    Intent,
    Interact,
    MoveTo,
    Observation,
    Priority,
    RecordingActuator,
    SetPrayer,
    Tile,
    WorldState,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bot" / "synthetic_capture.jsonl"


def capture_rows():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def make_intent(action=None, lane=ActionLane.PRAYER):
    world = WorldState.from_telemetry(capture_rows()[0])
    return Intent(
        "test",
        "test_producer",
        action or SetPrayer("magic"),
        Priority.PRAYER,
        lane,
        world.now_ns,
        world.now_ns + 100,
        world.session,
        world.sample_sequence,
        (Evidence("player.health.current", world.player.health.current),),
        observed_ns=world.observed_ns,
    )


class WorldStateTests(unittest.TestCase):
    def test_current_telemetry_and_value_age_are_distinct(self):
        world = WorldState.from_telemetry(capture_rows()[0])
        self.assertEqual(world.player.health.current.value, 8700)
        self.assertEqual(world.player.health.maximum.value, 8700)
        self.assertEqual(world.player.prayer.current.value, 840)
        self.assertEqual(world.player.adrenaline_percent.value, 0)
        self.assertEqual(world.player.health.current.age_ms, 5)
        self.assertEqual(world.now_ns, 10_005_000_000)
        self.assertTrue(world.player.health.current.is_fresh(5))
        self.assertFalse(world.player.health.current.is_fresh(4))
        self.assertEqual(world.player.tile.value, Tile(3200, 3201, 0))
        self.assertTrue(world.session.complete)

    def test_placeholders_are_named_unknowns_not_raw_stage_or_client_tick(self):
        world = WorldState.from_telemetry(capture_rows()[0])
        self.assertEqual(world.telos.stage_raw.value, 3)
        self.assertEqual(world.client_logic_tick.value, 500)
        fields = (
            (world.player.facing, "P1"),
            (world.player.target, "P1"),
            (world.npcs.value[0].tile, "P1"),
            (world.npcs.value[0].facing, "P1"),
            (world.npcs.value[0].target, "P1"),
            (world.telos.phase, "P2"),
            (world.telos.enrage, "P2"),
            (world.telos.streak, "P2"),
            (world.telos.meter, "P2"),
            (world.telos.freedom, "P2"),
            (world.game_tick, "P2"),
            (world.player.effects, "P3"),
            (world.hazards, "P4"),
            (world.ui.open, "P5"),
            (world.action_bar, "P5"),
        )
        for field, owner in fields:
            with self.subTest(owner=owner, field=field):
                self.assertIsNone(field.value)
                self.assertEqual(field.reason, "unmapped:" + owner)
        self.assertIsNone(world.abilities.value[0]["castable"])
        self.assertEqual(world.abilities.value[0]["cooldown"]["reason"], "timer_mapping_pending")

    def test_empty_partial_and_unavailable_are_different(self):
        first, second, unavailable, *_ = map(WorldState.from_telemetry, capture_rows())
        self.assertEqual(first.inventory.value, ())
        self.assertTrue(first.inventory.complete)
        self.assertEqual(second.npcs.value, ())
        self.assertFalse(second.npcs.complete)
        self.assertIsNone(second.inventory.value)
        self.assertIsNone(unavailable.player.health.current.value)
        self.assertEqual(unavailable.player.health.current.reason, "frame_unavailable")
        self.assertIsNone(unavailable.npcs.value)

    def test_snapshot_and_nested_containers_do_not_alias_the_input(self):
        row = capture_rows()[0]
        world = WorldState.from_telemetry(row)
        row["abilities"]["entries"][0]["cooldown"]["remaining_ms"] = 1234
        self.assertIsNone(world.abilities.value[0]["cooldown"]["remaining_ms"])
        with self.assertRaises(TypeError):
            world.abilities.value[0]["cooldown"]["remaining_ms"] = 1
        with self.assertRaises(TypeError):
            world.raw["combat"]["scene_epoch"] = 1
        with self.assertRaises(FrozenInstanceError):
            world.player.active_prayer.value = "magic"

    def test_events_keep_original_session_and_loss_counts(self):
        world = WorldState.from_telemetry(capture_rows()[1])
        self.assertEqual(world.events.npc_actions.lost.value, 2)
        hit = world.events.player_hits.records.value[0]
        self.assertEqual(hit["pid"], 110)
        self.assertEqual(hit["scene_epoch"], 2)
        self.assertNotEqual(hit["process_creation_time"], world.session.process_creation_time)
        missing = WorldState.from_telemetry(capture_rows()[2])
        self.assertIsNone(missing.events.npc_actions.lost.value)

    def test_legacy_capture_maps_only_explicit_values_with_unknown_age(self):
        world = WorldState.from_telemetry(capture_rows()[4])
        self.assertEqual(world.player.health.current.value, 8700)
        self.assertEqual(world.player.tile.value, Tile(3202, 3203, 0))
        self.assertEqual(world.npcs.value[0].animation.value["action_sequence"], 28929)
        self.assertIsNone(world.telos.phase.value)
        self.assertIsNone(world.player.prayer.current.value)
        self.assertFalse(world.player.health.current.is_fresh(1000))
        self.assertIsNone(world.npcs.value[0].health.current.value)
        self.assertFalse(world.npcs.complete)

    def test_future_observations_preserve_age_unit_and_false_values(self):
        row = capture_rows()[0]
        row["player"] = {
            "facing": {
                "value": {"angle": 123, "unit": "client_units"},
                "age_ms": 20,
                "source": "P1",
            },
            "target": {"value": {"kind": "npc", "instance_id": 99}, "source": "P1"},
            "effects": {"entries": {"value": [], "complete": True, "source": "P3"}},
        }
        row["telos"]["freedom"] = {"value": False, "source": "P2"}
        world = WorldState.from_telemetry(row)
        self.assertEqual(world.player.facing.value.unit, "client_units")
        self.assertEqual(world.player.facing.age_ms, 20)
        self.assertEqual(world.player.facing.source, "P1")
        self.assertEqual(world.player.target.value.instance_id, 99)
        self.assertEqual(world.player.effects.value, ())
        self.assertTrue(world.player.effects.complete)
        self.assertIs(world.telos.freedom.value, False)
        later = WorldState.from_telemetry(row, now_ns=10_500_000_000)
        self.assertEqual(later.player.facing.age_ms, 515)
        self.assertFalse(later.player.facing.is_fresh(100))

    def test_collection_provenance_applies_to_its_children(self):
        row = capture_rows()[0]
        row["npcs"]["entities"] = {
            "value": row["npcs"]["entities"],
            "age_ms": 500,
            "source": "older_npc_sample",
        }
        world = WorldState.from_telemetry(row)
        self.assertEqual(world.npcs.value[0].health.current.age_ms, 500)
        self.assertEqual(world.npcs.value[0].health.current.source, "older_npc_sample")
        self.assertFalse(world.npcs.value[0].health.current.is_fresh(100))

    def test_npc_health_keeps_untransmitted_reason_and_source(self):
        row = capture_rows()[0]
        row["npcs"]["entities"][0]["health"] = {
            "current": None,
            "max": None,
            "source": "npc_stats",
            "reason": "not_transmitted",
        }
        health = WorldState.from_telemetry(row).npcs.value[0].health.current
        self.assertIsNone(health.value)
        self.assertEqual(health.reason, "not_transmitted")
        self.assertEqual(health.source, "npc_stats")

    def test_unavailable_frame_does_not_drop_queued_historical_events(self):
        row = capture_rows()[1]
        row["status"] = "unavailable"
        world = WorldState.from_telemetry(row)
        self.assertIsNone(world.player.health.current.value)
        self.assertEqual(world.events.npc_actions.lost.value, 2)
        self.assertEqual(world.events.player_hits.records.value[0]["pid"], 110)

    def test_explicit_clock_and_no_carry_forward(self):
        world = WorldState.from_telemetry(capture_rows()[0], now_ns=10_500_000_000)
        self.assertEqual(world.player.health.current.age_ms, 500)
        missing = WorldState.from_telemetry({"schema_version": 1, "status": "partial"})
        self.assertIsNone(missing.player.active_prayer.value)
        self.assertIsNone(missing.player.adrenaline_percent.value)
        self.assertIsNone(missing.observed_ns)
        self.assertFalse(missing.session.complete)
        for kwargs in ({"now_ns": 1}, {"now_ns": -1}):
            with self.assertRaises(ValueError):
                WorldState.from_telemetry(capture_rows()[0], **kwargs)

    def test_unknown_observations_and_invalid_inputs(self):
        self.assertFalse(Observation().is_fresh(100))
        self.assertTrue(Observation(False, 0).known)
        for age in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Observation(1, age)
        with self.assertRaises(ValueError):
            Observation(complete=True)
        with self.assertRaises(ValueError):
            WorldState.from_telemetry({"schema_version": 2})


class IntentAndActuatorTests(unittest.TestCase):
    def test_payload_round_trip_includes_evidence_and_expected_slot_identity(self):
        intent = make_intent(ConsumeSlot(27, 123, ConsumeOperation.DRINK, 77), ActionLane.POTION)
        record = intent.to_record()
        self.assertEqual(record["kind"], "consume_slot")
        self.assertEqual(record["action"]["slot"], 27)
        self.assertEqual(record["action"]["expected_tracking_token"], 77)
        self.assertEqual(record["evidence"][0]["observation"]["source"], "synthetic_fixture")
        self.assertEqual(json.loads(json.dumps(record)), record)
        self.assertGreater(Priority.SURVIVAL, Priority.PRAYER)
        self.assertGreater(Priority.PRAYER, Priority.MECHANICS)
        self.assertGreater(Priority.MECHANICS, Priority.ROTATION)

    def test_fake_allows_food_potion_ability_prayer_at_the_same_time(self):
        proposals = (
            make_intent(ConsumeSlot(0, 101, "eat"), ActionLane.FOOD),
            make_intent(ConsumeSlot(1, 102, "drink"), ActionLane.POTION),
            make_intent(ActivateAbility(103), ActionLane.GCD),
            make_intent(ActivateAbility(104), ActionLane.OFF_GCD),
            make_intent(ActivateAbility(105), ActionLane.OFF_GCD),
            make_intent(),
        )
        fake = RecordingActuator()
        self.assertIsInstance(fake, Actuator)
        self.assertTrue(all(fake.send(intent).accepted for intent in proposals))
        self.assertEqual(len(fake.attempts), 6)
        self.assertEqual(len({attempt.result.sent_ns for attempt in fake.attempts}), 1)
        self.assertEqual(proposals[-1].evidence[0].observation.value, 8700)
        self.assertFalse(hasattr(fake.attempts[-1].result, "confirmed"))

    def test_deadline_and_rejection_are_recorded_without_confirming(self):
        intent = make_intent()
        fake = RecordingActuator(clock=lambda: intent.deadline_ns)
        self.assertTrue(fake.send(intent).accepted)
        late = RecordingActuator(clock=lambda: intent.deadline_ns + 1)
        self.assertEqual(late.send(intent).reason, "deadline_expired")
        self.assertEqual(len(late.attempts), 1)
        rejecting = RecordingActuator(accept=False)
        self.assertEqual(rejecting.send(intent).reason, "recording_rejected")
        fake.close()
        fake.close()
        self.assertEqual(fake.send(intent).reason, "closed")

    def test_invalid_actions_lanes_and_evidence_are_rejected(self):
        for action, lane in (
            (SetPrayer("magic"), ActionLane.GCD),
            (ConsumeSlot(0, 1, "drink"), ActionLane.FOOD),
        ):
            with self.assertRaises(ValueError):
                make_intent(action, lane)
        for action in (
            lambda: ConsumeSlot(28, 1, "eat"),
            lambda: ConsumeSlot(0.5, 1, "eat"),
            lambda: ActivateAbility(True),
            lambda: SetPrayer("unknown"),
        ):
            with self.assertRaises(ValueError):
                action()
        intent = make_intent()
        for kwargs in ({"evidence": ()}, {"deadline_ns": intent.issued_ns - 1}):
            with self.assertRaises(ValueError):
                replace(intent, **kwargs)
        self.assertEqual(make_intent(MoveTo(Tile(1, 2, 0)), ActionLane.POINTER).kind, "move_to")
        self.assertEqual(
            make_intent(
                Interact(EntityRef("npc", instance_id=1), "Attack"), ActionLane.POINTER
            ).kind,
            "interact",
        )


if __name__ == "__main__":
    unittest.main()
