"""Summarize local Telos captures without publishing raw gameplay records."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import statistics


def percentile_summary(values):
    return {'count': len(values), 'min_ms': round(min(values), 3),
            'median_ms': round(statistics.median(values), 3), 'max_ms': round(max(values), 3)} if values else {'count': 0}


def read_rows(path):
    rows = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n'):
                break  # A running writer may not yet have completed its last row.
            if line.strip():
                rows.append(json.loads(line))
    return rows


def session(row):
    return row['pid'], row['process_creation_time'], row['combat']['scene_epoch']


def summarize(rows):
    changes, previous, previous_ns = [], {}, {}
    keys, cues, hits, scene_ids = [], [], [], set()
    for row in rows:
        scope = session(row)
        active = row['combat']['active_prayer']
        if scope not in previous or active != previous[scope]:
            changes.append({'scope': scope, 'prayer': active, 'observed_ns': row['observed_ns'],
                            'previous_observation_ns': previous_ns.get(scope, row['observed_ns'])})
        previous[scope], previous_ns[scope] = active, row['observed_ns']
        for key in row.get('manual_prayer_keys', []):
            keys.append(dict(key, scope=scope))
        for actor in row['animations']:
            if actor['name'].partition(',')[0].strip().lower() == 'telos':
                scene_ids.add((scope, actor['instance_id']))
        for cue in row.get('npc_action_events', []):
            cues.append(cue)
        hits.extend(row.get('player_hit_events', []))
    cues = [c for c in cues if ((c['pid'], c['process_creation_time'], c['scene_epoch']), c['instance_id']) in scene_ids]
    pairs, delays = [], []
    used = set()
    for cue in cues:
        if cue['action_sequence'] != 28929:
            continue
        scope = cue['pid'], cue['process_creation_time'], cue['scene_epoch']
        matching = [h for h in hits if h['sequence'] not in used and
                    (h['pid'], h['process_creation_time'], h['scene_epoch']) == scope and
                    h['hit']['type_id'] in (133, 482) and
                    -100_000_000 <= h['observed_ns']-cue['observed_ns'] <= 1_200_000_000]
        if not matching:
            continue
        h = min(matching, key=lambda e:e['observed_ns'])
        used.add(h['sequence'])
        delay = (h['observed_ns']-cue['observed_ns'])/1e6
        active_before = [c for c in changes if c['scope'] == scope and c['observed_ns'] <= h['observed_ns']]
        active = active_before[-1]['prayer'] if active_before else None
        pair = {'cue_event': cue['sequence'], 'hit_event': h['sequence'],
                'cue_to_observed_hit_ms': round(delay, 3), 'hit_type': h['hit']['type_id'],
                'amount': h['hit']['amount'], 'prayer_near_hit_observation': active}
        pairs.append(pair)
        if h['hit']['type_id'] == 133:
            delays.append(delay)
    key_pairs, acknowledged_changes = [], set()
    for key in keys:
        matching = [c for c in changes if (c['scope'], c['observed_ns']) not in acknowledged_changes and
                    c['scope'] == key['scope'] and c['prayer'] == key['prayer'] and
                    key['observed_ns']-20_000_000 <= c['observed_ns'] <= key['observed_ns']+1_500_000_000]
        if not matching:
            continue
        c = min(matching, key=lambda c:abs(c['observed_ns']-key['observed_ns']))
        acknowledged_changes.add((c['scope'], c['observed_ns']))
        key_pairs.append({'prayer': key['prayer'], 'virtual_key': key['virtual_key'],
                          'modifiers': key['modifiers'],
                          'sampled_key_to_overhead_ms': round((c['observed_ns']-key['observed_ns'])/1e6, 3),
                          'key_poll_interval_ms': round((key['observed_ns']-key['previous_poll_ns'])/1e6, 3)})
    hit_data_present=any('player_hit_events' in r for r in rows)
    return {'retained_rows': len(rows), 'player_hit_data_present': hit_data_present,
            'executable_sha256_values': sorted({r['executable_sha256'] for r in rows if r.get('executable_sha256')}),
            'observed_curse_raw_values': sorted({r['combat']['raw_flags']['3275'] for r in rows
                                               if r['combat'].get('raw_flags', {}).get('3275') is not None}),
            'action_sequence_counts': dict(collections.Counter(str(c['action_sequence']) for c in cues)),
            'player_hit_events': len(hits), 'hit_type_counts': dict(collections.Counter(str(h['hit']['type_id']) for h in hits)),
            'cue_hit_pairs': pairs, 'cue_to_observed_damage_delay': percentile_summary(delays),
            'cue_to_observed_miss_delay': percentile_summary([p['cue_to_observed_hit_ms'] for p in pairs if p['hit_type']==482]),
            'manual_key_events': len(keys), 'matched_keyboard_changes': key_pairs,
            'sampled_key_to_overhead_delay': percentile_summary([p['sampled_key_to_overhead_ms'] for p in key_pairs]),
            'npc_action_events_lost': sum(r.get('npc_action_events_lost', 0) for r in rows),
            'player_hit_events_lost': sum(r.get('player_hit_events_lost', 0) for r in rows) if hit_data_present else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, nargs='+', required=True)
    parser.add_argument('--enrage', type=int, required=True)
    parser.add_argument('--phase', type=int, choices=[1], required=True)
    parser.add_argument('--session-date', required=True, help='Client-side calendar date, YYYY-MM-DD')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'client_session_date': args.session_date, 'enrage_user_label': args.enrage,
              'phase_user_label': args.phase, 'input_sent_by_calibration': False,
              'sources': [{'capture_sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                           'summary': summarize(read_rows(p))} for p in args.capture],
              'user_labelled_cues': {'28929': 'ordinary melee auto in the isolated phase-1 melee context',
                                    '28931': 'tendrils', '28938': 'uppercut', '28935': 'Hold Still'},
              'observed_npc_forms': {'22891': 'Telos active', '22892': 'Telos dormant'},
              'hitmark_symbol_leads': {'133': 'melee_damage_parent', '143': 'heal_splat_parent',
                                       '144': 'damage_parent', '482': 'miss_parent'},
              'automatic_flick_ready': False, 'prayer_check_deadline_validated': False,
              'limitations': ['Only 300% phase 1 has been captured; range, other phases and higher enrages remain unvalidated.',
                              'Cue-to-hit observation is not the server prayer-check deadline.',
                              'Overhead flags are observed client state, not proof of server-side activation timing.',
                              'Keyboard timestamps are sampled down edges with the recorded polling interval.',
                              'Player hitmarks do not identify the attacker; pairing uses the isolated phase-1 context.',
                              'No early/late protection outcome is inferred from a random damage amount alone.',
                              'Complete incoming-attack coverage and safe Soul Split windows are not established.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'captures': len(result['sources']), 'automatic_flick_ready': False}))


if __name__ == '__main__':
    main()
