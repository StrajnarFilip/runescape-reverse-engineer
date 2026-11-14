"""Check game key-code translation and slot identification without a live client."""

import unittest
from types import SimpleNamespace

from rs3_reader.action_bar_keys import (
    BAR_KEY_KNOWN,
    BAR_PACKED_KNOWN,
    displayed_bindings,
    key_label,
)


def slot(display_bar, number, content, key_code, flags=BAR_PACKED_KNOWN | BAR_KEY_KNOWN):
    return SimpleNamespace(
        display_bar=display_bar,
        preset_bar=18,
        slot=number,
        flags=flags,
        content_type=content[0],
        content_id=content[1],
        key_code=key_code,
        modifier_code=0,
    )


class KeyLabelTests(unittest.TestCase):
    def test_codes_calibrated_from_the_account_bars(self):
        # Observed 2026-11-14: bar 18 "1".."9", A, S, T, Z, U and bar 9 F1..F5, I, O, P, H..L.
        observed = {16: "1", 24: "9", 48: "A", 49: "S", 36: "T", 64: "Z", 38: "U", 3: "F1"}
        observed.update({7: "F5", 39: "I", 40: "O", 41: "P", 53: "H", 56: "L"})
        for code, label in observed.items():
            self.assertEqual(key_label(code), label)
        self.assertIsNone(key_label(255))
        self.assertIsNone(key_label(200))


class DisplayedBindingTests(unittest.TestCase):
    def test_first_displayed_known_slot_wins_and_unknowns_are_skipped(self):
        slots = [
            slot(1, 12, (3, 1), 36),
            slot(1, 13, (3, 2), 64),
            slot(2, 1, (3, 2), 16),
            slot(3, 3, (7, 40), 5),
            slot(3, 6, (3, 14), 39),
            slot(3, 7, (3, 4), 255),
            slot(3, 8, (4, 29), 41, flags=BAR_PACKED_KNOWN),
        ]
        reader = SimpleNamespace(
            interface_info=SimpleNamespace(action_bar_count=len(slots)), action_bar=slots
        )
        bindings = displayed_bindings(reader)
        self.assertEqual(
            {name: binding.key_label for name, binding in bindings.items()},
            {"Anticipation": "T", "Freedom": "Z", "Deflect Melee": "F3"},
        )


if __name__ == "__main__":
    unittest.main()
