"""Preserve native observations and controller outcomes through compact logging."""

import copy
import io
import json
import unittest

from rs3_reader.bot.telemetry_attempt import TelemetryAttemptLog, read_attempt_records


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
