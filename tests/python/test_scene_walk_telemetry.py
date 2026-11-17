"""Ground hover observations never imply a click or a navigation route."""

import unittest
from types import SimpleNamespace

from rs3_reader.native import PointerWalk, Position
from rs3_reader.telemetry import scene_walk_record


class SceneWalkTelemetryTests(unittest.TestCase):
    def test_serializes_native_tile_cycle_and_action_without_claiming_click_permission(self):
        walk = PointerWalk(valid=1, action=23, menu_cycle=44, tile=Position(1, 3005, 3006, 1))
        record = scene_walk_record(SimpleNamespace(pointer_walk=walk))
        self.assertEqual(record["value"], {"x": 3005, "y": 3006, "plane": 1})
        self.assertEqual(record["menu_cycle"], 44)
        self.assertIsNone(record["reason"])
        self.assertFalse(record["click_permission"])

    def test_cleared_nonwalk_and_minimap_observations_keep_the_scene_tile_unknown(self):
        for valid, action, tile_valid in ((0, 0, 0), (0, 23, 1), (1, 3, 1), (1, 23, 0)):
            walk = PointerWalk(
                valid=valid, action=action, menu_cycle=45, tile=Position(tile_valid, 3005, 3006, 1)
            )
            with self.subTest(valid=valid, action=action, tile_valid=tile_valid):
                record = scene_walk_record(SimpleNamespace(pointer_walk=walk))
                self.assertIsNone(record["value"])
                self.assertEqual(record["reason"], "default_scene_walk_unavailable")
                self.assertFalse(record["click_permission"])


if __name__ == "__main__":
    unittest.main()
