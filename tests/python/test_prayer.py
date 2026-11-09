"""Exercise the exported C planner through ctypes, without game input."""
import ctypes as C
import json
from pathlib import Path
import unittest
from native import CombatState, NPCAnimation, ROOT, library_path, load_library
from prayer import Planner, Frame, Config, Threat, Decision, Prayer


@unittest.skipUnless(library_path().is_file(), 'Build rs3_native for FFI checks')
class PrayerTests(unittest.TestCase):
    def settings(self):
        return json.loads((ROOT / 'profiles' / 'prayer_flick.json').read_text())

    def test_additive_abi_sizes(self):
        lib = load_library()
        self.assertEqual(lib.rs3_combat_sizeof(1), C.sizeof(CombatState))
        self.assertEqual(lib.rs3_combat_sizeof(2), C.sizeof(NPCAnimation))
        with Planner(self.settings()) as p:
            for i, struct in enumerate((Config, Frame, Threat, Decision), 1):
                self.assertEqual(p.lib.rs3_prayer_sizeof(i), C.sizeof(struct))

    def test_soul_split_proposal_needs_new_observed_acknowledgement(self):
        with Planner(self.settings()) as p:
            f = Frame(10000000000, 10000000000, 1, 1, 20000000000, 15, Prayer.MAGIC, 0x7c, 840)
            d = p.step(f)
            self.assertEqual((d.action, d.prayer), (2, Prayer.SOUL_SPLIT))
            p.note_sent(d.command_id, f.now_ns)
            f.now_ns += 10000000
            self.assertEqual(p.step(f).action, 3)
            f.sequence += 1
            f.observed_ns = f.now_ns
            f.active_prayer = Prayer.SOUL_SPLIT
            self.assertEqual(p.step(f).action, 1)
        with self.assertRaises(RuntimeError):
            p.step(f)

    def test_unclassified_live_coverage_never_authorizes_soul_split(self):
        with Planner(self.settings()) as p:
            f = Frame(10000000000, 10000000000, 1, 1, 0, 7, Prayer.MAGIC, 0x7c, 840)
            d = p.step(f)
            self.assertEqual((d.action, d.reason), (1, 9))
            unknown = Threat(1, 42, f.now_ns, f.now_ns, Prayer.UNKNOWN, 0)
            self.assertEqual(p.step(f, [unknown]).action, 0)

    def test_replaced_proposal_rejects_old_dispatch_id(self):
        with Planner(self.settings()) as p:
            f = Frame(10000000000, 10000000000, 1, 1, 0, 7, Prayer.SOUL_SPLIT, 0x7c, 840)
            t = Threat(1, 42, 10600000000, 10600000000, Prayer.MAGIC, 1)
            old = p.step(f, [t])
            t.prayer = Prayer.MELEE
            new = p.step(f, [t])
            self.assertNotEqual(old.command_id, new.command_id)
            with self.assertRaises(RuntimeError):
                p.note_sent(old.command_id, f.now_ns)


if __name__ == '__main__':
    unittest.main()
