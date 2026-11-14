"""A2 deterministic fixtures. No DLL, mouse, keyboard or game access."""

import unittest
from dataclasses import replace

from rs3_reader.bot import ActionLane, EntityRef, Observation, SessionIdentity, Tile, WorldState
from rs3_reader.bot.movement import (
    MinimapCalibration,
    MinimapRegion,
    MinimapSample,
    MovementPolicy,
    MovementPrimitives,
)

SESSION = SessionIdentity(123, 456, 7)
ORIGIN = Tile(3200, 3200, 0)
DESTINATION = Tile(3201, 3201, 0)
NPC = EntityRef("npc", 9, 42, 22891)
ABILITY_ID = 999
START_NS = 10_000_000_000


def world(
    *,
    offset_ms=0,
    sequence=1,
    tile=ORIGIN,
    target="none",
    session=SESSION,
    castable=True,
    cooldown=0,
    extra=None,
):
    timestamp = START_NS + offset_ms * 1_000_000
    if isinstance(target, EntityRef):
        target = {
            "kind": target.kind,
            "instance_id": target.instance_id,
            "runtime_id": target.runtime_id,
            "config_id": target.config_id,
        }
    elif target == "none":
        target = {"kind": "none"}
    record = {
        "schema_version": 1,
        "status": "partial",
        "source": "synthetic_native_fixture",
        "pid": session.pid,
        "process_creation_time": session.process_creation_time,
        "executable_sha256": "a" * 64,
        "monotonic_ms": timestamp / 1_000_000,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": session.scene_epoch},
        "player": {
            "tile": {"value": vars(tile), "age_ms": 0},
            "target": {"value": target, "age_ms": 0},
            "facing": {"value": {"angle": 0, "unit": "degrees"}, "age_ms": 0},
        },
        "npcs": {
            "status": "available",
            "entities": [
                {
                    "instance_id": NPC.instance_id,
                    "runtime_id": NPC.runtime_id,
                    "config_id": NPC.config_id,
                    "tile": vars(DESTINATION),
                }
            ],
        },
        "abilities": {
            "status": "available",
            "entries": [
                {
                    "struct_id": ABILITY_ID,
                    "castable": castable,
                    "cooldown": {"remaining_ms": cooldown, "reason": None},
                }
            ],
        },
    }
    record.update(extra or {})
    return WorldState.from_telemetry(record)


def region(timestamp=START_NS, **changes):
    return replace(
        MinimapRegion(
            SESSION, timestamp, "fixture-view", 400, 200, 40, "synthetic_native_UI", True
        ),
        **changes,
    )


def samples():
    # Four fit points and two independent held-out arrivals; rotated 3/4-pixel axes.
    return tuple(
        MinimapSample(
            SESSION,
            "fixture-view",
            ORIGIN,
            Tile(ORIGIN.x + east, ORIGIN.y + north, 0),
            400 + 3 * east + 4 * north,
            200 + 4 * east - 3 * north,
            "synthetic_confirmed_arrival",
            True,
        )
        for east, north in ((1, 0), (0, 1), (-1, 0), (0, -1), (2, 1), (-1, 2))
    )


def primitives(**changes):
    options = dict(
        calibration=MinimapCalibration.fit(samples(), region()),
        region_provider=lambda current: region(current.observed_ns),
        path_validator=lambda current, tile: True,
        interaction_validator=lambda action, current: True,
        ability_validator=lambda current, ability, destination: True,
    )
    options.update(changes)
    return MovementPrimitives(**options)


class CalibrationTests(unittest.TestCase):
    def test_fits_rotation_and_scale_from_confirmed_arrivals(self):
        fit = MinimapCalibration.fit(samples(), region())
        self.assertEqual((fit.east_dx, fit.east_dy, fit.north_dx, fit.north_dy), (3, 4, 4, -3))
        self.assertEqual(fit.project(ORIGIN, DESTINATION, region()), (407, 201))
        # The player can move while the observed view/zoom/UI signature remains fixed.
        self.assertEqual(fit.project(DESTINATION, Tile(3202, 3202, 0), region()), (407, 201))

    def test_rejects_unconfirmed_or_foreign_samples(self):
        for replacement in (
            replace(samples()[0], confirmed=False),
            replace(samples()[0], view_signature="changed"),
            replace(samples()[0], session=SessionIdentity(124, 456, 7)),
        ):
            with self.subTest(sample=replacement), self.assertRaises(ValueError):
                MinimapCalibration.fit((replacement,) + samples()[1:], region())
        with self.assertRaises(ValueError):
            MinimapCalibration.fit(samples(), region(validated=False))
        with self.assertRaises(ValueError):
            MinimapCalibration.fit(samples()[:5], region())

    def test_collinear_and_failed_holdout_are_not_calibrations(self):
        collinear = tuple(
            replace(sample, arrived=Tile(3201 + index, 3200, 0))
            for index, sample in enumerate(samples())
        )
        with self.assertRaises(ValueError):
            MinimapCalibration.fit(collinear, region())
        bad = samples()[:-1] + (replace(samples()[-1], screen_x=410),)
        with self.assertRaises(ValueError):
            MinimapCalibration.fit(bad, region())

    def test_invalid_geometry_provenance_is_rejected(self):
        for changes in ({"source": True}, {"view_signature": True}, {"source": " "}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                region(**changes)
        with self.assertRaises(ValueError):
            MinimapCalibration.fit((replace(samples()[0], source=True),) + samples()[1:], region())

    def test_fit_uncertainty_cannot_cross_a_tile_and_negative_pixels_match_C_round(self):
        fit = MinimapCalibration.fit(samples(), region())
        with self.assertRaises(ValueError):
            replace(fit, east_dx=1, east_dy=0, north_dx=0, north_dy=-1)
        with self.assertRaises(ValueError):
            replace(fit, maximum_error_pixels=2)
        negative = replace(
            fit,
            region=region(center_x=-400),
            east_dx=-3.5,
            east_dy=0,
            north_dx=0,
            north_dy=-4,
            maximum_error_pixels=0,
        )
        self.assertEqual(
            negative.project(ORIGIN, Tile(3201, 3200, 0), negative.region), (-404, 200)
        )

    def test_projection_rejects_changed_UI_plane_bounds_and_nan(self):
        fit = MinimapCalibration.fit(samples(), region())
        self.assertIsNone(fit.project(ORIGIN, DESTINATION, region(view_signature="new-zoom")))
        self.assertIsNone(fit.project(ORIGIN, Tile(3201, 3201, 1), region()))
        self.assertIsNone(fit.project(ORIGIN, Tile(3250, 3250, 0), region()))
        with self.assertRaises(ValueError):
            replace(fit, east_dx=float("nan"))


class MovementTests(unittest.TestCase):
    def test_unknown_dependencies_never_propose(self):
        controller = MovementPrimitives()
        self.assertIsNone(controller.move_to(world(), DESTINATION))
        self.assertIsNone(controller.select_target(world(), NPC.instance_id))
        self.assertIsNone(controller.click_object(world(), DESTINATION))
        self.assertEqual(controller.step(world()), ())

    def test_move_waits_for_newer_observation_after_actual_acceptance(self):
        controller = primitives()
        before = world()
        intent = controller.move_to(before, DESTINATION)
        self.assertEqual(controller.step(before), (intent,))
        arrived = world(offset_ms=100, sequence=2, tile=DESTINATION)
        self.assertIsNone(controller.confirm(intent, before, arrived).value)
        controller.note_sent(intent.intent_id, before.now_ns)
        self.assertIsNone(controller.confirm(intent, before, before).value)
        self.assertFalse(controller.confirm(intent, before, world(offset_ms=100, sequence=2)).value)
        result = controller.observe(arrived)
        self.assertEqual(result[0].status, "confirmed")
        self.assertEqual(controller.step(arrived), ())

    def test_move_rechecks_view_path_and_already_arrived(self):
        controller = primitives()
        intent = controller.move_to(world(), DESTINATION)
        self.assertTrue(controller.pointer_validator(intent, world()))
        controller.region_provider = lambda current: region(view_signature="camera-moved")
        self.assertFalse(controller.can_send(intent, world()))
        controller = primitives(path_validator=lambda current, tile: None)
        self.assertIsNone(controller.move_to(world(), DESTINATION))
        self.assertIsNone(primitives().move_to(world(), ORIGIN))

    def test_unknown_target_cannot_confirm_or_authorize_entity_click(self):
        controller = primitives()
        self.assertIsNone(controller.select_target(world(target=None), NPC.instance_id))
        before = world()
        intent = controller.select_target(before, NPC.instance_id)
        controller.note_sent(intent.intent_id, before.now_ns)
        self.assertFalse(controller.confirm(intent, before, world(offset_ms=100, sequence=2)).value)
        after = world(offset_ms=100, sequence=2, target=NPC)
        self.assertTrue(controller.confirm(intent, before, after).value)
        self.assertEqual(controller.observe(after)[0].status, "confirmed")

    def test_target_runtime_or_config_change_invalidates_before_send(self):
        controller = primitives()
        before = world()
        intent = controller.select_target(before, NPC.instance_id)
        npc = replace(before.npcs.value[0], runtime_id=Observation(43, 0, "fixture"))
        after = replace(before, npcs=Observation((npc,), 0, "fixture", complete=True))
        self.assertFalse(controller.can_send(intent, after))
        self.assertIsNone(controller.select_target(world(target=NPC), NPC.instance_id))

    def test_timeout_is_explicit_and_latches_fault_without_retry(self):
        controller = primitives()
        before = world()
        intent = controller.move_to(before, DESTINATION)
        controller.note_sent(intent.intent_id, before.now_ns)
        after = world(offset_ms=3001, sequence=10)
        outcome = controller.observe(after)[0]
        self.assertEqual(outcome.reason, "movement_confirmation_timeout")
        self.assertIsNone(controller.move_to(after, DESTINATION))
        with self.assertRaises(RuntimeError):
            controller.step(after)

    def test_scene_change_and_clock_regression_are_faults(self):
        for after in (
            world(offset_ms=100, sequence=2, session=SessionIdentity(123, 456, 8)),
            world(offset_ms=-1, sequence=2),
        ):
            controller = primitives()
            intent = controller.move_to(world(), DESTINATION)
            controller.note_sent(intent.intent_id, START_NS)
            self.assertEqual(controller.observe(after)[0].status, "failed")

    def test_stale_pose_and_unknown_ages_fail(self):
        before = world()
        stale = replace(before.player, tile=Observation(ORIGIN, 151, "fixture"))
        self.assertIsNone(primitives().move_to(replace(before, player=stale), DESTINATION))
        unknown = replace(before.player, tile=Observation(ORIGIN, None, "fixture"))
        self.assertIsNone(primitives().move_to(replace(before, player=unknown), DESTINATION))
        controller = primitives(region_provider=lambda current: region(START_NS - 151_000_000))
        self.assertIsNone(controller.move_to(before, DESTINATION))

    def test_unknown_snapshot_age_cannot_be_overridden_by_a_fresh_field(self):
        current = world(extra={"native": {"sample_sequence": 1, "snapshot_age_ms": None}})
        self.assertEqual(current.player.tile.age_ms, 0)
        self.assertIsNone(primitives().move_to(current, DESTINATION))
        self.assertIsNone(primitives().select_target(current, NPC.instance_id))

    def test_bounded_proposals_rejections_and_duplicate_sends(self):
        controller = primitives(policy=MovementPolicy(maximum_pending=2))
        first = controller.move_to(world(), DESTINATION)
        second = controller.move_to(world(), Tile(3202, 3201, 0))
        self.assertIsNotNone(second)  # This is not a one-input-per-tick limit.
        self.assertIsNone(controller.move_to(world(), Tile(3203, 3201, 0)))
        controller.note_rejected(first.intent_id, "fixture_rejection", START_NS)
        self.assertIsNotNone(controller.move_to(world(), Tile(3203, 3201, 0)))
        controller.note_sent(second.intent_id, START_NS)
        with self.assertRaises(ValueError):
            controller.note_sent(second.intent_id, START_NS)

    def test_movement_ability_requires_castability_lane_and_actual_displacement(self):
        controller = primitives()
        self.assertIsNone(controller.use_movement_ability(world(), ABILITY_ID, DESTINATION))
        self.assertIsNone(
            controller.use_movement_ability(
                world(castable=None), ABILITY_ID, DESTINATION, lane=ActionLane.OFF_GCD
            )
        )
        before = world()
        intent = controller.use_movement_ability(
            before, ABILITY_ID, DESTINATION, lane=ActionLane.OFF_GCD
        )
        controller.note_sent(intent.intent_id, START_NS)
        # A cooldown only proves an activation, never that a movement ability reached its tile.
        after = world(offset_ms=100, sequence=2, cooldown=1000)
        self.assertFalse(controller.confirm(intent, before, after).value)
        arrived = world(offset_ms=200, sequence=3, cooldown=900, tile=DESTINATION)
        self.assertEqual(controller.observe(arrived)[0].status, "confirmed")

    def test_object_interaction_requires_current_identity_and_explicit_confirmation(self):
        reference = EntityRef("object", 11, None, 12345)
        controller = primitives(
            object_provider=lambda current, query: reference,
            object_confirmation=lambda intent, before, after: after.raw.get("fixture_object_ack")
            is True,
        )
        before = world()
        intent = controller.click_object(before, DESTINATION, option="fixture_interact")
        controller.note_sent(intent.intent_id, START_NS)
        self.assertFalse(controller.confirm(intent, before, world(offset_ms=100, sequence=2)).value)
        after = world(offset_ms=200, sequence=3, extra={"fixture_object_ack": True})
        self.assertEqual(controller.observe(after)[0].status, "confirmed")

    def test_twenty_synthetic_moves_and_twenty_target_switches_confirm(self):
        controller = primitives()
        for index in range(20):
            before = world(offset_ms=index * 200, sequence=index * 2 + 1)
            intent = controller.move_to(before, DESTINATION)
            controller.note_sent(intent.intent_id, before.now_ns)
            after = world(offset_ms=index * 200 + 100, sequence=index * 2 + 2, tile=DESTINATION)
            self.assertEqual(controller.observe(after)[0].status, "confirmed")
        controller = primitives()
        for index in range(20):
            before = world(offset_ms=index * 200, sequence=index * 2 + 1)
            intent = controller.select_target(before, NPC.instance_id)
            controller.note_sent(intent.intent_id, before.now_ns)
            after = world(offset_ms=index * 200 + 100, sequence=index * 2 + 2, target=NPC)
            self.assertEqual(controller.observe(after)[0].status, "confirmed")


if __name__ == "__main__":
    unittest.main()
