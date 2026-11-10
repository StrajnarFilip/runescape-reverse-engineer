"""Recover compact ability bindings from the local cache and pinned gameval leads.

Inputs under work/references are reference JSON, not executable agents. The
output contains IDs, names and flags only, never cache blobs, scripts or
ability descriptions. Writes profiles/ability_bindings_951_3.json.
"""

import hashlib
import json

from workspace import PROFILES_DIRECTORY, WORK_DIRECTORY, add_import_paths

add_import_paths(package=True)

from rs3_reader.cache_definitions import Archive, decode_enum, decode_struct  # noqa: E402

REFERENCES_DIRECTORY = WORK_DIRECTORY / "references"
SYMBOL_KINDS = ("struct", "var_client", "var_player")
STRUCT_ARCHIVE = 22
ENUM_ARCHIVE = 17
ENUM_GROUP_SHIFT = 8
STRUCT_FILES_PER_GROUP = 32
ABILITY_BOOK_ENUM = 10148
GAMEVAL_REFERENCE_COMMIT = "2ae22f569b529e71e97361039976e3b5c53073a3"
SERVER_TICK_MS = 600

# Struct parameters.
ABILITY_ID_PARAMETER = 2793
ABILITY_NAME_PARAMETER = 2794
BASE_COOLDOWN_TICKS_PARAMETER = 2796
ADRENALINE_PARAMETER = 2798
PARENT_STAT_PARAMETER = 2806
LEVEL_PARAMETER = 2807
DUAL_WIELD_PARAMETER = 2811
TWO_HANDED_PARAMETER = 2812
SHIELD_PARAMETER = 2813
WEAPON_PARAMETER = 5195
SERVER_TIMER_START_PARAMETER = 9488
SERVER_TIMER_END_PARAMETER = 9489


def read_reference(name):
    return json.loads((REFERENCES_DIRECTORY / name).read_text())


def ability_book_structs(enums):
    """Return the struct IDs listed by the ability-book category enums."""
    def enum_values(enum_id):
        return decode_enum(enums.read_file(enum_id, ENUM_GROUP_SHIFT))["values"].values()

    book = set()
    for category_enum in enum_values(ABILITY_BOOK_ENUM):
        book.update(enum_values(category_enum))
    return book


def client_timer_pair(stem, client_variables):
    """Return (start, end, source) of the VarClient cooldown pair named after a struct symbol."""
    start = client_variables.get(stem + "_start_client")
    end = client_variables.get(stem + "_end_client")
    if start is not None and end is not None:
        return start, end, "client_symbol_pair"
    # Accept spelling differences in underscores only for a unique start/end pair.
    normalized_stem = stem.replace("_", "")
    starts = [
        identifier
        for name, identifier in client_variables.items()
        if name.endswith("_start_client") and name[:-13].replace("_", "") == normalized_stem
    ]
    ends = [
        identifier
        for name, identifier in client_variables.items()
        if name.endswith("_end_client") and name[:-11].replace("_", "") == normalized_stem
    ]
    if len(starts) == len(ends) == 1:
        return starts[0], ends[0], "client_symbol_pair_normalized"
    return start, end, None


def main():
    symbols = {kind: read_reference(f"gamevals_{kind}.json")["entries"] for kind in SYMBOL_KINDS}
    client_variables = {name: int(identifier) for identifier, name in symbols["var_client"].items()}
    parameter_ids = {
        name: int(identifier)
        for identifier, name in read_reference("gamevals_param.json")["entries"].items()
    }
    structs = Archive(STRUCT_ARCHIVE)
    book = ability_book_structs(Archive(ENUM_ARCHIVE))

    bindings = []
    for group_id in structs.group_files:
        try:
            files = structs.read_group(group_id)
        except KeyError:
            continue
        for file_id, data in files.items():
            struct_id = group_id * STRUCT_FILES_PER_GROUP + file_id
            parameters = decode_struct(data)
            if ABILITY_NAME_PARAMETER not in parameters:
                continue
            symbol = symbols["struct"].get(str(struct_id), "")
            stem = symbol.replace("combatv2_ability_", "combatv2_cooldown_", 1)
            start, end, source = client_timer_pair(stem, client_variables)
            if (
                source is None
                and SERVER_TIMER_START_PARAMETER in parameters
                and SERVER_TIMER_END_PARAMETER in parameters
            ):
                # These parameters name VarPlayer server ticks, NOT VarClient ticks.
                # Keep the raw mapping, but never apply the 20 ms client clock to it.
                source = "server_parameter_pair"
                start = parameters[SERVER_TIMER_START_PARAMETER]
                end = parameters[SERVER_TIMER_END_PARAMETER]
            if struct_id not in book and source is None:
                continue
            target_type_parameter = parameter_ids["combatv2_ability_target_type"]
            bindings.append(
                {
                    "struct_id": struct_id,
                    "name": parameters[ABILITY_NAME_PARAMETER],
                    "symbol": symbol,
                    "start_var": start,
                    "end_var": end,
                    "timer_source": source,
                    "base_cooldown_ms": (
                        parameters.get(BASE_COOLDOWN_TICKS_PARAMETER, 0) * SERVER_TICK_MS
                    ),
                    "base_cooldown_known": BASE_COOLDOWN_TICKS_PARAMETER in parameters,
                    "target_type": parameters.get(target_type_parameter, 0),
                    "requires_dual_wield": bool(parameters.get(DUAL_WIELD_PARAMETER, 0)),
                    "requires_two_handed": bool(parameters.get(TWO_HANDED_PARAMETER, 0)),
                    "requires_shield": bool(parameters.get(SHIELD_PARAMETER, 0)),
                    "requires_weapon": bool(parameters.get(WEAPON_PARAMETER, 0)),
                    "parent_stat": parameters.get(PARENT_STAT_PARAMETER, 0),
                    "level_requirement": parameters.get(LEVEL_PARAMETER, 0),
                    "adrenaline_requirement_raw": parameters.get(ADRENALINE_PARAMETER, 0),
                    "ability_id": parameters.get(ABILITY_ID_PARAMETER, -1),
                    "in_ability_book": struct_id in book,
                }
            )
    bindings.sort(key=lambda binding: binding["struct_id"])

    report = {
        "version": "951-3",
        "source": "Local JS5 definitions and revision-951 symbolic VarClient pairs",
        "gameval_reference_commit": GAMEVAL_REFERENCE_COMMIT,
        "reference_hashes": {
            kind: hashlib.sha256(
                (REFERENCES_DIRECTORY / f"gamevals_{kind}.json").read_bytes()
            ).hexdigest()
            for kind in symbols
        },
        "struct_reference_sha256": hashlib.sha256(structs.reference_table_bytes).hexdigest(),
        "binding_validation": (
            "Flurry, Hurricane and Resonance live timers checked; "
            "other symbolic pairs not individually exercised"
        ),
        "abilities": bindings,
    }
    output_path = PROFILES_DIRECTORY / "ability_bindings_951_3.json"
    output_path.write_bytes((json.dumps(report, indent=2, ensure_ascii=True) + "\n").encode())
    client_pairs = sum(
        (binding["timer_source"] or "").startswith("client_symbol_pair") for binding in bindings
    )
    print("Bindings:", len(bindings), "ability book:", len(book), "client pairs:", client_pairs)


if __name__ == "__main__":
    main()
