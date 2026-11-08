import unittest
from unittest.mock import patch
from rs3 import decode_gcd, extract_bits
from telemetry import Telemetry

class DecodingTests(unittest.TestCase):
    def test_prayer_packed(self):
        self.assertEqual(extract_bits(5512659, 0, 14)/10, 763.5)

    def test_gcd_start_expiry_and_old_value(self):
        self.assertEqual(decode_gcd(1000,1090,1000)["remaining_ms"],1800)
        self.assertEqual(decode_gcd(1000,1090,1045)["remaining_ms"],900)
        self.assertEqual(decode_gcd(1000,1090,1090)["remaining_ms"],0)
        self.assertEqual(decode_gcd(1000,1090,2000)["remaining_ms"],0)

    def test_gcd_counter_wrap(self):
        self.assertEqual(decode_gcd(0x7ffffff0,-0x7fffffb6,-0x80000000)["remaining_ms"],1480)

    def test_gcd_unknown_and_inconsistent(self):
        self.assertIsNone(decode_gcd(None,1090,1000)["remaining_ms"])
        self.assertIsNone(decode_gcd(1000,900,1000)["remaining_ms"])
        self.assertIsNone(decode_gcd(1000,1090,900)["remaining_ms"])

    def test_reconnect_deadline_does_not_slide(self):
        t=Telemetry()
        with patch("telemetry.processes", return_value=[]), patch("telemetry.time.perf_counter",return_value=10):
            self.assertEqual(t.sample()["status"],"unavailable")
        self.assertEqual(t.retry_after,11)
        with patch("telemetry.processes") as process_list, patch("telemetry.time.perf_counter",return_value=10.5):
            self.assertEqual(t.sample()["status"],"unavailable")
            process_list.assert_not_called()
        self.assertEqual(t.retry_after,11)
        with patch("telemetry.processes",return_value=[]) as process_list, patch("telemetry.time.perf_counter",return_value=11.1):
            t.sample()
            process_list.assert_called_once()

if __name__ == "__main__":
    unittest.main()
