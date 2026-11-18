"""Check game key-code translation and slot identification without a live client."""

import unittest
from types import SimpleNamespace

from rs3_reader.action_bar_keys import (
    BAR_KEY_KNOWN,
    BAR_PACKED_KNOWN,
    COMPATIBILITY_SHA256,
    displayed_bindings,
    key_label,
    native_key_label,
    refresh_binding,
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
            info=SimpleNamespace(executable_sha256=COMPATIBILITY_SHA256.encode()),
            interface_info=SimpleNamespace(action_bar_count=len(slots)),
            action_bar=slots,
        )
        bindings = displayed_bindings(reader, legacy_unmodified=True)
        self.assertEqual(
            {name: binding.key_label for name, binding in bindings.items()},
            {"Anticipation": "T", "Freedom": "Z", "Deflect Melee": "F3"},
        )


class NativeBindingTests(unittest.TestCase):
    def reader(self, name="Soul Split", virtual_key=0x72, modifiers=1):
        info = SimpleNamespace(pid=50, process_creation_time=70, sequence=9, monotonic_ns=1000)
        label = SimpleNamespace(
            name=name.encode(),
            flags=15,
            reason=0,
            display_bar=2,
            preset_bar=6,
            slot=3,
            pid=50,
            process_creation_time=70,
            sample_sequence=9,
            observed_ns=1000,
            virtual_key=virtual_key,
            modifiers=modifiers,
            item_id=-1,
            sprite_id=11748,
        )

        def read_label(bar, number):
            if (bar, number) != (2, 3):
                raise RuntimeError("No live label")
            return label

        reader = SimpleNamespace(info=info, read_action_label=read_label, monotonic_ns=lambda: 2000)
        return reader, label

    def test_native_chords_preserve_modifiers_and_special_keys(self):
        for virtual, modifiers, expected in (
            (0x72, 1, "Ctrl+F3"),
            (0x73, 1, "Ctrl+F4"),
            (ord("G"), 4, "Alt+G"),
            (ord("F"), 2, "Shift+F"),
            (ord("A"), 3, "Ctrl+Shift+A"),
            (0x21, 0, "PageUp"),
            (0x22, 0, "PageDown"),
            (0x2D, 0, "Insert"),
            (0xBB, 0, "Equals"),
        ):
            self.assertEqual(native_key_label(virtual, modifiers), expected)
        self.assertIsNone(native_key_label(0x72, 8))
        self.assertIsNone(native_key_label(0x72, -1))
        self.assertIsNone(native_key_label(0, 0))

    def test_native_ui_wins_over_other_accounts_raw_modifier_calibration(self):
        reader, _ = self.reader()
        # The old account's raw values must not turn this account's Ctrl+F3
        # into its plain F4 or the compact metadata's wrong ability name.
        reader.action_bar = [slot(2, 3, (7, 49), 6)]
        reader.action_bar[0].modifier_code = 2
        binding = displayed_bindings(reader)["Soul Split"]
        self.assertEqual(binding.key_label, "Ctrl+F3")
        self.assertEqual(binding.modifiers, 1)
        self.assertEqual(binding.source, "native_ui_label")
        self.assertIsNone(binding.modifier_code)
        self.assertEqual(
            (binding.pid, binding.process_creation_time, binding.sample_sequence), (50, 70, 9)
        )

    def test_unavailable_wrong_identity_or_stale_label_never_falls_back(self):
        mutations = (
            ("flags", 7),
            ("reason", 5),
            ("preset_bar", 0),
            ("preset_bar", 19),
            ("display_bar", 1),
            ("slot", 4),
            ("pid", 51),
            ("process_creation_time", 71),
            ("sample_sequence", 8),
            ("sample_sequence", 10),
            ("observed_ns", 0),
            ("observed_ns", 3000),
            ("modifiers", 8),
        )
        for field, value in mutations:
            reader, label = self.reader()
            setattr(label, field, value)
            with self.subTest(field=field, value=value):
                self.assertEqual(displayed_bindings(reader), {})
        reader, label = self.reader()
        reader.monotonic_ns = lambda: 200_000_000
        self.assertEqual(displayed_bindings(reader), {})

    def test_new_sample_can_refresh_but_changed_role_chord_item_or_lifetime_cannot(self):
        reader, label = self.reader()
        binding = displayed_bindings(reader)["Soul Split"]
        reader.info.sequence = label.sample_sequence = 10
        self.assertEqual(refresh_binding(reader, binding).sample_sequence, 10)
        for field, value in (
            ("name", b"Deflect Melee"),
            ("virtual_key", 0x73),
            ("modifiers", 0),
            ("preset_bar", 18),
            ("item_id", 51065),
            ("sprite_id", 1),
        ):
            original = getattr(label, field)
            setattr(label, field, value)
            with self.subTest(field=field):
                self.assertIsNone(refresh_binding(reader, binding))
            setattr(label, field, original)
        reader.info.process_creation_time = label.process_creation_time = 71
        self.assertIsNone(refresh_binding(reader, binding))

    def test_legacy_calibration_requires_the_exact_profile_and_explicit_option(self):
        reader, _ = self.reader()
        reader.info.executable_sha256 = b"f" * 64
        with self.assertRaisesRegex(ValueError, "Compatibility Mode"):
            displayed_bindings(reader, legacy_unmodified=True)


if __name__ == "__main__":
    unittest.main()
