"""Build our compact metadata file from the installed, read-only game cache.

No network access. This is our own documented interchange format, not a copied
game cache. Generated metadata stays local under build/ and is loaded by C.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "rs3_reader"))
from cachedefs import Archive, DecodeError, decode_item, decode_struct

# Catalog item flags: weapon, two-handed, shield, defender; effect bits separate.
WEAPON, TWO_HANDED, SHIELD, DEFENDER = 1, 2, 4, 8


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root",default=r"C:\ProgramData\Jagex\RuneScape")
    parser.add_argument("--output", type=Path, required=True, help="Output catalog path beside the DLL")
    args=parser.parse_args()
    items,structs=Archive(19,args.cache_root),Archive(22,args.cache_root)
    definitions={}
    def get_struct(sid):
        if sid not in definitions:
            definitions[sid]=decode_struct(structs.file(sid,5))
        return definitions[sid]
    dose_params={588:4,589:3,590:2,591:1,534:6,535:5,532:8,533:7,
                 2241:6,2242:5,2243:4,2244:3,2245:2,2246:1,2981:12,2982:11,2983:10,2984:9}
    families={}; potion_count=0; ambiguous=set()
    for group in structs.groups:
        try:files=structs.group(group)
        except KeyError:continue
        for sub,data in files.items():
            sid=group*32+sub;p=decode_struct(data)
            if 9453 not in p:continue
            potion_count+=1
            effect=get_struct(p[7601]) if p.get(7601) else {}
            effects=(1 if effect.get(7665,0)>0 or effect.get(7666,0)>0 else 0)
            effects|=2 if effect.get(7670,0)>0 or effect.get(7671,0)>0 else 0
            effects|=4 if any(effect.get(k,0)>0 for k in (7660,7661,7662,7663)) else 0
            for param,doses in dose_params.items():
                if p.get(param,0)<=0:continue
                iid=p[param]
                # Keep vial and flask chains separate when locating next dose.
                if 2241<=param<=2246:
                    next_id=p.get(param+1,-1) if param<2246 else -1
                elif 588<=param<=591:
                    next_id=p.get(param+1,-1) if param<591 else -1
                else:next_id=-1
                candidate=(p[9453],doses,next_id,effects,max(0,effect.get(7665,0)))
                if iid in families and families[iid]!=candidate:
                    ambiguous.add(iid)
                families[iid]=candidate
    for iid in ambiguous:
        del families[iid]  # Unknown is safer than assigning a conflicting family.
    strings=bytearray(b"\0");offsets={"":0}
    def string(value):
        value=value or ""
        if value not in offsets:
            raw=value.encode("utf-8")
            if len(raw)>511:raise DecodeError("Catalog name exceeds bound")
            offsets[value]=len(strings);strings.extend(raw+b"\0")
        return offsets[value]
    item_records=[];absent=0;failures=[]
    for group in sorted(items.groups):
        try:files=items.group(group)
        except KeyError:continue
        for sub,data in sorted(files.items()):
            iid=group*256+sub
            if not data:absent+=1;continue
            try:d=decode_item(data)
            except DecodeError as error:failures.append((iid,str(error)));continue
            p=d["params"];category=p.get(686)
            attack=get_struct(category).get(2853,0) if category else 0
            style=1 if attack in (5,6,7) else (2 if attack in (8,9,10) else (3 if attack in (1,2,3,4) else (4 if attack==37 else 0)))
            flags=WEAPON if category and d["equip_slot"] in (3,5) else 0
            flags|=TWO_HANDED if d["equip_slot"]==3 and d["hide_slot"]==5 else 0
            flags|=SHIELD if p.get(2832,0) else 0
            flags|=DEFENDER|SHIELD|WEAPON if p.get(5416,0) else 0
            family,doses,next_id,effects,heal=families.get(iid,(0,-1,-1,0,0))
            if p.get(642,0)>0:effects|=1
            # Own record: id,name offset,family,doses,next id,equip slot,style,
            # equipment flags,effect flags,base flat heal. Ten LE 32-bit words.
            item_records.append(struct.pack("<IIIiiiIIII",iid,string(d["name"]),family,doses,next_id,d["equip_slot"],style,flags,effects,heal))
    if failures:raise DecodeError("Item decode failures: "+repr(failures[:10]))
    bindings=json.loads((ROOT/"profiles"/"ability_bindings_951_3.json").read_text(encoding="utf-8"))
    ability_records=[]
    for d in bindings["abilities"]:
        requirements=int(d["requires_dual_wield"])|int(d["requires_two_handed"])<<1|int(d["requires_shield"])<<2|int(d["requires_weapon"])<<3
        parent=d["parent_stat"]
        style=1 if parent in (1,2) else (2 if parent==3 else (3 if parent==4 else (4 if parent==29 else 0)))
        if d.get("target_type")!=1 and not requirements:
            style=0  # Self/movement actions do not inherit a weapon style from their ability book.
        source=1 if (d["timer_source"] or '').startswith("client_symbol_pair") else 0
        ability_records.append(struct.pack("<IIiiIIIIIIII",d["struct_id"],string(d["name"]),d["start_var"] if source else -1,
            d["end_var"] if source else -1,d["base_cooldown_ms"],requirements,style,source,
            d["level_requirement"],d["adrenaline_requirement_raw"],d["ability_id"]&0xffffffff,
            int(d["in_ability_book"])|int(d.get("base_cooldown_known",False))<<1))
    profile=json.loads((ROOT/"profiles"/"profile_951_3.json").read_text(encoding="utf-8"))
    body=b"".join(item_records)+b"".join(ability_records)+strings
    header=struct.pack("<8sIIII32s32s",b"RS3CAT1\0",1,len(item_records),len(ability_records),len(strings),bytes.fromhex(profile["sha256"]),hashlib.sha256(body).digest())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(header+body)
    manifest={"format":1,"items":len(item_records),"abilities":len(ability_records),"potion_families":potion_count,
              "empty_definitions_skipped":absent,"ambiguous_potion_items":len(ambiguous),"decode_failures":len(failures),"client_sha256":profile["sha256"],
              "body_sha256":hashlib.sha256(body).hexdigest(),"item_reference_sha256":hashlib.sha256(items.index_bytes).hexdigest(),
              "struct_reference_sha256":hashlib.sha256(structs.index_bytes).hexdigest()}
    args.output.with_suffix(".manifest.json").write_bytes((json.dumps(manifest,indent=2)+"\n").encode())
    print(json.dumps(manifest))


if __name__=="__main__":main()
