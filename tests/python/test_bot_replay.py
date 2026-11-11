"""Golden replay tests run without a client, native DLL or input backend."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rs3_reader.bot.replay import NullStack, load_stack, read_capture, replay

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "bot"
CAPTURE = FIXTURES / "synthetic_capture.jsonl"


def fixture_stack():
    spec = importlib.util.spec_from_file_location("golden_stack", FIXTURES / "golden_stack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_stack()


class ReplayTests(unittest.TestCase):
    def run_replay(self, stack, text=None):
        output = io.StringIO()
        count = replay(
            io.StringIO(text if text is not None else CAPTURE.read_text(encoding="utf-8")),
            output,
            stack,
        )
        return count, output.getvalue()

    def test_null_stack_matches_golden_file(self):
        count, log = self.run_replay(NullStack())
        self.assertEqual(count, 5)
        self.assertEqual(log, (FIXTURES / "null_intents.jsonl").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in log.splitlines()]
        self.assertTrue(all(row["intents"] == [] and not row["input_sent"] for row in rows))
        self.assertEqual(rows[1]["event_losses"]["npc_actions"], 2)
        self.assertEqual(rows[2]["event_losses"]["npc_actions"], None)
        self.assertIn("session_changed", rows[2]["boundaries"])
        self.assertIn("clock_regressed", rows[3]["boundaries"])

    def test_proposals_match_golden_without_becoming_inputs(self):
        _, log = self.run_replay(fixture_stack())
        self.assertEqual(log, (FIXTURES / "golden_intents.jsonl").read_text(encoding="utf-8"))
        first = json.loads(log.splitlines()[0])
        self.assertEqual(
            [intent["lane"] for intent in first["intents"]], ["food", "potion", "gcd", "prayer"]
        )
        self.assertEqual(len({intent["issued_ns"] for intent in first["intents"]}), 1)
        self.assertFalse(first["input_sent"])
        _, repeat = self.run_replay(fixture_stack())
        self.assertEqual(log, repeat)

    def test_order_and_regressions_are_reported_without_sorting_or_dropping(self):
        row = {
            "schema_version": 1,
            "status": "partial",
            "pid": 1,
            "process_creation_time": 2,
            "combat": {"scene_epoch": 3},
        }
        text = "\n".join(
            json.dumps(dict(row, monotonic_ms=time, native={"sample_sequence": sequence}))
            for time, sequence in ((20, 2), (10, 1), (10, 1))
        )
        count, log = self.run_replay(NullStack(), text)
        rows = [json.loads(line) for line in log.splitlines()]
        self.assertEqual(count, 3)
        self.assertEqual([row["observed_ns"] for row in rows], [20_000_000, 10_000_000, 10_000_000])
        self.assertEqual(rows[1]["boundaries"], ["clock_regressed", "sequence_regressed"])
        self.assertEqual(rows[2]["boundaries"], [])

    def test_empty_capture_and_blank_lines(self):
        self.assertEqual(self.run_replay(NullStack(), "\n\n"), (0, ""))
        self.assertEqual(list(read_capture(io.StringIO("\n{}\n"))), [(2, {})])

    def test_bad_json_schema_and_nonfinite_values_report_line(self):
        for line in ("[]", "{", '{"value":NaN}', '{"schema_version":2}'):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "Capture line 2"):
                self.run_replay(NullStack(), "\n" + line)

    def test_invalid_plugin_or_non_intent_output_fails(self):
        for module in (SimpleNamespace(), SimpleNamespace(create_stack=lambda: object())):
            with patch("rs3_reader.bot.replay.importlib.import_module", return_value=module):
                with self.assertRaises(ValueError):
                    load_stack("example")
        with self.assertRaisesRegex(TypeError, "Intent"):
            self.run_replay(SimpleNamespace(step=lambda world: [{"action": "magic"}]))

    def test_cli_loads_trusted_module_and_never_imports_native(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "python"), str(FIXTURES)))
        environment["RS3_NATIVE_LIBRARY"] = "this-dll-does-not-exist"
        script = (
            "import sys; from rs3_reader.bot.replay import main; "
            "result=main(); assert 'rs3_reader.native' not in sys.modules; "
            "assert 'rs3_reader.telemetry' not in sys.modules; raise SystemExit(result)"
        )
        for stack, expected in (
            ("null", "null_intents.jsonl"),
            ("golden_stack", "golden_intents.jsonl"),
        ):
            result = subprocess.run(
                [sys.executable, "-c", script, str(CAPTURE), "--stack", stack],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout, (FIXTURES / expected).read_text(encoding="utf-8"))

    def test_cli_module_entrypoint_and_output_file(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "intents.jsonl"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "rs3_reader.bot.replay",
                    str(CAPTURE),
                    "--stack",
                    "null",
                    "--output",
                    str(output),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                (FIXTURES / "null_intents.jsonl").read_text(encoding="utf-8"),
            )

    def test_cli_cannot_truncate_capture_and_errors_stay_on_stderr(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
        original = CAPTURE.read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "rs3_reader.bot.replay", str(CAPTURE), "--output", str(CAPTURE)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("different files", result.stderr)
        self.assertEqual(CAPTURE.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
