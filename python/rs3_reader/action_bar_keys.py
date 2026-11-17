"""Resolve ability and prayer keybinds from the DLL's decoded action bars.

The DLL decodes each displayed action-bar slot from server variables: a packed (content type,
content id) and a raw game key code. This module turns those into displayed key labels, so a
controller can find, for example, the key of Freedom without the operator typing it.

Two calibrations were made on 2026-11-14 against the account owner's on-screen bar labels
(Compatibility Mode 6ada3f59):

* Game key codes follow keyboard rows: F1-F12 from 3, digits 1-9 from 16 (0 after 9), the
  Q row from 32, the A row from 48 and the Z row from 64. 255 means unbound.
* The DLL's ability-name match is wrong for these bars (Anticipation was reported as Runic
  Charge), so slots are identified by their raw (content type, content id) pair instead.

The raw modifier bits disagreed with play (Soul Split reported modifier 2, yet plain F4
works), so modifiers are reported but never added to a resolved chord. Callers must still
confirm every press, for example through the cooldown or overhead it should change.
"""

from __future__ import annotations

from dataclasses import dataclass

# enum rs3_action_bar_flags in include/rs3/interface.h.
BAR_PACKED_KNOWN = 2
BAR_KEY_KNOWN = 64
UNBOUND_KEY = 255
FUNCTION_KEY_FIRST_CODE = 3
FUNCTION_KEY_COUNT = 12
DIGIT_FIRST_CODE = 16
KEYBOARD_ROWS = {32: "QWERTYUIOP", 48: "ASDFGHJKL", 64: "ZXCVBNM"}


def _game_key_labels():
    labels = {
        FUNCTION_KEY_FIRST_CODE + index: f"F{index + 1}" for index in range(FUNCTION_KEY_COUNT)
    }
    labels.update({DIGIT_FIRST_CODE + index: str(index + 1) for index in range(9)})
    labels[DIGIT_FIRST_CODE + 9] = "0"
    for first_code, letters in KEYBOARD_ROWS.items():
        labels.update({first_code + index: letter for index, letter in enumerate(letters)})
    return labels


GAME_KEY_LABELS = _game_key_labels()

# (content type, content id) -> name, identified from the account owner's bars.
SLOT_CONTENT_NAMES = {
    (3, 1): "Anticipation",
    (3, 2): "Freedom",
    (3, 4): "Resonance",
    (4, 29): "Dive",
    (5, 3): "Escape",
    (7, 38): "Deflect Magic",
    (7, 39): "Deflect Ranged",
    (7, 40): "Deflect Melee",
    (7, 49): "Soul Split",
}


@dataclass(frozen=True)
class SlotBinding:
    """One displayed slot's content and key, with its raw codes for the log."""

    name: str
    key_label: str
    display_bar: int
    preset_bar: int
    slot: int
    key_code: int
    modifier_code: int


def key_label(key_code):
    """Return the displayed key label for a game key code, or None when unknown/unbound."""
    if key_code == UNBOUND_KEY:
        return None
    return GAME_KEY_LABELS.get(key_code)


def displayed_bindings(reader):
    """Return {name: SlotBinding} for known contents on the displayed bars of the last sample.

    Only slots whose packed content and key were read are used. When a name appears on
    several displayed bars, the first bar wins, matching the order the bars are shown.
    """
    info = reader.interface_info
    found = {}
    for slot in reader.action_bar[: info.action_bar_count]:
        if not slot.flags & BAR_PACKED_KNOWN or not slot.flags & BAR_KEY_KNOWN:
            continue
        name = SLOT_CONTENT_NAMES.get((slot.content_type, slot.content_id))
        label = key_label(slot.key_code)
        if name is None or label is None or name in found:
            continue
        found[name] = SlotBinding(
            name=name,
            key_label=label,
            display_bar=slot.display_bar,
            preset_bar=slot.preset_bar,
            slot=slot.slot,
            key_code=slot.key_code,
            modifier_code=slot.modifier_code,
        )
    return found
