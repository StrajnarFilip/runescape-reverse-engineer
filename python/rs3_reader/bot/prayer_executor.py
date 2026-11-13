"""D2 gated adapter to the existing C prayer planner; proposes, never sends.

Import and construction do not open a DLL. The native adapter is loaded only
for an explicitly calibrated band or direct test construction. P6 supplies
validation, D1 supplies pending threats/coverage, and D6 reports accepted input.
Unknown scope or timing produces no toggle, including no Soul Split toggle.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .intents import ActionLane, Evidence, Intent, Priority, SetPrayer
from .world_state import Observation, WorldState, freeze

ROOT = Path(__file__).resolve().parents[3]
PROFILE_PATH = ROOT / "profiles/prayer_flick.json"
CATALOGUE_PATH = ROOT / "profiles/telos_attacks.json"
NS_PER_MS = 1_000_000
RAW_PRAYER_UNITS_PER_DISPLAY_POINT = (
    10  # NativeTelemetry decodes the packed prayer value in tenths.
)
PRAYERS = {"none": 1, "melee": 2, "magic": 3, "ranged": 4, "soul_split": 5, "necromancy": 6}
PRAYER_NAMES = {value: key for key, value in PRAYERS.items()}
ACTION_SET, ACTION_WAIT_ACK, ACTION_FAULT = 2, 3, 4
CONNECTED, ACTIVE_KNOWN, POINTS_KNOWN, COVERAGE_KNOWN = 1, 2, 4, 8


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _timed(row):
    window = row.get("cue_to_prayer_check_ms")
    return (
        row.get("validated") is True
        and row.get("classification_validated") is True
        and row.get("timing_validated") is True
        and _integer(row.get("sample_count"), 1)
        and row.get("evidence_kind") == "user_labelled_native_capture"
        and row.get("timing_evidence_kind") == "controlled_protection_trial"
        and isinstance(window, (list, tuple))
        and len(window) == 2
        and all(_number(value) for value in window)
        and 0 <= window[0] <= window[1]
        and row.get("required_protection") in PRAYERS
        and row["required_protection"] != "soul_split"
    )


def derive_validation_bands(catalogue):
    """Derive phase/hash/enrage flags from P6; profile booleans cannot upgrade it."""
    groups = {}
    if catalogue.get("schema_version") != 1:
        raise ValueError("Expected version-1 P6 metadata")
    for row in catalogue.get("attacks", ()):
        band = row.get("enrage_range")
        if (
            type(row.get("phase")) is not int
            or row["phase"] not in range(1, 6)
            or not isinstance(band, (tuple, list))
            or len(band) != 2
            or not all(_integer(value) for value in band)
            or not 0 <= band[0] <= band[1] <= 4000
        ):
            raise ValueError("Invalid catalogue phase/enrage scope")
        key = (row.get("client_sha256"), row["phase"], tuple(band), row.get("context"))
        if (
            not isinstance(key[0], str)
            or len(key[0]) != 64
            or any(character not in "0123456789abcdef" for character in key[0])
        ):
            raise ValueError("An exact executable digest is required")
        groups.setdefault(key, []).append(row)
    result = []
    for (digest, phase, band, context), rows in groups.items():
        timing = bool(rows) and all(_timed(row) for row in rows)
        coverage = (
            timing
            and catalogue.get("automatic_flick_ready") is True
            and any(
                entry.get("phase") == phase
                and tuple(entry.get("enrage_range", ())) == band
                and entry.get("client_sha256") == digest
                and entry.get("context") == context
                and entry.get("complete") is True
                for entry in catalogue.get("coverage", ())
            )
        )
        result.append(
            {
                "client_sha256": digest,
                "phase": phase,
                "enrage_range": list(band),
                "context": context,
                "prayer_check_deadline_validated": timing,
                "attack_coverage_validated": bool(coverage),
            }
        )
    return freeze(result)


class NativePrayerPlanner:
    """Use rs3_reader.prayer.Planner without duplicating the C timing policy."""

    def __init__(self, settings, library=None):
        from ..prayer import Frame, Planner, Threat

        self.Frame, self.Threat = Frame, Threat
        self.planner = Planner(settings, library)

    def step(self, frame, threats):
        decision = self.planner.step(
            self.Frame(**frame), [self.Threat(**threat) for threat in threats]
        )
        return {
            **{
                name: getattr(decision, name)
                for name in (
                    "command_id",
                    "threat_id",
                    "next_wakeup_ns",
                    "check_earliest_ns",
                    "check_latest_ns",
                    "action",
                    "prayer",
                    "flags",
                )
            },
            "reason": self.planner.describe(decision)["reason"],
        }

    def note_sent(self, command_id, sent_ns):
        self.planner.note_sent(command_id, sent_ns)

    def reset(self):
        self.planner.reset()

    def close(self):
        self.planner.close()


@dataclass(frozen=True)
class PrayerReport:
    reason: str
    fault: Optional[str]
    phase: Optional[int]
    waiting_ack: bool = False


class PrayerExecutor:
    """Feed validated D1/P6 observations to C and propose PRAYER-priority intents.

    A supplied planner is caller-owned. The lazily created default planner is
    owned and closed here. Calls must be serialized. reset() requires explicit
    re-observation; scene/build changes do not silently reset accepted input.
    """

    def __init__(
        self, settings=None, catalogue=None, *, planner=None, encounter_provider=None, context=None
    ):
        self.settings = freeze(
            settings if settings is not None else json.loads(PROFILE_PATH.read_text())
        )
        self.catalogue = freeze(
            catalogue
            if catalogue is not None
            else (
                json.loads(CATALOGUE_PATH.read_text())
                if CATALOGUE_PATH.exists()
                else {"schema_version": 1, "attacks": [], "coverage": []}
            )
        )
        self.bands = derive_validation_bands(self.catalogue)
        self.planner, self._owns_planner = planner, planner is None
        self.encounter_provider, self.context = encounter_provider, context
        self._identity, self._session_number, self._fault = None, 0, None
        self._closed = False
        self._offers, self._last_encounter = {}, None
        self.last_report = PrayerReport("timing_band_unvalidated", None, None)

    def close(self):
        self._closed = True
        if self._owns_planner and self.planner is not None:
            self.planner.close()
            self.planner = None

    def reset(self):
        """Explicitly clear planner faults after safely re-observing the client."""
        if self._closed:
            raise RuntimeError("Prayer executor is closed")
        if self.planner is not None:
            self.planner.reset()
        self._identity, self._fault, self._offers = None, None, {}

    def _fresh(self, observation):
        return (
            bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(self.settings["timing"]["max_age_ms"])
        )

    def _binding_available(self, world, prayer):
        binding = self.settings["bindings"].get(prayer)
        if binding is None:
            return False
        if not world.action_bar.known:
            return True  # Explicit user-calibrated profile until P5 is present.
        if not self._fresh(world.action_bar):
            return False
        matches = [
            entry
            for entry in world.action_bar.value
            if entry.get("kind") == "prayer"
            and entry.get("ability_struct_id") == binding.get("action_bar_struct_id")
        ]
        expected = ("Ctrl+" if binding["modifiers"] == 1 else "") + binding["key"]
        return (
            len(matches) == 1
            and matches[0].get("source")
            and matches[0].get("reason") is None
            and matches[0].get("binding_reason") is None
            and matches[0].get("key") == expected
        )

    def _assemble(self, world, encounter):
        maximum_ns = self.settings["timing"]["max_age_ms"] * NS_PER_MS
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or not _integer(world.now_ns)
            or not _integer(world.observed_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0 <= world.now_ns - world.observed_ns <= maximum_ns
        ):
            return None, "snapshot_unavailable_or_stale"
        phase, enrage, digest = (
            world.telos.phase.value,
            world.telos.enrage.value,
            world.raw.get("executable_sha256"),
        )
        if (
            not self._fresh(world.telos.phase)
            or type(phase) is not int
            or not self._fresh(world.telos.enrage)
            or not _number(enrage)
        ):
            return None, "phase_or_enrage_unknown"
        bands = [
            band
            for band in self.bands
            if band["phase"] == phase
            and band["client_sha256"] == digest
            and band.get("context") == self.context
            and band["enrage_range"][0] <= enrage <= band["enrage_range"][1]
        ]
        if len(bands) != 1 or not bands[0]["prayer_check_deadline_validated"]:
            return None, "timing_band_unvalidated"
        encounter_ns = _field(encounter, "observed_ns")
        if (
            _field(encounter, "session") != world.session
            or not _integer(encounter_ns)
            or not 0 <= world.now_ns - encounter_ns <= maximum_ns
        ):
            return None, "encounter_unavailable_or_stale"
        active, points = world.player.active_prayer, world.player.prayer.current
        if (
            not self._fresh(active)
            or active.value not in PRAYERS
            or not self._fresh(points)
            or not _number(points.value)
            or not 0 < points.value * RAW_PRAYER_UNITS_PER_DISPLAY_POINT <= 0xFFFFFFFF
        ):
            return None, "prayer_state_unavailable"
        threats = []
        for threat in _field(encounter, "pending_threats", ()):
            rows = [
                row
                for row in self.catalogue["attacks"]
                if row["phase"] == phase
                and row["client_sha256"] == digest
                and row.get("context") == self.context
                and row["sequence"] == _field(threat, "sequence")
                and row["enrage_range"][0] <= enrage <= row["enrage_range"][1]
            ]
            if (
                len(rows) != 1
                or not _timed(rows[0])
                or type(_field(threat, "flags")) is not int
                or _field(threat, "flags") != 1
                or _field(threat, "classification_validated") is not True
            ):
                return None, "pending_threat_unvalidated"
            row = rows[0]
            cue_ns = _field(threat, "observed_ns")
            if (
                not _integer(cue_ns)
                or cue_ns > world.now_ns
                or _field(threat, "prayer") != PRAYERS[row["required_protection"]]
            ):
                return None, "threat_scope_or_prayer_mismatch"
            expected = tuple(
                cue_ns + round(value * NS_PER_MS) for value in row["cue_to_prayer_check_ms"]
            )
            if (
                expected != (_field(threat, "check_earliest_ns"), _field(threat, "check_latest_ns"))
                or not _integer(_field(threat, "event_id"), 1)
                or not _integer(_field(threat, "source_instance"))
            ):
                return None, "threat_window_mismatch"
            threats.append(
                {
                    "event_id": _field(threat, "event_id"),
                    "source_instance": _field(threat, "source_instance"),
                    "check_earliest_ns": expected[0],
                    "check_latest_ns": expected[1],
                    "prayer": _field(threat, "prayer"),
                    "flags": 1,
                }
            )
        coverage = _field(encounter, "coverage_until_ns")
        covered = (
            bands[0]["attack_coverage_validated"]
            and _integer(coverage)
            and coverage >= world.now_ns
        )
        available = sum(
            1 << PRAYERS[name]
            for name in set(self.settings["available_prayers"])
            if name in PRAYERS
        )
        frame = {
            "now_ns": world.now_ns,
            "observed_ns": world.observed_ns,
            "session_id": self._session_number,
            "sequence": world.sample_sequence,
            "coverage_until_ns": coverage if covered else 0,
            "flags": CONNECTED | ACTIVE_KNOWN | POINTS_KNOWN | (COVERAGE_KNOWN if covered else 0),
            "active_prayer": PRAYERS[active.value],
            "available_mask": available,
            "prayer_points": round(points.value * RAW_PRAYER_UNITS_PER_DISPLAY_POINT),
        }
        return (frame, tuple(threats), bands[0]), None

    def _decide(self, world, encounter):
        assembled, reason = self._assemble(world, encounter)
        if assembled is None:
            return None, reason
        if self.planner is None:
            self.planner = NativePrayerPlanner(self.settings)
        frame, threats, band = assembled
        decision = self.planner.step(frame, threats)
        if decision["action"] == ACTION_FAULT:
            self._fault = "planner_fault:" + decision["reason"]
        if (
            decision["action"] == ACTION_SET
            and decision["prayer"] == PRAYERS["soul_split"]
            and not band["attack_coverage_validated"]
        ):
            return None, "soul_split_coverage_unvalidated"
        return decision, decision["reason"]

    def step(self, world, encounter=None):
        """Produce no toggle outside a calibrated phase/hash/enrage scope."""
        if not isinstance(world, WorldState):
            raise TypeError("PrayerExecutor requires WorldState")
        if self._closed:
            return ()
        self._offers = {}
        identity = (world.session, world.raw.get("executable_sha256"))
        if self._identity is None:
            self._identity = identity
            self._session_number += 1
        elif identity != self._identity:
            self._fault = "session_or_build_changed"
        encounter = (
            encounter
            if encounter is not None
            else self.encounter_provider() if self.encounter_provider else None
        )
        self._last_encounter = encounter
        if self._fault:
            self.last_report = PrayerReport(self._fault, self._fault, world.telos.phase.value)
            return ()
        decision, reason = self._decide(world, encounter)
        self.last_report = PrayerReport(
            reason,
            self._fault,
            world.telos.phase.value,
            bool(decision and decision["action"] == ACTION_WAIT_ACK),
        )
        if (
            decision is None
            or decision["action"] != ACTION_SET
            or decision.get("flags", 0) & 1
            or decision["prayer"] not in PRAYER_NAMES
            or decision["prayer"] == PRAYERS["none"]
        ):
            return ()
        earliest = decision.get("check_earliest_ns", 0)
        deadline = world.now_ns + self.settings["timing"]["max_age_ms"] * NS_PER_MS
        if earliest:
            deadline = min(deadline, earliest - 1)
        elif decision.get("next_wakeup_ns"):
            deadline = min(deadline, decision["next_wakeup_ns"] - 1)
        if deadline < world.now_ns:
            return ()
        prayer = PRAYER_NAMES[decision["prayer"]]
        binding = self.settings["bindings"].get(prayer)
        if binding is None or not self._binding_available(world, prayer):
            self.last_report = PrayerReport(
                "binding_unknown_or_changed", self._fault, world.telos.phase.value
            )
            return ()
        evidence = (
            Evidence("player.active_prayer", world.player.active_prayer),
            Evidence("player.prayer", world.player.prayer.current),
            Evidence("telos.phase", world.telos.phase),
            Evidence("telos.enrage", world.telos.enrage),
            Evidence(
                "prayer.planner",
                Observation(
                    {
                        "command_id": decision["command_id"],
                        "reason": reason,
                        "binding": dict(binding),
                    },
                    0,
                    "validated_C_prayer_planner",
                ),
            ),
        )
        intent = Intent(
            "d2:{}:{}:{}:{}:{}".format(
                world.session.pid,
                world.session.process_creation_time,
                world.session.scene_epoch,
                world.sample_sequence,
                decision["command_id"],
            ),
            "prayer",
            SetPrayer(prayer),
            Priority.PRAYER,
            ActionLane.PRAYER,
            world.now_ns,
            deadline,
            world.session,
            world.sample_sequence,
            evidence,
            world.observed_ns,
        )
        self._offers[intent.intent_id] = (intent, decision["command_id"], decision["prayer"])
        return (intent,)

    def can_send(self, intent, world):
        """D6 prayer validator: re-run C against fresh exact-scope evidence."""
        offer = self._offers.get(intent.intent_id)
        if (
            self._closed
            or offer is None
            or self._fault
            or intent.session != world.session
            or intent.expired(world.now_ns)
        ):
            return False
        encounter = self.encounter_provider() if self.encounter_provider else self._last_encounter
        decision, _ = self._decide(world, encounter)
        return bool(
            decision
            and decision["action"] == ACTION_SET
            and not decision.get("flags", 0) & 1
            and decision["command_id"] == offer[1]
            and decision["prayer"] == offer[2]
            and self._binding_available(world, PRAYER_NAMES[decision["prayer"]])
        )

    def note_sent(self, intent_id, sent_ns):
        """Pass only actual input acceptance to rs3_prayer_note_sent()."""
        offer = self._offers.get(intent_id)
        if (
            offer is None
            or not _integer(sent_ns)
            or not offer[0].issued_ns <= sent_ns <= offer[0].deadline_ns
        ):
            raise ValueError("Unknown or expired accepted prayer intent")
        self.planner.note_sent(offer[1], sent_ns)
        del self._offers[intent_id]

    def note_rejected(self, intent_id, reason, sent_ns):
        """Rejected input is not reported as sent to the C planner."""
        self.last_report = PrayerReport(
            "send_rejected:" + str(reason), self._fault, self.last_report.phase
        )


def create_stack():
    """Create a gated proposal-only replay producer; no live planner is armed."""
    return PrayerExecutor()
