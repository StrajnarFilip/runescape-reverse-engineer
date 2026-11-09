"""Observe native prayer/animations or replay timing scenarios. No game input."""
import argparse
import ctypes as C
import json
from pathlib import Path
import sys
import time
from native import Reader, ROOT
from native_telemetry import combat_record
from prayer import Planner, Frame, Threat, Prayer, prayer_id


def available_mask(settings):
    return sum(1 << prayer_id(name) for name in set(settings['available_prayers']))


def replay(path, settings):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported replay schema')
    with Planner(settings) as planner:
        for row in data['frames']:
            now = row['time_ms'] * 1000000
            frame = Frame(now, row.get('observed_ms', row['time_ms']) * 1000000,
                          row.get('session', 1), row['sequence'], row.get('coverage_until_ms', 0) * 1000000,
                          row.get('flags', 7), prayer_id(row['active_prayer']), available_mask(settings),
                          row.get('prayer_points', 840))
            threats = [Threat(t['id'], t.get('source', 1), t['earliest_ms'] * 1000000,
                              t['latest_ms'] * 1000000, prayer_id(t['prayer']), t.get('validated', False))
                       for t in row.get('threats', [])]
            d = planner.step(frame, threats)
            print(json.dumps({'source': 'synthetic_replay', 'time_ms': row['time_ms'],
                              'decision': planner.describe(d), 'input_sent': False}))
            if d.action == 2 and row.get('simulate_acceptance', False):
                planner.note_sent(d.command_id, now)


def observe(args, settings):
    # No timing classifier is installed yet. An empty list with UNKNOWN coverage
    # cannot authorize a healing flick or an automatic protection switch.
    with Reader(pid=args.pid, worker_hz=100) as reader, Planner(settings) as planner:
        until = time.monotonic() + args.duration
        last, last_write, epoch_key, session = None, 0, None, 0
        while time.monotonic() < until:
            info, player, npcs, _ = reader.sample()
            key = (info.pid, info.process_creation_time, reader.combat.scene_epoch)
            if key != epoch_key:
                session += 1
                epoch_key = key
            active = reader.lib.rs3_combat_active_prayer(C.byref(reader.combat))
            flags = (1 if info.status in (1, 2) and reader.combat.scene_epoch and player.clock_stalled_ns < 150000000 else 0)
            flags |= 2 if active else 0
            flags |= 4 if player.valid_variables & 4 else 0
            frame = Frame(reader.lib.rs3_monotonic_ns(), info.monotonic_ns, session,
                          info.sequence, 0, flags, active, available_mask(settings),
                          (player.raw_variables[2] & 32767) if flags & 4 else 0)
            decision = planner.describe(planner.step(frame))
            animations = [{'instance_id': n.instance_id, 'runtime_id': n.runtime_id, 'config_id': n.config_id,
                           'name': n.name.decode(), 'action_sequence': a.action_sequence if a.valid else None,
                           'movement_sequence': a.movement_sequence if a.valid else None}
                          for n, a in zip(npcs[:info.npc_count], reader.animations[:info.npc_count])]
            payload = {'source': 'native_memory', 'pid': info.pid, 'combat': combat_record(reader),
                       'animations': animations, 'decision': decision, 'input_sent': False,
                       'live_flick_ready': False, 'missing': ['attack_classification', 'prayer_check_timing', 'complete_attack_coverage']}
            signature = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            if signature != last or now-last_write >= 1:
                payload.update(observed_ns=info.monotonic_ns, sequence=info.sequence)
                print(json.dumps(payload), flush=True)
                last, last_write = signature, now
            time.sleep(.01)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=ROOT / 'profiles' / 'prayer_flick.json')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--duration', type=float, default=60)
    parser.add_argument('--replay', type=Path)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error('duration must be positive')
    settings = json.loads(args.profile.read_text(encoding='utf-8'))
    if settings.get('schema_version') != 1 or settings.get('mode') != 'observe':
        parser.error('Only schema 1 observe profiles are supported')
    print('Prayer planner: replay/observation only; no game input.', file=sys.stderr)
    replay(args.replay, settings) if args.replay else observe(args, settings)


if __name__ == '__main__':
    main()
