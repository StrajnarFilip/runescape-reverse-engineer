"""Publish selected real NPC observations, keeping complete captures local."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=root/'work'/'npc_combat_capture.jsonl'
digest=hashlib.sha256()
with source.open('rb') as stream:
    for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
samples=0; seen=set(); examples={}; previous={}; transitions=[]; baselines={}
first=last=None
with source.open(encoding='utf-8') as stream:
    for line in stream:
        try:record=json.loads(line)
        except json.JSONDecodeError:continue
        samples+=1;stamp=record['timestamp_ms'];first=stamp if first is None else first;last=stamp
        entities=(record.get('npcs') or {}).get('entities') or []
        lookup={e['runtime_id']:e for e in entities}
        for e in entities:
            if e.get('name') not in ('Goblin','Giant spider'):continue
            h=e['health'];key=(e['runtime_id'],e['config_id'])
            if h['current'] is None:continue
            if h['current']==h['max']:
                baselines[e['name']]={'config_id':e['config_id'],'current_hp':h['current'],'max_hp':h['max']}
            if previous.get(key)==550 and h['current']==326:
                transitions.append({'name':e['name'],'runtime_id':e['runtime_id'],'config_id':e['config_id'],
                                    'before_hp':550,'after_hp':326,'difference':224,'observed_at_ms':stamp})
            previous[key]=h['current']
        for hit in (record.get('hit_splats') or {}).get('newly_observed') or []:
            key=tuple(hit.get(k) for k in ('runtime_id','config_id','slot','type_id','amount','end_client_tick'))
            if key in seen:continue
            seen.add(key)
            if hit.get('name') not in ('Goblin','Giant spider') or hit['amount'] not in (224,550,1000):continue
            if hit['amount'] not in examples:
                e=lookup.get(hit['runtime_id'])
                examples[hit['amount']]={k:hit[k] for k in ['name','runtime_id','config_id','amount','type_id','start_client_tick','end_client_tick']}
                examples[hit['amount']].update(observed_at_ms=stamp,health_in_same_sample=e['health'] if e else None)
report={'schema_version':1,'source':'actual_local_native_memory_capture','local_capture_name':source.name,
        'capture_sha256':digest.hexdigest(),'client_version':'951-3',
        'client_sha256':'fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45',
        'sample_count':samples,'first_observed_at_ms':first,'last_observed_at_ms':last,
        'user_reported_hit_amounts':[224,550,1000],'full_health_examples':baselines,
        'matching_observed_hits':[examples[k] for k in (224,550,1000) if k in examples],
        'observed_hp_transitions_matching_224':transitions,
        'limitations':['Amounts are raw hitmark values; some types represent healing or text marks.',
                      'The ring identifies the recipient, not the attacker.',
                      'Polling can miss overwritten or removed records.',
                      'Raw captures remain local. Telos phase and animation classification are not validated.']}
(root/'outputs'/'rs3_reader'/'npc-validation-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('Samples',samples,'matching amounts',sorted(examples),'matching HP transitions',len(transitions))
