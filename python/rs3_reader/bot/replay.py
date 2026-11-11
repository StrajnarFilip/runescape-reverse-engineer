"""Stream captured JSONL through a decision stack; log proposals, send no input.

Run with PYTHONPATH=python::

    python -m rs3_reader.bot.replay capture.jsonl --stack null
    python -m rs3_reader.bot.replay capture.jsonl --stack my_decisions --output intents.jsonl

A trusted stack module exports ``create_stack()`` returning an object with
``step(world: WorldState) -> Iterable[Intent]``. It can compose trackers and
producers. The harness supplies neither an actuator nor a clock: replay uses
only captured monotonic time. The default null stack emits no proposals.
One deterministic output row is written per input record, in file order.
The harness reports boundaries/gaps; D1/D8 own their policy and resets.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Iterable, Protocol

from .intents import Intent
from .world_state import CONTRACT_VERSION, WorldState, to_jsonable


class DecisionStack(Protocol):
    def step(self, world: WorldState) -> Iterable[Intent]:
        """Consume one sample and propose actions; do not dispatch them."""
        ...


class NullStack:
    def step(self, world):
        """Exercise the complete replay path without proposing an action."""
        return ()


def load_stack(module_name):
    """Load the explicitly selected local stack, once per replay (no global singleton)."""
    if module_name == "null":
        return NullStack()
    factory = getattr(importlib.import_module(module_name), "create_stack", None)
    if not callable(factory):
        raise ValueError("Stack module must export create_stack()")
    stack = factory()
    if not callable(getattr(stack, "step", None)):
        raise ValueError("create_stack() must return an object with step(world)")
    return stack


def _reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def read_capture(stream):
    """Yield validated JSON objects, reporting physical line numbers on errors."""
    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_constant=_reject_constant)
            if not isinstance(row, dict):
                raise ValueError("Capture row must be an object")
        except ValueError as error:
            raise ValueError("Capture line {}: {}".format(line_number, error)) from error
        yield line_number, row


def replay(stream, output, stack):
    """Write intent logs in capture order; never sleep, sort, send or acknowledge."""
    previous = None
    count = 0
    for line_number, row in read_capture(stream):
        try:
            world = WorldState.from_telemetry(row)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("Capture line {}: {}".format(line_number, error)) from error
        boundaries = []
        if previous is None:
            boundaries.append("initial")
        else:
            if world.session != previous.session:
                boundaries.append("session_changed")
            if (
                world.now_ns is not None
                and previous.now_ns is not None
                and world.now_ns < previous.now_ns
            ):
                boundaries.append("clock_regressed")
            if (
                world.session == previous.session
                and world.sample_sequence is not None
                and previous.sample_sequence is not None
                and world.sample_sequence < previous.sample_sequence
            ):
                boundaries.append("sequence_regressed")
        intents = tuple(stack.step(world))
        if any(not isinstance(intent, Intent) for intent in intents):
            raise TypeError("Decision stack must return an iterable of Intent objects")
        log = {
            "schema_version": CONTRACT_VERSION,
            "sample_index": count + 1,
            "sample_sequence": world.sample_sequence,
            "session": to_jsonable(world.session),
            "observed_ns": world.observed_ns,
            "now_ns": world.now_ns,
            "boundaries": boundaries,
            "event_losses": {
                name: getattr(world.events, name).lost.value
                for name in (
                    "npc_actions",
                    "npc_hits",
                    "player_hits",
                    "inventory_changes",
                    "manual_actions",
                )
            },
            "intents": [intent.to_record() for intent in intents],
            "input_sent": False,
        }
        output.write(json.dumps(log, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        count += 1
        previous = world
    return count


def main(argv=None):
    """CLI with diagnostics on stderr and JSON Lines only on stdout/output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", type=Path)
    parser.add_argument("--stack", default="null", help="Trusted module exporting create_stack()")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output and arguments.capture.resolve() == arguments.output.resolve():
        parser.error("Capture and output must be different files")
    try:
        stack = load_stack(arguments.stack)
        with arguments.capture.open(encoding="utf-8-sig") as capture:
            if arguments.output:
                with arguments.output.open("w", encoding="utf-8", newline="\n") as output:
                    replay(capture, output, stack)
            else:
                replay(capture, sys.stdout, stack)
    except (OSError, ValueError, TypeError, ImportError) as error:
        print("Replay error: {}".format(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
