"""Public ABI and binding checks that do not need a running RS3 client."""

import ctypes
import time
import unittest

from rs3_reader.native import (
    SIZED_STRUCTURES,
    Ability,
    Event,
    Info,
    Item,
    ItemEvent,
    NPC,
    Player,
    Position,
    Reader,
    Result,
    Status,
    library_path,
    load_library,
)
from rs3_reader.telemetry import NativeTelemetry

# A PID that never belongs to a running client.
ABSENT_PID = 0xFFFFFFFF


@unittest.skipUnless(library_path().is_file(), "Build rs3_native to run ABI tests")
class NativeLibraryTests(unittest.TestCase):
    def test_structure_sizes_and_field_offsets_match_the_c_abi(self):
        library = load_library()
        for structure_id, structure in SIZED_STRUCTURES.items():
            self.assertEqual(library.rs3_sizeof(structure_id), ctypes.sizeof(structure))
        self.assertEqual(Info.message.offset, 192)
        self.assertEqual(Player.gcd_reason.offset, 132)
        self.assertEqual(NPC.name.offset, 48)
        self.assertEqual(Event.hit.offset, 48)
        self.assertEqual(Event.name.offset, 88)
        self.assertEqual(Item.name.offset, 60)
        self.assertEqual(Ability.name.offset, 64)
        self.assertEqual(ItemEvent.before.offset, 40)
        self.assertEqual(ItemEvent.after.offset, 232)
        self.assertEqual(Position.plane.offset, 12)

    def test_missing_process_produces_null_values_and_closes(self):
        telemetry = NativeTelemetry(pid=ABSENT_PID, worker_hz=100)
        try:
            time.sleep(0.03)
            record = telemetry.sample(True)
            self.assertEqual(record["status"], "unavailable")
            self.assertIsNone(record["health"])
            self.assertIsNone(record["adrenaline_percent"])
            self.assertIsNone(record["npcs"])
            self.assertIsNone(record["position"])
            self.assertIsNone(record["inventory"]["slots"])
            self.assertEqual(record["equipment"]["configuration"], "unknown")
            self.assertEqual(record["abilities"]["entries"], [])
            self.assertEqual(telemetry.reader.drain_item_events(), ([], 0))
            self.assertGreater(record["native"]["sample_sequence"], 0)
            self.assertGreaterEqual(record["native"]["snapshot_age_ms"], 0)
            self.assertLess(record["native"]["snapshot_age_ms"], 1000)
            self.assertEqual(telemetry.reader.drain_events(), ([], 0))
        finally:
            telemetry.close()
        telemetry.close()
        with self.assertRaises(RuntimeError):
            telemetry.sample()

    def test_synchronous_sampling_and_the_worker_are_exclusive(self):
        with Reader(pid=ABSENT_PID, worker_hz=0) as reader:
            library = reader.library
            self.assertEqual(reader.sample()[0].status, Status.UNAVAILABLE)
            self.assertEqual(library.rs3_start(reader.context, 100), Result.OK)
            self.assertEqual(library.rs3_sample(reader.context, reader.snapshot), Result.STATE)
            self.assertEqual(library.rs3_start(reader.context, 100), Result.STATE)
            self.assertEqual(library.rs3_stop(reader.context), Result.OK)
            self.assertEqual(library.rs3_stop(reader.context), Result.OK)
            self.assertEqual(reader.sample()[0].status, Status.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
