"""Lossless, versioned deltas for already-decoded native telemetry and decisions.

This module never samples the game or drains queues. It accepts the same owned
JSON records used by the controller, so event loss, unknowns and input outcomes
survive replay without a second reader consuming their queues.
"""

from __future__ import annotations

import copy
import json
import time

SCHEMA_VERSION = 2


def _difference(previous, current, path, replacements, removals):
    if type(previous) is not type(current):
        replacements.append([path, current])
    elif isinstance(current, dict):
        removals.extend(path + [key] for key in previous if key not in current)
        for key, value in current.items():
            if key not in previous:
                replacements.append([path + [key], value])
            else:
                _difference(previous[key], value, path + [key], replacements, removals)
    elif isinstance(current, list) and len(previous) == len(current):
        for index, value in enumerate(current):
            _difference(previous[index], value, path + [index], replacements, removals)
    elif previous != current:
        replacements.append([path, current])


class TelemetryAttemptLog:
    """Write one initial record and exact tree deltas; caller owns the stream.

    Paths are arrays of dictionary keys or list indexes, avoiding string-path
    escaping. Replacing an array of a different length preserves removals and
    order. Timestamps are copied from the native record; recording time is a
    separate field and never substituted for an unknown historical event time.
    """

    def __init__(self, stream, *, wall_clock_ns=time.time_ns):
        self.stream = stream
        self.wall_clock_ns = wall_clock_ns
        self.previous = None
        self.sequence = 0

    def record(self, source):
        """Persist one JSON-owned record before returning, including unchanged samples."""
        # JSON round-tripping freezes caller ownership and normalizes tuples and
        # integer object keys exactly as the original JSONL output would do.
        current = json.loads(json.dumps(source, allow_nan=False))
        if not isinstance(current, dict):
            raise ValueError("Attempt records must be JSON objects")
        telemetry = current.get("telemetry") or {}
        timestamp = telemetry.get("timestamp_ms", current.get("timestamp_ms"))
        packet = {
            "schema_version": SCHEMA_VERSION,
            "event": "record_delta",
            "sequence": self.sequence + 1,
            "source_kind": current.get("kind"),
            "timestamp_ms": timestamp,
            "timestamp_reason": None if timestamp is not None else "source_timestamp_unavailable",
            "recorded_at_ms": self.wall_clock_ns() // 1_000_000,
            "observed_ns": current.get("observed_ns"),
            "frame_sequence": (telemetry.get("native") or {}).get("sample_sequence"),
            "client_logic_tick": (telemetry.get("freshness") or {}).get("client_logic_tick"),
            "game_tick": (telemetry.get("game_tick") or {}).get("value"),
        }
        if self.previous is None:
            packet["initial"] = current
        else:
            replacements, removals = [], []
            _difference(self.previous, current, [], replacements, removals)
            packet["set"], packet["remove"] = replacements, removals
        self.stream.write(json.dumps(packet, separators=(",", ":"), allow_nan=False) + "\n")
        self.stream.flush()
        self.previous = current
        self.sequence += 1


def _parent(record, path):
    if not isinstance(path, list) or not path:
        raise ValueError("Delta paths must be nonempty arrays")
    value = record
    for index, component in enumerate(path):
        if isinstance(value, dict):
            if not isinstance(component, str):
                raise ValueError("Object delta paths require string keys")
        elif isinstance(value, list):
            if type(component) is not int or not 0 <= component < len(value):
                raise ValueError("Array delta paths require an existing index")
        else:
            raise ValueError("Delta path has no container")
        if index == len(path) - 1:
            return value, component
        value = value[component]


def read_attempt_records(stream):
    """Yield independent original records; reject missing or reordered delta rows.

    This reader is for schema 2 files. The earlier standalone controller's
    schema 1 logs retain their existing event format.
    """
    previous, expected = None, 1
    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            packet = json.loads(line, parse_constant=_invalid_number)
            if (
                packet.get("schema_version") != SCHEMA_VERSION
                or packet.get("event") != "record_delta"
                or type(packet.get("sequence")) is not int
                or packet["sequence"] != expected
            ):
                raise ValueError("Unexpected attempt schema or sequence")
            if previous is None:
                current = packet["initial"]
                if not isinstance(current, dict) or "set" in packet or "remove" in packet:
                    raise ValueError("Initial record must be an object")
            else:
                if "initial" in packet:
                    raise ValueError("An initial record is only valid at sequence 1")
                current = copy.deepcopy(previous)
                for path in packet["remove"]:
                    parent, key = _parent(current, path)
                    if not isinstance(parent, dict):
                        raise ValueError("Array removal requires an array replacement")
                    del parent[key]
                for path, value in packet["set"]:
                    if path == []:
                        if not isinstance(value, dict):
                            raise ValueError("Root replacement must be an object")
                        current = value
                    else:
                        parent, key = _parent(current, path)
                        parent[key] = value
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
            raise ValueError(f"Attempt line {line_number}: {error}") from error
        previous = copy.deepcopy(current)
        expected += 1
        yield current


def _invalid_number(value):
    raise ValueError("Non-finite JSON number: " + value)
