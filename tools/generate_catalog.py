"""Build the compact rs3_catalog.bin metadata file from the installed, read-only game cache.

No network access is used. The output is our own documented interchange format
(see src/catalog.h), not a copy of the game cache. Generated metadata stays
local beside the DLL and is loaded by the C library.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "python"))

from rs3_reader.cache_definitions import (  # noqa: E402
    DEFAULT_CACHE_ROOT,
    Archive,
    DecodeError,
    decode_item,
    decode_struct,
)

ITEM_ARCHIVE = 19
STRUCT_ARCHIVE = 22
ITEM_GROUP_SHIFT = 8
STRUCT_GROUP_SHIFT = 5
CATALOG_MAGIC = b"RS3CAT1\0"
CATALOG_FORMAT_VERSION = 1
MAX_NAME_BYTES = 511

# Item record: id, name offset, family, doses, next dose ID, equip slot, style,
# equipment flags, effect flags, base flat heal. Ten little-endian 32-bit words.
ITEM_RECORD = struct.Struct("<IIIiiiIIII")
# Ability record: struct ID, name offset, start var, end var, base cooldown,
# requirements, style, timer source, level, adrenaline, ability ID, flags.
ABILITY_RECORD = struct.Struct("<IIiiIIIIIIII")
# Header: magic, format, item count, ability count, string bytes, client and body SHA-256.
CATALOG_HEADER = struct.Struct("<8sIIII32s32s")

# Equipment flags.
WEAPON, TWO_HANDED, SHIELD, DEFENDER = 1, 2, 4, 8
# Effect flags.
HEALS, RESTORES_PRAYER, RESTORES_STATS = 1, 2, 4
# Combat styles.
STYLE_NONE, STYLE_MELEE, STYLE_RANGED, STYLE_MAGIC, STYLE_NECROMANCY = 0, 1, 2, 3, 4
# Timer sources.
TIMER_UNMAPPED, TIMER_CLIENT_PAIR = 0, 1
# Ability definition flags.
IN_ABILITY_BOOK, BASE_COOLDOWN_KNOWN = 1, 2
MAIN_HAND_SLOT, OFF_HAND_SLOT = 3, 5
TARGETS_ENEMY = 1

# Struct and item parameters used below.
POTION_FAMILY_PARAMETER = 9453
POTION_EFFECT_PARAMETER = 7601
HEAL_PARAMETERS = (7665, 7666)
PRAYER_RESTORE_PARAMETERS = (7670, 7671)
STAT_RESTORE_PARAMETERS = (7660, 7661, 7662, 7663)
BASE_HEAL_PARAMETER = 7665
ITEM_CATEGORY_PARAMETER = 686
CATEGORY_ATTACK_STYLE_PARAMETER = 2853
SHIELD_PARAMETER = 2832
DEFENDER_PARAMETER = 5416
FOOD_HEAL_PARAMETER = 642

# Potion dose parameter -> dose count. Vial (2241..2246) and flask (588..591)
# chains locate the next dose; the other parameters do not chain.
DOSE_PARAMETERS = {
    588: 4, 589: 3, 590: 2, 591: 1,
    534: 6, 535: 5, 532: 8, 533: 7,
    2241: 6, 2242: 5, 2243: 4, 2244: 3, 2245: 2, 2246: 1,
    2981: 12, 2982: 11, 2983: 10, 2984: 9,
}  # fmt: skip
DOSE_CHAINS = ((2241, 2246), (588, 591))

ITEM_ATTACK_STYLES = {
    **dict.fromkeys((5, 6, 7), STYLE_MELEE),
    **dict.fromkeys((8, 9, 10), STYLE_RANGED),
    **dict.fromkeys((1, 2, 3, 4), STYLE_MAGIC),
    37: STYLE_NECROMANCY,
}
ABILITY_PARENT_STAT_STYLES = {
    1: STYLE_MELEE,
    2: STYLE_MELEE,
    3: STYLE_RANGED,
    4: STYLE_MAGIC,
    29: STYLE_NECROMANCY,
}


class StringTable:
    """Pools repeated names. Offset zero is reserved for the empty string."""

    def __init__(self):
        self.data = bytearray(b"\0")
        self.offsets = {"": 0}

    def offset(self, value):
        value = value or ""
        if value not in self.offsets:
            encoded = value.encode("utf-8")
            if len(encoded) > MAX_NAME_BYTES:
                raise DecodeError("Catalog name exceeds bound")
            self.offsets[value] = len(self.data)
            self.data.extend(encoded + b"\0")
        return self.offsets[value]


class StructCache:
    def __init__(self, archive):
        self.archive = archive
        self.definitions = {}

    def get(self, struct_id):
        if struct_id not in self.definitions:
            data = self.archive.read_file(struct_id, STRUCT_GROUP_SHIFT)
            self.definitions[struct_id] = decode_struct(data)
        return self.definitions[struct_id]


def iterate_group_files(archive):
    """Yield (group ID, file ID, data) for every readable group of an archive."""
    for group_id in sorted(archive.group_files):
        try:
            files = archive.read_group(group_id)
        except KeyError:
            continue
        for file_id, data in sorted(files.items()):
            yield group_id, file_id, data


def next_dose_item(parameters, dose_parameter):
    for first_parameter, last_parameter in DOSE_CHAINS:
        if first_parameter <= dose_parameter <= last_parameter:
            if dose_parameter < last_parameter:
                return parameters.get(dose_parameter + 1, -1)
            return -1
    return -1


def potion_effect_flags(effect):
    flags = 0
    if any(effect.get(parameter, 0) > 0 for parameter in HEAL_PARAMETERS):
        flags |= HEALS
    if any(effect.get(parameter, 0) > 0 for parameter in PRAYER_RESTORE_PARAMETERS):
        flags |= RESTORES_PRAYER
    if any(effect.get(parameter, 0) > 0 for parameter in STAT_RESTORE_PARAMETERS):
        flags |= RESTORES_STATS
    return flags


def collect_potion_families(structs, struct_cache):
    """Return ({item ID: (family, doses, next ID, effects, heal)}, potion count, ambiguous IDs)."""
    families, ambiguous_items, potion_count = {}, set(), 0
    for _group_id, _file_id, data in iterate_group_files(structs):
        parameters = decode_struct(data)
        if POTION_FAMILY_PARAMETER not in parameters:
            continue
        potion_count += 1
        effect_struct = parameters.get(POTION_EFFECT_PARAMETER)
        effect = struct_cache.get(effect_struct) if effect_struct else {}
        effects = potion_effect_flags(effect)
        base_heal = max(0, effect.get(BASE_HEAL_PARAMETER, 0))
        for dose_parameter, doses in DOSE_PARAMETERS.items():
            if parameters.get(dose_parameter, 0) <= 0:
                continue
            item_id = parameters[dose_parameter]
            candidate = (
                parameters[POTION_FAMILY_PARAMETER],
                doses,
                next_dose_item(parameters, dose_parameter),
                effects,
                base_heal,
            )
            if item_id in families and families[item_id] != candidate:
                ambiguous_items.add(item_id)
            families[item_id] = candidate
    for item_id in ambiguous_items:
        del families[item_id]  # Unknown is safer than a conflicting family.
    return families, potion_count, ambiguous_items


def item_equipment_flags(definition, parameters):
    category = parameters.get(ITEM_CATEGORY_PARAMETER)
    hand_slot = definition["equip_slot"] in (MAIN_HAND_SLOT, OFF_HAND_SLOT)
    flags = WEAPON if category and hand_slot else 0
    if definition["equip_slot"] == MAIN_HAND_SLOT and definition["hide_slot"] == OFF_HAND_SLOT:
        flags |= TWO_HANDED
    if parameters.get(SHIELD_PARAMETER, 0):
        flags |= SHIELD
    if parameters.get(DEFENDER_PARAMETER, 0):
        flags |= DEFENDER | SHIELD | WEAPON
    return flags


def build_item_records(items, struct_cache, families, strings):
    """Return (packed item records, skipped empty definitions)."""
    records, empty_definitions, failures = [], 0, []
    for group_id, file_id, data in iterate_group_files(items):
        item_id = (group_id << ITEM_GROUP_SHIFT) + file_id
        if not data:
            empty_definitions += 1
            continue
        try:
            definition = decode_item(data)
        except DecodeError as error:
            failures.append((item_id, str(error)))
            continue
        parameters = definition["params"]
        category = parameters.get(ITEM_CATEGORY_PARAMETER)
        attack_style = 0
        if category:
            attack_style = struct_cache.get(category).get(CATEGORY_ATTACK_STYLE_PARAMETER, 0)
        family, doses, next_item_id, effects, base_heal = families.get(item_id, (0, -1, -1, 0, 0))
        if parameters.get(FOOD_HEAL_PARAMETER, 0) > 0:
            effects |= HEALS
        records.append(
            ITEM_RECORD.pack(
                item_id,
                strings.offset(definition["name"]),
                family,
                doses,
                next_item_id,
                definition["equip_slot"],
                ITEM_ATTACK_STYLES.get(attack_style, STYLE_NONE),
                item_equipment_flags(definition, parameters),
                effects,
                base_heal,
            )
        )
    if failures:
        raise DecodeError("Item decode failures: " + repr(failures[:10]))
    return records, empty_definitions


def build_ability_records(bindings, strings):
    records = []
    for ability in bindings["abilities"]:
        requirements = (
            int(ability["requires_dual_wield"])
            | int(ability["requires_two_handed"]) << 1
            | int(ability["requires_shield"]) << 2
            | int(ability["requires_weapon"]) << 3
        )
        style = ABILITY_PARENT_STAT_STYLES.get(ability["parent_stat"], STYLE_NONE)
        if ability.get("target_type") != TARGETS_ENEMY and not requirements:
            style = STYLE_NONE  # Self and movement actions do not inherit their book's style.
        # Only VarClient pairs use the clock sampled by the runtime.
        client_pair = (ability["timer_source"] or "").startswith("client_symbol_pair")
        definition_flags = int(ability["in_ability_book"]) * IN_ABILITY_BOOK
        if ability.get("base_cooldown_known", False):
            definition_flags |= BASE_COOLDOWN_KNOWN
        records.append(
            ABILITY_RECORD.pack(
                ability["struct_id"],
                strings.offset(ability["name"]),
                ability["start_var"] if client_pair else -1,
                ability["end_var"] if client_pair else -1,
                ability["base_cooldown_ms"],
                requirements,
                style,
                TIMER_CLIENT_PAIR if client_pair else TIMER_UNMAPPED,
                ability["level_requirement"],
                ability["adrenaline_requirement_raw"],
                ability["ability_id"] & 0xFFFFFFFF,
                definition_flags,
            )
        )
    return records


def read_profile_json(file_name):
    return json.loads((REPOSITORY_ROOT / "profiles" / file_name).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT)
    parser.add_argument(
        "--output", type=Path, required=True, help="Output catalog path beside the DLL"
    )
    arguments = parser.parse_args()

    items = Archive(ITEM_ARCHIVE, arguments.cache_root)
    structs = Archive(STRUCT_ARCHIVE, arguments.cache_root)
    struct_cache = StructCache(structs)
    strings = StringTable()
    families, potion_count, ambiguous_items = collect_potion_families(structs, struct_cache)
    item_records, empty_definitions = build_item_records(items, struct_cache, families, strings)
    bindings = read_profile_json("ability_bindings_951_3.json")
    ability_records = build_ability_records(bindings, strings)

    profile = read_profile_json("profile_951_3.json")
    body = b"".join(item_records) + b"".join(ability_records) + bytes(strings.data)
    body_digest = hashlib.sha256(body)
    header = CATALOG_HEADER.pack(
        CATALOG_MAGIC,
        CATALOG_FORMAT_VERSION,
        len(item_records),
        len(ability_records),
        len(strings.data),
        bytes.fromhex(profile["sha256"]),
        body_digest.digest(),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(header + body)

    manifest = {
        "format": CATALOG_FORMAT_VERSION,
        "items": len(item_records),
        "abilities": len(ability_records),
        "potion_families": potion_count,
        "empty_definitions_skipped": empty_definitions,
        "ambiguous_potion_items": len(ambiguous_items),
        "decode_failures": 0,
        "client_sha256": profile["sha256"],
        "body_sha256": body_digest.hexdigest(),
        "item_reference_sha256": hashlib.sha256(items.reference_table_bytes).hexdigest(),
        "struct_reference_sha256": hashlib.sha256(structs.reference_table_bytes).hexdigest(),
    }
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    arguments.output.with_suffix(".manifest.json").write_bytes(manifest_text.encode())
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
