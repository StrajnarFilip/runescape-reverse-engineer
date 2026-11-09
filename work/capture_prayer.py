"""Record C-collected prayer flags and raw animation changes; never send input."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python' / 'rs3_reader'))
from native import Reader
from native_telemetry import combat_record


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
        previous, last_write, written, previous_sequence = None, 0, 0, 0
        while time.monotonic() < until:
            info, player, npcs, _ = reader.sample()
            if info.sequence == previous_sequence:
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
                       'prayer_raw': player.raw_variables[2] if player.valid_variables & 4 else None}
            key = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            if key != previous or now-last_write >= 1:
                payload.update(sequence=info.sequence, observed_ns=info.monotonic_ns,
                               timestamp_ns=info.timestamp_ns, client_logic_tick=info.logic_tick,
                               sample_duration_ns=info.sample_duration_ns,
                               executable_sha256=info.executable_sha256.decode('ascii'))
                out.write(json.dumps(payload) + '\n')
                out.flush()
                written += 1
                previous, last_write = key, now
            time.sleep(.002)
        print(f'Wrote {written} changed/heartbeat observations to {args.output}', file=sys.stderr)


if __name__ == '__main__':
    main()
