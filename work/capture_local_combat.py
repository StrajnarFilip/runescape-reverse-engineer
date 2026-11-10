"""Read-only calibration research: local hitmarks and configured prayer key edges.

Uses the independent reference reader for the newly investigated player actor;
production acquisition stays in C. Hitmark amounts are not assumed to be damage.
Only configured function keys are observed, only in the foreground game window.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python' / 'rs3_reader'), str(ROOT / 'work' / 'reference')]
from native import Reader
from native_telemetry import combat_record
from winmem import ProcessMemory
from rs3 import RS3
from entities import decode_hit


def local_hits(memory, world, clock):
    if memory.i32(world + 0x19fc0) != 30:
        return None
    context = memory.pointer(world + 0x19fc8)
    if not context or memory.pointer(context) != world:
        return None
    index = memory.i32(context + 0x48)
    actor = memory.pointer(context + 0x58)
    if not actor:
        table = memory.pointer(world + 0x19968)
        begin, end = memory.unpack(table + 0x10, '<QQ')
        if not 0 <= index < (end-begin)//8:
            return None
        slot = memory.pointer(begin + index*8)
        actor = memory.pointer(slot + 0x38)
    if (memory.pointer(actor) != memory.module.base + 0x15367e0 or
            memory.unpack(actor + 0x88, '<H')[0] != index):
        return None
    ring = memory.pointer(actor + 0xf18)
    hits = []
    if ring:
        header = memory.read(ring, 40)
        capacity, = struct.unpack_from('<I', header, 4)
        count, data = struct.unpack_from('<QQ', header, 24)
        if not count <= capacity <= 64:
            return None
        records = memory.read(data, count*24) if count else b''
        if (records != (memory.read(data, count*24) if count else b'') or
                memory.read(ring+24, 16) != header[24:40]):
            return None
        for i in range(count):
            hit = decode_hit(struct.unpack_from('<6i', records, i*24), clock, 20)
            if hit is not None:
                hits.append(dict(hit, slot=i))
    if (memory.pointer(actor+0xf18) != ring or memory.pointer(context) != world or
            memory.unpack(actor+0x88, '<H')[0] != index):
        return None
    return {'player_index': index, 'hitmarks': hits}


class PrayerKeys:
    def __init__(self, pid, bindings):
        self.pid, self.bindings = pid, bindings
        self.previous, self.focus, self.last_poll = set(), False, 0
        user = C.WinDLL('user32')
        self.foreground = user.GetForegroundWindow
        self.foreground.restype = W.HWND
        self.owner = user.GetWindowThreadProcessId
        self.owner.argtypes, self.owner.restype = [W.HWND, C.POINTER(W.DWORD)], W.DWORD
        self.state = user.GetAsyncKeyState
        self.state.argtypes, self.state.restype = [C.c_int], C.c_short

    def sample(self, clock):
        window, pid = self.foreground(), W.DWORD()
        active = bool(window and self.owner(window, C.byref(pid)) and pid.value == self.pid)
        down = set()
        modifiers = sum(bit for key, bit in ((0x11, 1), (0x10, 2), (0x12, 4)) if self.state(key) < 0)
        if active:
            for name, binding in self.bindings.items():
                key = binding['virtual_key']
                if not 0x70 <= key <= 0x7b:
                    raise ValueError('Calibration records function keys only')
                if self.state(key) < 0:
                    down.add(key)
        now = clock()
        pressed = down-self.previous if active and self.focus else set()
        out = [{'prayer': name, 'observed_ns': now, 'previous_poll_ns': self.last_poll,
                'virtual_key': self.bindings[name]['virtual_key'], 'modifiers': modifiers}
               for name in sorted(self.bindings)
               if self.bindings[name]['virtual_key'] in pressed and self.bindings[name]['modifiers']==modifiers
               and not any(self.state(k)<0 for k in (0x5b,0x5c))]
        self.previous, self.focus, self.last_poll = down, active, now
        return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--duration', default=900, type=float)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    settings = json.loads((ROOT / 'profiles' / 'prayer_flick.json').read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ProcessMemory(args.pid) as memory, Reader(pid=args.pid, worker_hz=100) as reader, args.output.open('x', encoding='utf-8') as out:
        reference = RS3(memory)
        if reference.identity['sha256'] != 'fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45':
            raise RuntimeError('Player-hit research layout is only checked for the original profile')
        keys = PrayerKeys(args.pid, settings['bindings'])
        end, previous, last_write = time.monotonic()+args.duration, None, 0
        print('Recording local hitmarks and configured prayer keys; no input sent.', file=sys.stderr, flush=True)
        while time.monotonic() < end:
            begin_ns = reader.lib.rs3_monotonic_ns()
            info, player, _, _ = reader.sample()
            presses = keys.sample(reader.lib.rs3_monotonic_ns)
            reason, local = None, None
            try:
                client = reference.domain_capture('client')
                world = memory.pointer(client+8)
                local = local_hits(memory, world, info.logic_tick)
            except (OSError, ValueError, struct.error) as error:
                reason = type(error).__name__
            payload = {'pid': info.pid, 'process_creation_time': info.process_creation_time,
                       'scene_epoch': reader.combat.scene_epoch, 'combat': combat_record(reader),
                       'hp': player.raw_variables[0] if player.valid_variables & 1 else None,
                       'local_player': local, 'reason': reason}
            signature = json.dumps(payload, sort_keys=True)
            now = time.monotonic()
            if presses or signature != previous or now-last_write >= 1:
                payload.update(source='reference_decoder_research_and_native_snapshot',
                               read_begin_ns=begin_ns, read_end_ns=reader.lib.rs3_monotonic_ns(),
                               sample_observed_ns=info.monotonic_ns, sequence=info.sequence,
                               client_logic_tick=info.logic_tick, manual_prayer_keys=presses)
                print(json.dumps(payload), file=out, flush=True)
                previous, last_write = signature, now
            time.sleep(.005)


if __name__ == '__main__':
    main()
