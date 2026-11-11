"""Hit history deduplication across coalesced telemetry records."""

import unittest

from rs3_reader.hit_history import HitHistory


def telemetry_record(timestamp_ms, pid, hits):
    return {"timestamp_ms": timestamp_ms, "pid": pid, "hit_splats": {"newly_observed": hits}}


class HitHistoryTests(unittest.TestCase):
    def test_history_preserves_native_observation_time_and_process(self):
        history = HitHistory()
        event = {
            "native_event_sequence": 1,
            "observed_at_ms": 900,
            "pid": 10,
            "process_creation_time": 100,
            "amount": 224,
        }
        history.observe(telemetry_record(1000, 11, [event]))
        history.observe(telemetry_record(1050, 11, [event]))
        self.assertEqual(len(history.events), 1)
        self.assertEqual(history.events[0]["observed_at_ms"], 900)
        self.assertEqual(history.events[0]["pid"], 10)
        # A new collector can reuse sequence 1 in the same process.
        new_hit = dict(event, end_client_tick=2060)
        history.observe(telemetry_record(1100, 10, [new_hit]))
        self.assertEqual(len(history.events), 2)

    def test_history_survives_coalesced_samples_and_evicts_oldest(self):
        history = HitHistory(capacity=2)
        hit = {
            "runtime_id": 42,
            "config_id": 12357,
            "slot": 0,
            "type_id": 133,
            "amount": 224,
            "end_client_tick": 1060,
        }
        sample = {"timestamp_ms": 1000, "pid": 1, "hit_splats": {"newly_observed": [hit]}}
        history.observe(sample)
        history.observe({"timestamp_ms": 1050, "pid": 1, "hit_splats": {"newly_observed": []}})
        history.observe(sample)
        self.assertEqual(len(history.events), 1)
        self.assertEqual(history.events[0]["amount"], 224)
        for end_tick in (1120, 1180):
            later_hit = dict(hit, end_client_tick=end_tick)
            history.observe(dict(sample, hit_splats={"newly_observed": [later_hit]}))
        self.assertEqual([event["end_client_tick"] for event in history.events], [1120, 1180])


if __name__ == "__main__":
    unittest.main()
