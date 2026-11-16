"""Manual calibration records explicit chords and keeps buff state unknown."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from rs3_reader.native import Ability, AbilityFlags

WORK_DIRECTORY = Path(__file__).resolve().parents[2] / "work"
sys.path.insert(0, str(WORK_DIRECTORY))
try:
    import capture_prayer as capture
    from capture_local_combat import PrayerKeys
finally:
    sys.path.remove(str(WORK_DIRECTORY))


class CalibrationCaptureTests(unittest.TestCase):
    def test_unobserved_timer_stays_unknown_despite_metadata(self):
        ability = Ability(
            struct_id=capture.DEVOTION_STRUCT_ID,
            flags=AbilityFlags.IN_BOOK,
            start_tick=100,
            end_tick=3100,
        )
        reader = SimpleNamespace(
            abilities=[ability], inventory_info=SimpleNamespace(ability_count=1)
        )
        result = capture.devotion_cooldown_record(reader)
        self.assertFalse(result["timer_valid"])
        self.assertIsNone(result["start_tick"])
        self.assertIsNone(result["end_tick"])
        self.assertIsNone(result["buff_active"])

    def test_cooldown_evidence_does_not_claim_an_active_buff(self):
        ability = Ability(
            struct_id=capture.DEVOTION_STRUCT_ID,
            flags=AbilityFlags.TIMER_VALID,
            start_tick=100,
            end_tick=3100,
        )
        reader = SimpleNamespace(
            abilities=[ability], inventory_info=SimpleNamespace(ability_count=1)
        )
        result = capture.devotion_cooldown_record(reader)
        self.assertTrue(result["timer_valid"])
        self.assertEqual((result["start_tick"], result["end_tick"]), (100, 3100))
        self.assertIsNone(result["buff_active"])

    def test_alt_g_requires_explicit_configuration_focus_and_a_fresh_edge(self):
        binding = {"devotion": {"virtual_key": capture.VK_G, "modifiers": capture.ALT_MODIFIER}}
        with self.assertRaises(ValueError):
            PrayerKeys(1, binding)
        keys = PrayerKeys(1, binding, additional_virtual_keys=(capture.VK_G,))
        keys.game_focused = Mock(return_value=True)
        held = set()
        keys.get_async_key_state = lambda key: -32768 if key in held else 0
        clock = Mock(side_effect=range(100, 1000, 10))
        self.assertEqual(keys.sample(clock), [])
        held.update((0x12, capture.VK_G))
        self.assertEqual(keys.sample(clock)[0]["prayer"], "devotion")
        self.assertEqual(keys.sample(clock), [])
        held.clear()
        keys.sample(clock)
        held.add(capture.VK_G)
        self.assertEqual(keys.sample(clock), [])
        held.add(0x12)
        self.assertEqual(keys.sample(clock), [])  # Adding Alt to a held G is not a new edge.
        held.clear()
        keys.sample(clock)
        keys.game_focused.return_value = False
        held.update((0x12, capture.VK_G))
        self.assertEqual(keys.sample(clock), [])
        keys.game_focused.return_value = True
        self.assertEqual(keys.sample(clock), [])  # Focus gain establishes a baseline.


if __name__ == "__main__":
    unittest.main()
