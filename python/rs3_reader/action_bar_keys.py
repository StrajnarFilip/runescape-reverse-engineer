"""Resolve current bindings from guarded C UI labels, preserving Ctrl/Shift/Alt.

The old Compatibility Mode calibration is explicitly opt-in. Its opaque raw
modifier codes are not the native UI accessor's decoded modifier mask, and its
unmodified F-keys must never become defaults for another account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# enum rs3_action_bar_flags in include/rs3/interface.h.
BAR_PACKED_KNOWN = 2
BAR_KEY_KNOWN = 64
UNBOUND_KEY = 255
FUNCTION_KEY_FIRST_CODE = 3
FUNCTION_KEY_COUNT = 12
DIGIT_FIRST_CODE = 16
KEYBOARD_ROWS = {32: "QWERTYUIOP", 48: "ASDFGHJKL", 64: "ZXCVBNM"}
DISPLAY_BARS = 5
SLOTS_PER_BAR = 14
MAXIMUM_PRESET_BARS = 18
LABEL_FLAGS_REQUIRED = 15
MAXIMUM_BINDING_AGE_NS = 150_000_000
COMPATIBILITY_SHA256 = "7a60c69fcf11eda42a328732721f5e89e1c85a6bc919038ff3451d4643e42dbf"
CTRL, SHIFT, ALT = 1, 2, 4  # rs3_key_modifiers from the C UI-label accessor.
VIRTUAL_KEY_NAMES = {0x21: "PageUp", 0x22: "PageDown", 0x2D: "Insert", 0xBB: "Equals"}
VIRTUAL_KEY_NAMES.update({0x6F + number: f"F{number}" for number in range(1, 13)})
VIRTUAL_KEY_NAMES.update({ord(value): value for value in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})


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
    key_code: Optional[int]
    modifier_code: Optional[int]
    source: str = "legacy_compatibility_calibration"
    virtual_key: Optional[int] = None
    modifiers: Optional[int] = None
    pid: Optional[int] = None
    process_creation_time: Optional[int] = None
    sample_sequence: Optional[int] = None
    observed_ns: Optional[int] = None
    item_id: Optional[int] = None
    sprite_id: Optional[int] = None


def key_label(key_code):
    """Return the displayed key label for a game key code, or None when unknown/unbound."""
    if key_code == UNBOUND_KEY:
        return None
    return GAME_KEY_LABELS.get(key_code)


def native_key_label(virtual_key, modifiers):
    """Translate a decoded C chord mask, never an opaque VarClient modifier code."""
    if type(modifiers) is not int or modifiers < 0 or modifiers & ~(CTRL | SHIFT | ALT):
        return None
    label = VIRTUAL_KEY_NAMES.get(virtual_key)
    if label is None:
        return None
    prefixes = [
        name for bit, name in ((CTRL, "Ctrl"), (SHIFT, "Shift"), (ALT, "Alt")) if modifiers & bit
    ]
    return "+".join(prefixes + [label])


def _native_binding(reader, display_bar, slot):
    label = reader.read_action_label(display_bar, slot)
    info = reader.info
    now = reader.monotonic_ns()
    if (
        label.flags != LABEL_FLAGS_REQUIRED
        or label.reason != 0
        or label.display_bar != display_bar
        or label.slot != slot
        or not 1 <= label.preset_bar <= MAXIMUM_PRESET_BARS
        or (label.pid, label.process_creation_time, label.sample_sequence)
        != (info.pid, info.process_creation_time, info.sequence)
        or not label.pid
        or not label.process_creation_time
        or not 0 < label.observed_ns <= now
        or now - label.observed_ns > MAXIMUM_BINDING_AGE_NS
        or not 0 < info.monotonic_ns <= now
        or now - info.monotonic_ns > MAXIMUM_BINDING_AGE_NS
    ):
        return None
    name = label.name.decode("utf-8", "strict")
    chord = native_key_label(label.virtual_key, label.modifiers)
    if not name or chord is None:
        return None
    # The UI's action name omits dose counts, unlike inventory item names.
    if name == "Super restore":
        name = "Super Restore"
    return SlotBinding(
        name,
        chord,
        display_bar,
        label.preset_bar,
        slot,
        None,
        None,
        source="native_ui_label",
        virtual_key=label.virtual_key,
        modifiers=label.modifiers,
        pid=label.pid,
        process_creation_time=label.process_creation_time,
        sample_sequence=label.sample_sequence,
        observed_ns=label.observed_ns,
        item_id=label.item_id,
        sprite_id=label.sprite_id,
    )


def displayed_bindings(reader, *, legacy_unmodified=False):
    """Return current roles and chords; unavailable labels never trigger a raw-key fallback.

    Multiple bound copies of the same role choose the first displayed slot.
    The old unmodified-key calibration requires the explicit option and exact
    Compatibility Mode executable. It is not fresh native UI-label evidence.
    """
    if not legacy_unmodified:
        found = {}
        for bar in range(1, DISPLAY_BARS + 1):
            for slot in range(1, SLOTS_PER_BAR + 1):
                try:
                    binding = _native_binding(reader, bar, slot)
                except (RuntimeError, UnicodeError):
                    continue
                if binding is not None:
                    found.setdefault(binding.name, binding)
        return found
    digest = reader.info.executable_sha256.decode("ascii", "strict")
    if digest != COMPATIBILITY_SHA256:
        raise ValueError("Legacy unmodified keys require the calibrated Compatibility Mode profile")
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


def refresh_binding(reader, binding):
    """Re-read a native role before dispatch; return None for a changed chord or identity.

    Item contents are compared exactly. A changed item requires a new policy
    selection, so an equip/special cannot execute through an old item binding.
    A newer native sample is allowed only within the same process lifetime.
    """
    if binding.source != "native_ui_label":
        return None
    try:
        current = _native_binding(reader, binding.display_bar, binding.slot)
    except (RuntimeError, UnicodeError):
        return None
    fields = (
        "name",
        "key_label",
        "display_bar",
        "preset_bar",
        "slot",
        "pid",
        "process_creation_time",
        "item_id",
        "sprite_id",
    )
    if current is None or any(
        getattr(current, field) != getattr(binding, field) for field in fields
    ):
        return None
    if current.sample_sequence < binding.sample_sequence:
        return None
    return current
