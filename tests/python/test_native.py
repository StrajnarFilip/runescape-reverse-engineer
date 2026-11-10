"""Public ABI/binding checks without requiring a running RS3 client."""
import ctypes as C
import time
import unittest
from unittest.mock import patch

from native import (Ability, Event, Hit, Info, InventoryInfo, Item, ItemEvent, NPC,
                    Options, Player, PlayerHitEvent, Position, Reader, library_path, load_library)
from native_telemetry import NativeTelemetry
from tracker import HitHistory


@unittest.skipUnless(library_path().is_file(), "Build rs3_native to run ABI tests")
class NativeTests(unittest.TestCase):
    def test_struct_sizes_and_offsets(self):
        lib = load_library()
        structures = (Options, Info, Player, NPC, Hit, Event, InventoryInfo, Item, Ability, ItemEvent, Position)
        for i, structure in enumerate(structures, 1):
            self.assertEqual(lib.rs3_sizeof(i), C.sizeof(structure))
        self.assertEqual(Info.message.offset, 192)
        self.assertEqual(Player.gcd_reason.offset, 132)
        self.assertEqual(NPC.name.offset, 48)
        self.assertEqual(Event.hit.offset, 48)
        self.assertEqual(Event.name.offset, 88)
        self.assertEqual(Item.name.offset,60)
        self.assertEqual(Ability.name.offset,64)
        self.assertEqual(ItemEvent.before.offset,40)
        self.assertEqual(ItemEvent.after.offset,232)
        self.assertEqual(Position.plane.offset, 12)

    def test_missing_process_produces_null_values_and_closes(self):
        telemetry = NativeTelemetry(pid=0xffffffff, worker_hz=100)
        try:
            time.sleep(.03)
            record = telemetry.sample(True)
            self.assertEqual(record["status"], "unavailable")
            self.assertIsNone(record["health"])
            self.assertIsNone(record["adrenaline_percent"])
            self.assertIsNone(record["npcs"])
            self.assertIsNone(record["position"])
            self.assertIsNone(record["inventory"]["slots"])
            self.assertEqual(record["equipment"]["configuration"],"unknown")
            self.assertEqual(record["abilities"]["entries"],[])
            self.assertEqual(telemetry.reader.drain_item_events(),([],0))
            self.assertGreater(record["native"]["sample_sequence"], 0)
            self.assertGreaterEqual(record["native"]["snapshot_age_ms"], 0)
            self.assertLess(record["native"]["snapshot_age_ms"], 1000)
            self.assertEqual(telemetry.reader.drain_events(), ([], 0))
        finally:
            telemetry.close()
        telemetry.close()
        with self.assertRaises(RuntimeError):
            telemetry.sample()

    def test_sync_and_worker_lifecycle_are_exclusive(self):
        with Reader(pid=0xffffffff, worker_hz=0) as reader:
            self.assertEqual(reader.sample()[0].status, 3)
            self.assertEqual(reader.lib.rs3_start(reader.context, 100), 0)
            self.assertEqual(reader.lib.rs3_sample(reader.context, reader.snapshot), -5)
            self.assertEqual(reader.lib.rs3_start(reader.context, 100), -5)
            self.assertEqual(reader.lib.rs3_stop(reader.context), 0)
            self.assertEqual(reader.lib.rs3_stop(reader.context), 0)
            self.assertEqual(reader.sample()[0].status, 3)

    def test_queued_player_hit_preserves_identity_after_frame_failure(self):
        telemetry = NativeTelemetry(pid=0xffffffff, worker_hz=0)
        event = PlayerHitEvent(sequence=7, observed_ns=123456, sample_sequence=9,
                               scene_epoch=3, process_creation_time=100, instance_id=4,
                               pid=22, player_index=5, hit=Hit(type_id=133, amount=224, state=1))
        try:
            with patch.object(telemetry.reader, 'drain_player_hit_events', return_value=([event], 2)):
                record = telemetry.sample()
            channel = record['player_hit_splats']
            self.assertEqual(channel['status'], 'unavailable')
            self.assertEqual(channel['hits'], [])
            self.assertEqual(channel['events_lost'], 2)
            hit = channel['newly_observed'][0]
            self.assertEqual((hit['pid'], hit['process_creation_time'], hit['instance_id'], hit['scene_epoch']),
                             (22, 100, 4, 3))
            self.assertEqual((hit['observed_ns'], hit['sample_sequence'], hit['amount']), (123456, 9, 224))
        finally:
            telemetry.close()

    def test_history_preserves_native_observation_time_and_process(self):
        history = HitHistory()
        event = {"native_event_sequence": 1, "observed_at_ms": 900, "pid": 10,
                 "process_creation_time": 100, "amount": 224}
        history.observe({"timestamp_ms": 1000, "pid": 11, "hit_splats": {"newly_observed": [event]}})
        history.observe({"timestamp_ms": 1050, "pid": 11, "hit_splats": {"newly_observed": [event]}})
        self.assertEqual(len(history.events), 1)
        self.assertEqual(history.events[0]["observed_at_ms"], 900)
        self.assertEqual(history.events[0]["pid"], 10)
        # A new collector can reuse sequence 1 in the same process.
        new_hit = dict(event, end_client_tick=2060)
        history.observe({"timestamp_ms": 1100, "pid": 10, "hit_splats": {"newly_observed": [new_hit]}})
        self.assertEqual(len(history.events), 2)


if __name__ == "__main__":
    unittest.main()
