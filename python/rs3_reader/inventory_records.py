"""Presentation-only conversion of native inventory, equipment and ability records."""

from __future__ import annotations

from .native import (
    AbilityFlags,
    Container,
    EffectFlags,
    EquipmentFlags,
    EquipmentMatch,
    InventoryFlags,
    ItemChange,
    ItemFlags,
    Reason,
    Requirements,
    Status,
    decode_text,
)

STYLE_NAMES = {0: None, 1: "melee", 2: "ranged", 3: "magic", 4: "necromancy"}
COOLDOWN_REASON_NAMES = {
    Reason.NONE: None,
    Reason.NOT_SET: "not_set",
    Reason.READ_FAILED: "read_failed",
    Reason.INCONSISTENT_CLOCK: "inconsistent_clock",
    Reason.CATALOG_UNAVAILABLE: "catalog_unavailable",
    Reason.UNMAPPED_TIMER: "timer_mapping_pending",
}
CHANGE_NAMES = {
    ItemChange.ADDED: "added",
    ItemChange.REMOVED: "removed",
    ItemChange.MOVED: "moved",
    ItemChange.CHANGED: "replaced",
    ItemChange.QUANTITY_CHANGED: "quantity_changed",
    ItemChange.DOSES_CHANGED: "doses_changed",
}
EQUIPMENT_MATCH_NAMES = {
    EquipmentMatch.UNKNOWN: "unknown",
    EquipmentMatch.ALLOWED: "allowed",
    EquipmentMatch.BLOCKED: "blocked",
}
# Main and off hand use native logical slots 3 and 5 of the equipment container.
MAIN_HAND_SLOT = 3
OFF_HAND_SLOT = 5


def _negative_as_none(value):
    return None if value < 0 else value


def item_record(item):
    """Return the JSON record of one slot. Negative item IDs mark explicit empty slots."""
    empty = item.item_id < 0
    doses_known = item.doses >= 0
    return {
        "container_id": item.container_id,
        "slot": item.slot,
        "slot_number": item.slot + 1,
        "item_id": None if empty else item.item_id,
        "quantity": item.quantity,
        "empty": empty,
        "tracking_token": item.token or None,
        "identity_ambiguous": bool(item.flags & ItemFlags.AMBIGUOUS_IDENTITY),
        "name": decode_text(item.name) or None,
        "family_id": item.family_id if doses_known else None,
        "doses": item.doses if doses_known else None,
        "next_dose_item_id": _negative_as_none(item.next_dose_item_id),
        "metadata_reason": (
            None if empty or item.flags & ItemFlags.METADATA else "definition_unavailable"
        ),
        "equipment": {
            "slot": _negative_as_none(item.equip_slot),
            "style": STYLE_NAMES.get(item.combat_style),
            "weapon": bool(item.equipment_flags & EquipmentFlags.WEAPON),
            "two_handed": bool(item.equipment_flags & EquipmentFlags.TWO_HANDED),
            "shield": bool(item.equipment_flags & EquipmentFlags.SHIELD),
            "defender": bool(item.equipment_flags & EquipmentFlags.DEFENDER),
        },
        "effects": {
            "heals": bool(item.effect_flags & EffectFlags.HEALS),
            "restores_prayer": bool(item.effect_flags & EffectFlags.RESTORES_PRAYER),
            "restores_stats": bool(item.effect_flags & EffectFlags.RESTORES_STATS),
            "base_flat_heal": item.base_heal or None,
        },
    }


def weapon_configuration(main_hand, off_hand):
    """Describe the main-hand and off-hand combination of two item records."""
    if main_hand is None or off_hand is None:
        return "unknown"
    if main_hand["empty"]:
        return "unarmed" if off_hand["empty"] else "off_hand_only"
    if main_hand["equipment"]["two_handed"]:
        return "two_handed"
    if off_hand["equipment"]["weapon"] and main_hand["equipment"]["weapon"]:
        return "dual_wield_with_defender" if off_hand["equipment"]["defender"] else "dual_wield"
    if off_hand["equipment"]["shield"]:
        return "main_hand_and_shield"
    if off_hand["empty"]:
        return "main_hand_only"
    return "unknown"


def ability_record(ability):
    """Return the JSON record of one ability definition and its live cooldown.

    The timer-valid flag separates an observed cooldown from definition data;
    zero remaining time means an expired timer only when that flag is set.
    """
    timer_valid = bool(ability.flags & AbilityFlags.TIMER_VALID)
    base_cooldown_known = bool(ability.flags & AbilityFlags.BASE_COOLDOWN_KNOWN)
    return {
        "struct_id": ability.struct_id,
        "name": decode_text(ability.name),
        "in_ability_book": bool(ability.flags & AbilityFlags.IN_BOOK),
        "cooldown": {
            "remaining_ms": ability.remaining_ms if timer_valid else None,
            "observed_duration_ms": ability.duration_ms if timer_valid else None,
            "base_duration_ms": ability.base_cooldown_ms if base_cooldown_known else None,
            "reason": COOLDOWN_REASON_NAMES.get(ability.reason, "unknown"),
            "start_varc": _negative_as_none(ability.start_var),
            "end_varc": _negative_as_none(ability.end_var),
            "start_tick": ability.start_tick if timer_valid else None,
            "end_tick": ability.end_tick if timer_valid else None,
        },
        "requirements": {
            "dual_wield": bool(ability.requirements & Requirements.DUAL_WIELD),
            "two_handed": bool(ability.requirements & Requirements.TWO_HANDED),
            "shield": bool(ability.requirements & Requirements.SHIELD),
            "weapon": bool(ability.requirements & Requirements.WEAPON),
            "style": STYLE_NAMES.get(ability.combat_style),
            "level": ability.level_requirement,
            "adrenaline_raw": ability.adrenaline_requirement_raw,
        },
        "equipment_match": EQUIPMENT_MATCH_NAMES[ability.equipment_match],
        "castable": None,
        "castability_reason": "other_cast_conditions_not_evaluated",
    }


def item_change_record(event):
    """Return the JSON record of one observed inventory change."""
    return {
        "sequence": event.sequence,
        "observed_at_ms": event.observed_at_ns // 1_000_000,
        "pid": event.pid,
        "process_creation_time": event.process_creation_time,
        "kind": CHANGE_NAMES[event.kind],
        "before": item_record(event.before) if event.before.item_id >= 0 else None,
        "after": item_record(event.after) if event.after.item_id >= 0 else None,
    }


def extend_record(record, reader):
    """Add the inventory, equipment, ability and item-change sections to a record."""
    info = reader.inventory_info
    item_count = info.backpack_count + info.equipment_count
    items = [item_record(reader.items[index]) for index in range(item_count)]
    status = "available" if info.status == Status.AVAILABLE else "unavailable"
    catalog_available = info.catalog_status == Status.AVAILABLE
    reason = decode_text(info.message) or None

    record["inventory"] = {
        "status": status,
        "container_id": int(Container.BACKPACK),
        "slots": (
            [item for item in items if item["container_id"] == Container.BACKPACK]
            if item_count
            else None
        ),
        "reason": reason,
        "catalog_status": "available" if catalog_available else "unavailable",
        "slot_indexing": "zero_based_native_order",
    }

    equipment = [item for item in items if item["container_id"] == Container.EQUIPMENT]
    main_hand = next((item for item in equipment if item["slot"] == MAIN_HAND_SLOT), None)
    off_hand = next((item for item in equipment if item["slot"] == OFF_HAND_SLOT), None)
    bone_shield_known = bool(info.flags & InventoryFlags.BONE_SHIELD_KNOWN)
    record["equipment"] = {
        "status": status,
        "slots": equipment if item_count else None,
        "main_hand": main_hand,
        "off_hand": off_hand,
        "configuration": weapon_configuration(main_hand, off_hand),
        "bone_shield_tier": info.bone_shield_tier if bone_shield_known else None,
        "reason": reason,
    }

    record["abilities"] = {
        "status": "available" if catalog_available else "unavailable",
        "entries": [ability_record(reader.abilities[index]) for index in range(info.ability_count)],
        "reason": None if catalog_available else "catalog_unavailable",
    }

    events, lost = reader.drain_item_events()
    record["inventory_changes"] = {
        "events_lost": lost,
        "sequence": reader.item_event_cursor,
        "newly_observed": [item_change_record(event) for event in events],
    }
    return record
