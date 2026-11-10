"""Record C-collected prayer flags, NPC action events and raw animations."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python' / 'rs3_reader'))
from native import NPCActionEvent, Reader
from native_telemetry import combat_record, hit_record
from capture_local_combat import PrayerKeys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--duration', type=float, default=300)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error('duration must be positive')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with Reader(pid=args.pid, worker_hz=100) as reader, args.output.open('w', encoding='utf-8') as out:
        print('Recording native prayer/animation observations; no game input.', file=sys.stderr, flush=True)
        until = time.monotonic() + args.duration
        settings = json.loads((Path(__file__).resolve().parents[1] / 'profiles' / 'prayer_flick.json').read_text())
        keys = PrayerKeys(reader.info.pid or args.pid, settings['bindings'])
        previous, last_write, written, previous_sequence = None, 0, 0, 0
        while time.monotonic() < until:
            info, player, npcs, _ = reader.sample()
            actions, lost = reader.drain_npc_action_events()
            hits, hit_lost = reader.drain_player_hit_events()
            if info.pid:
                keys.pid = info.pid
            presses = keys.sample(reader.lib.rs3_monotonic_ns)
            if info.sequence == previous_sequence and not actions and not lost and not hits and not hit_lost and not presses:
                time.sleep(.002)
                continue
            previous_sequence = info.sequence
            animations = [{'instance_id': n.instance_id, 'runtime_id': n.runtime_id,
                           'config_id': n.config_id, 'name': n.name.decode('utf-8'),
                           'valid': bool(a.valid), 'action_sequence': a.action_sequence if a.valid else None,
                           'movement_sequence': a.movement_sequence if a.valid else None}
                          for n, a in zip(npcs[:info.npc_count], reader.animations[:info.npc_count])]
            payload = {'status': info.status, 'pid': info.pid,
                       'process_creation_time': info.process_creation_time,
                       'combat': combat_record(reader), 'animations': animations,
                       'hp': player.raw_variables[0] if player.valid_variables & 1 else None,
                       'hp_changed_ns': player.hp_changed_ns,
                       'prayer_raw': player.raw_variables[2] if player.valid_variables & 4 else None,
                       'telos_raw': player.raw_variables[9] if player.valid_variables & (1 << 9) else None,
                       'position': ([reader.position.x, reader.position.y, reader.position.plane]
                                    if reader.position.valid else None),
                       'player_hitmarks': {
                           'status': reader.player_hit_info.status, 'reason': reader.player_hit_info.reason,
                           'player_index': reader.player_hit_info.player_index,
                           'instance_id': reader.player_hit_info.instance_id,
                           'hits': [hit_record(h) for h in reader.player_hits[:reader.player_hit_info.hit_count]]}}
            key = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            if actions or lost or hits or hit_lost or presses or key != previous or now-last_write >= 1:
                payload.update(sequence=info.sequence, observed_ns=info.monotonic_ns,
                               timestamp_ns=info.timestamp_ns, client_logic_tick=info.logic_tick,
                               sample_duration_ns=info.sample_duration_ns,
                               executable_sha256=info.executable_sha256.decode('ascii'),
                               npc_action_events=[{name: getattr(event, name) for name, _ in NPCActionEvent._fields_}
                                                  for event in actions],
                               npc_action_events_lost=lost,
                               manual_prayer_keys=presses,
                               player_hit_events=[dict({name: getattr(event, name) for name, _ in event._fields_ if name != 'hit'},
                                                       hit=hit_record(event.hit)) for event in hits],
                               player_hit_events_lost=hit_lost)
                out.write(json.dumps(payload) + '\n')
                out.flush()
                written += 1
                previous, last_write = key, now
            time.sleep(.002)
        print(f'Wrote {written} changed/heartbeat observations to {args.output}', file=sys.stderr)


if __name__ == '__main__':
    main()
