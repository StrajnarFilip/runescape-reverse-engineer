"""Recover compact ability bindings from local cache and pinned gameval leads.

Inputs under work/references are reference JSON, not executable agents. Output
contains IDs/names/flags only, never cache blobs, scripts or ability descriptions.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "rs3_reader"))
from cachedefs import Archive, decode_struct, decode_enum

refs = ROOT/"work"/"references"
symbols = {kind: json.loads((refs/("gamevals_"+kind+".json")).read_text())["entries"]
           for kind in ("struct", "var_client", "var_player")}
client = {name:int(identifier) for identifier,name in symbols["var_client"].items()}
player = {name:int(identifier) for identifier,name in symbols["var_player"].items()}
parameter_ids={name:int(identifier) for identifier,name in json.loads((refs/"gamevals_param.json").read_text())["entries"].items()}
cache = Archive(22)
enums = Archive(17)
book = set()
# The top-level ability-book enum points to category enums containing struct IDs.
for eid in decode_enum(enums.file(10148,8))["values"].values():
    book.update(decode_enum(enums.file(eid,8))["values"].values())
bindings = []
for group in cache.groups:
    try:
        files = cache.group(group)
    except KeyError:
        continue
    for sub,data in files.items():
        sid = group*32+sub
        params = decode_struct(data)
        if 2794 not in params:
            continue
        symbol = symbols["struct"].get(str(sid), "")
        stem = symbol.replace("combatv2_ability_", "combatv2_cooldown_", 1)
        start, end = client.get(stem+"_start_client"), client.get(stem+"_end_client")
        source = "client_symbol_pair" if start is not None and end is not None else None
        if source is None:
            # Accept spelling differences in underscores only for a unique start/end pair.
            normalized=stem.replace("_", "")
            starts=[identifier for name,identifier in client.items() if name.endswith("_start_client") and name[:-13].replace("_", "")==normalized]
            ends=[identifier for name,identifier in client.items() if name.endswith("_end_client") and name[:-11].replace("_", "")==normalized]
            if len(starts)==len(ends)==1:
                start,end=starts[0],ends[0];source="client_symbol_pair_normalized"
        if source is None and 9488 in params and 9489 in params:
            # These parameters name VarPlayer server ticks, NOT VarClient ticks.
            # Retain the raw mapping, but do not apply the 20 ms client clock.
            source = "server_parameter_pair"
            start, end = params[9488], params[9489]
        if sid not in book and source is None:
            continue
        bindings.append({"struct_id":sid,"name":params[2794],"symbol":symbol,
            "start_var":start,"end_var":end,"timer_source":source,
            "base_cooldown_ms":params.get(2796,0)*600,
            "base_cooldown_known":2796 in params,
            "target_type":params.get(parameter_ids['combatv2_ability_target_type'],0),
            "requires_dual_wield":bool(params.get(2811,0)),
            "requires_two_handed":bool(params.get(2812,0)),
            "requires_shield":bool(params.get(2813,0)),
            "requires_weapon":bool(params.get(5195,0)),
            "parent_stat":params.get(2806,0),"level_requirement":params.get(2807,0),
            "adrenaline_requirement_raw":params.get(2798,0),
            "ability_id":params.get(2793,-1),"in_ability_book":sid in book})
bindings.sort(key=lambda b:b["struct_id"])
report={"version":"951-3","source":"Local JS5 definitions and revision-951 symbolic VarClient pairs",
        "gameval_reference_commit":"2ae22f569b529e71e97361039976e3b5c53073a3",
        "reference_hashes":{kind:hashlib.sha256((refs/("gamevals_"+kind+".json")).read_bytes()).hexdigest() for kind in symbols},
        "struct_reference_sha256":hashlib.sha256(cache.index_bytes).hexdigest(),
        "binding_validation":"Flurry, Hurricane and Resonance live timers checked; other symbolic pairs not individually exercised",
        "abilities":bindings}
out=ROOT / "profiles" / "ability_bindings_951_3.json"
out.write_bytes((json.dumps(report,indent=2,ensure_ascii=True)+"\n").encode())
print("Bindings:",len(bindings),"ability book:",len(book),"client pairs:",sum((b["timer_source"] or '').startswith("client_symbol_pair") for b in bindings))
