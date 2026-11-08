"""Presentation-only conversion of C inventory, equipment and ability records."""
STYLES = {0: None, 1: "melee", 2: "ranged", 3: "magic", 4: "necromancy"}
REASONS = {0: None, 1: "not_set", 2: "read_failed", 6: "inconsistent_clock", 9: "catalog_unavailable", 12: "timer_mapping_pending"}
CHANGES = {1: "added", 2: "removed", 3: "moved", 4: "replaced", 5: "quantity_changed", 6: "doses_changed"}


def item_record(item):
    empty = item.item_id<0
    return {"container_id": item.container_id, "slot": item.slot, "slot_number": item.slot+1,
            "item_id": None if empty else item.item_id, "quantity": item.quantity, "empty": empty,
            "tracking_token": item.token or None, "identity_ambiguous": bool(item.flags&2),
            "name": item.name.decode("utf-8") or None,
            "family_id": item.family_id if item.doses>=0 else None, "doses": None if item.doses<0 else item.doses,
            "next_dose_item_id": None if item.next_dose_item_id<0 else item.next_dose_item_id,
            "metadata_reason": None if empty or item.flags&1 else "definition_unavailable",
            "equipment": {"slot": None if item.equip_slot<0 else item.equip_slot,
                          "style": STYLES.get(item.combat_style), "weapon": bool(item.equipment_flags&1),
                          "two_handed": bool(item.equipment_flags&2), "shield": bool(item.equipment_flags&4),
                          "defender": bool(item.equipment_flags&8)},
            "effects": {"heals": bool(item.effect_flags&1), "restores_prayer": bool(item.effect_flags&2),
                        "restores_stats": bool(item.effect_flags&4), "base_flat_heal": item.base_heal or None}}


def extend_record(record, reader):
    info = reader.inventory_info
    count = info.backpack_count+info.equipment_count
    items = [item_record(reader.items[i]) for i in range(count)]
    status = "available" if info.status==1 else "unavailable"
    record["inventory"] = {"status": status, "container_id": 93,
                           "slots": [i for i in items if i["container_id"]==93] if count else None,
                           "reason": info.message.decode("utf-8") or None,
                           "catalog_status": "available" if info.catalog_status==1 else "unavailable",
                           "slot_indexing": "zero_based_native_order"}
    equipment = [i for i in items if i["container_id"]==94]
    main = next((i for i in equipment if i["slot"]==3), None)
    off = next((i for i in equipment if i["slot"]==5), None)
    configuration = "unknown"
    if main is not None and off is not None:
        if main["empty"]:
            configuration = "unarmed" if off["empty"] else "off_hand_only"
        elif main["equipment"]["two_handed"]:
            configuration = "two_handed"
        elif off["equipment"]["weapon"] and main["equipment"]["weapon"]:
            configuration = "dual_wield_with_defender" if off["equipment"]["defender"] else "dual_wield"
        elif off["equipment"]["shield"]:
            configuration = "main_hand_and_shield"
        elif off["empty"]:
            configuration = "main_hand_only"
    record["equipment"] = {"status": status, "slots": equipment if count else None,
                           "main_hand": main, "off_hand": off, "configuration": configuration,
                           "bone_shield_tier": info.bone_shield_tier if info.flags&16 else None,
                           "reason": info.message.decode("utf-8") or None}
    abilities = []
    for i in range(info.ability_count):
        a = reader.abilities[i]
        abilities.append({"struct_id": a.struct_id, "name": a.name.decode("utf-8"), "in_ability_book": bool(a.flags&2),
            "cooldown": {"remaining_ms": a.remaining_ms if a.flags&1 else None,
                         "observed_duration_ms": a.duration_ms if a.flags&1 else None,
                         "base_duration_ms": a.base_cooldown_ms if a.flags&4 else None, "reason": REASONS.get(a.reason, "unknown"),
                         "start_varc": a.start_var if a.start_var>=0 else None,
                         "end_varc": a.end_var if a.end_var>=0 else None,
                         "start_tick": a.start_tick if a.flags&1 else None, "end_tick": a.end_tick if a.flags&1 else None},
            "requirements": {"dual_wield": bool(a.requirements&1), "two_handed": bool(a.requirements&2),
                             "shield": bool(a.requirements&4), "weapon": bool(a.requirements&8),
                             "style": STYLES.get(a.combat_style), "level": a.level_requirement,
                             "adrenaline_raw": a.adrenaline_requirement_raw},
            "equipment_match": {0: "unknown", 1: "allowed", 2: "blocked"}[a.equipment_match],
            "castable": None, "castability_reason": "other_cast_conditions_not_evaluated"})
    record["abilities"] = {"status": "available" if info.catalog_status==1 else "unavailable",
                           "entries": abilities, "reason": None if info.catalog_status==1 else "catalog_unavailable"}
    events, lost = reader.drain_item_events()
    record["inventory_changes"] = {"events_lost": lost, "sequence": reader.item_cursor,
        "newly_observed": [{"sequence": e.sequence, "observed_at_ms": e.observed_at_ns//1000000,
            "pid": e.pid, "process_creation_time": e.process_creation_time, "kind": CHANGES[e.kind],
            "before": item_record(e.before) if e.before.item_id>=0 else None,
            "after": item_record(e.after) if e.after.item_id>=0 else None} for e in events]}
    return record
