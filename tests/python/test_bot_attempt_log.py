"""Preserve native observations and controller outcomes through compact logging."""

import copy
import io
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from rs3_reader.bot.attempt_log import AttemptLog
from rs3_reader.bot.telemetry_attempt import TelemetryAttemptLog, read_attempt_records
from rs3_reader.native import ItemChange, ItemEvent, Reader, library_path


@unittest.skipUnless(library_path().is_file(), "Build rs3_native to run ABI tests")
class NativeAttemptLogTests(unittest.TestCase):
    def test_frame_drains_each_queue_once_and_preserves_unknown_initial_inventory(self):
        stream = io.StringIO()
        with Reader(pid=0xFFFFFFFF, worker_hz=0) as reader:
            reader.sample()
            info = reader.info
            attempt = AttemptLog(stream, mode="observation", enrage=None)
            names = (
                "drain_npc_action_events",
                "drain_player_hit_events",
                "drain_events",
                "drain_item_events",
            )
            with ExitStack() as stack:
                drains = {
                    name: stack.enter_context(
                        patch.object(reader, name, wraps=getattr(reader, name))
                    )
                    for name in names
                }
                actions, action_loss = reader.drain_npc_action_events()
                hits, hit_loss = reader.drain_player_hit_events()
                attempt.frame(
                    reader,
                    info,
                    phase=None,
                    actions=actions,
                    action_loss=action_loss,
                    hits=hits,
                    hit_loss=hit_loss,
                )
                for drain in drains.values():
                    drain.assert_called_once_with()
            self.assertEqual(attempt.inventory_events, [])
            self.assertEqual(attempt.inventory_lost, 0)
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        initial = next(row["changes"] for row in rows if row["event"] == "state")
        self.assertIsNone(initial["inventory"])
        self.assertIsNone(initial["equipment"])
        self.assertIsNone(initial["hp"])
        self.assertEqual([row["sequence"] for row in rows], list(range(1, len(rows) + 1)))

    def test_inventory_wall_clock_identity_and_loss_are_preserved(self):
        stream = io.StringIO()
        event = ItemEvent(
            sequence=91,
            observed_at_ns=1_800_000_000_000_000_000,
            pid=22,
            process_creation_time=33,
            kind=ItemChange.DOSES_CHANGED,
        )
        event.before.item_id, event.after.item_id = 49027, 49025
        event.before.doses, event.after.doses = 6, 5
        with Reader(pid=0xFFFFFFFF, worker_hz=0) as reader:
            reader.sample()
            info = reader.info
            attempt = AttemptLog(stream, mode="observation", enrage=None)
            with patch.object(reader, "drain_item_events", return_value=([event], 2)) as drain:
                attempt.frame(
                    reader, info, phase=None, actions=[], action_loss=0, hits=[], hit_loss=0
                )
                drain.assert_called_once_with()
            self.assertEqual(attempt.inventory_events, [event])
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        loss = next(row for row in rows if row["event"] == "queue_loss")
        self.assertEqual(loss["inventory_changes"], 2)
        change = next(row for row in rows if row["event"] == "inventory_change")
        self.assertEqual(change["native_sequence"], 91)
        self.assertEqual(change["event_timestamp_ns"], event.observed_at_ns)
        self.assertEqual(change["change"]["observed_at_ms"], 1_800_000_000_000)
        self.assertFalse(change["same_process"])
        self.assertEqual(change["change"]["after"]["doses"], 5)


class TelemetryAttemptLogTests(unittest.TestCase):
    def test_round_trip_preserves_unknowns_events_removals_and_numeric_types(self):
        records = [
            {"kind": "p1_preflight", "bindings": {"renewal": [2, 8, "PgDn"]}},
            {
                "kind": "supervised_p1",
                "observed_ns": 100000000,
                "telemetry": {
                    "timestamp_ms": 1234,
                    "native": {"sample_sequence": 10},
                    "freshness": {"client_logic_tick": 50},
                    "adrenaline_percent": {"value": 120.0, "reason": None},
                    "inventory": [{"item_id": 49079, "doses": 4}],
                    "npc_action_events": [{"sequence": 9, "animation_id": 28962}],
                    "npc_action_events_lost": 0,
                    "unknown": None,
                    "typed": True,
                },
                "events": [{"event": "action_attempted", "action": "renewal"}],
            },
        ]
        changed = copy.deepcopy(records[-1])
        changed["telemetry"]["native"]["sample_sequence"] = 11
        changed["telemetry"]["npc_action_events"] = []
        changed["telemetry"]["npc_action_events_lost"] = 1
        changed["telemetry"]["typed"] = 1  # Python equality must not erase a type change.
        del changed["telemetry"]["unknown"]
        records.extend([changed, copy.deepcopy(changed)])
        stream = io.StringIO()
        writer = TelemetryAttemptLog(stream, wall_clock_ns=lambda: 9000000)
        for record in records:
            writer.record(record)
        packets = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertIsNone(packets[0]["timestamp_ms"])
        self.assertEqual(packets[0]["timestamp_reason"], "source_timestamp_unavailable")
        self.assertEqual(packets[1]["timestamp_ms"], 1234)
        self.assertEqual(packets[1]["recorded_at_ms"], 9)
        self.assertEqual(packets[1]["client_logic_tick"], 50)
        self.assertEqual(packets[-1]["set"], [])
        reconstructed = list(read_attempt_records(io.StringIO(stream.getvalue())))
        self.assertEqual(reconstructed, records)
        self.assertIs(type(reconstructed[2]["telemetry"]["typed"]), int)
        reconstructed[-1]["telemetry"]["inventory"].clear()
        self.assertEqual(len(reconstructed[-2]["telemetry"]["inventory"]), 1)

    def test_record_owns_its_copy_and_rejects_nonfinite_values_before_writing(self):
        stream = io.StringIO()
        writer = TelemetryAttemptLog(stream)
        record = {"items": [{"doses": 4}]}
        writer.record(record)
        record["items"][0]["doses"] = 3
        writer.record(record)
        with self.assertRaises(ValueError):
            writer.record({"adrenaline": float("nan")})
        self.assertEqual(writer.sequence, 2)
        self.assertEqual(
            list(read_attempt_records(io.StringIO(stream.getvalue()))),
            [{"items": [{"doses": 4}]}, {"items": [{"doses": 3}]}],
        )

    def test_truncated_reordered_and_invalid_paths_are_rejected(self):
        stream = io.StringIO()
        writer = TelemetryAttemptLog(stream)
        writer.record({"items": [1]})
        writer.record({"items": [2]})
        packets = [json.loads(line) for line in stream.getvalue().splitlines()]
        for mutation in (
            lambda values: values.pop(0),
            lambda values: values[1].update(sequence=3),
            lambda values: values[1].update(set=[[["items", 4], 2]]),
            lambda values: values[1].update(remove=[["items", 0]]),
        ):
            changed = copy.deepcopy(packets)
            mutation(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                list(read_attempt_records(io.StringIO("\n".join(map(json.dumps, changed)))))


if __name__ == "__main__":
    unittest.main()
