"""D1 deterministic encounter observations; no process, DLL or input access.

``step`` accepts S0a WorldState. Labels need an exact phase/enrage/client scope.
The four historical fallback labels apply only to 300% phase 1. A labelled cue
with no measured protection window produces an unresolved IncomingThreat, not
a guessed deadline. ``prayer_record`` returns None for such threats. Coverage,
cycle position and next attack remain unknown until their evidence exists.

Load P6 JSON directly to keep replay independent of native ctypes bindings.
Only track L composes this tracker with D2 and an actuator. ``create_stack``
is an observe-only S0a replay adapter: it proposes no intents.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .world_state import SessionIdentity, WorldState, freeze

NS_PER_MS = 1_000_000
MAXIMUM_AGE_MS = 150
MAXIMUM_EVENT_AGE_MS = 1500
MAXIMUM_PENDING = 128
MAXIMUM_RECENT_EVENTS = 4096
ACTIVE_TELOS_CONFIG = 22891
PRAYER_IDS = {"none": 1, "melee": 2, "magic": 3, "ranged": 4, "necromancy": 6}
HISTORICAL_CLIENT = "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45"
HISTORICAL_LABELS = {28929: "melee auto", 28931: "tendrils", 28938: "uppercut", 28935: "Hold Still"}
CATALOGUE_PATH = Path(__file__).resolve().parents[3] / "profiles" / "telos_attacks.json"


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _fresh(observation, maximum_age_ms):
    return (
        bool(observation.source)
        and observation.reason is None
        and observation.is_fresh(maximum_age_ms)
    )


@dataclass(frozen=True)
class IncomingThreat:
    """A cue is distinct from a calibrated protection-check window.

    IDs are tracker-local monotonic integers, never reused across resets. Null
    windows/prayer and flags=0 deliberately cannot enter the native planner.
    Damage bounds and defensive eligibility require separate measured evidence.
    """

    event_id: int
    source_instance: int
    sequence: int
    observed_ns: int
    name: str
    classification_validated: bool
    check_earliest_ns: Optional[int] = None
    check_latest_ns: Optional[int] = None
    prayer: Optional[int] = None
    flags: int = 0
    reason: Optional[str] = "protection_check_unmeasured"
    damage_upper_bound: Optional[int] = None
    defensive_options: Tuple[str, ...] = ()

    def prayer_record(self):
        """Return rs3_prayer_threat fields only with validated, complete timing."""
        if (
            self.flags != 1
            or self.check_earliest_ns is None
            or self.check_latest_ns is None
            or self.prayer is None
        ):
            return None
        return {
            key: getattr(self, key)
            for key in (
                "event_id",
                "source_instance",
                "check_earliest_ns",
                "check_latest_ns",
                "prayer",
                "flags",
            )
        }


@dataclass(frozen=True)
class EncounterState:
    session: SessionIdentity
    observed_ns: Optional[int]
    phase: Optional[int]
    attack_index: Optional[int]
    observed_auto_count: int
    next_attack: Optional[int]
    confidence: str
    pending_threats: Tuple[IncomingThreat, ...]
    coverage_until_ns: Optional[int]
    reasons: Tuple[str, ...]


class AttackTable:
    """Read-only exact-scope P6 rows; no phase, style or cadence fallback."""

    def __init__(self, document=None):
        self.rows = ()
        self.historical_fallback = document is None
        if document is None:
            return
        if document.get("schema_version") != 1 or not isinstance(document.get("attacks"), list):
            raise ValueError("Expected a version-1 P6 attack table")
        rows = []
        for row in document["attacks"]:
            band = row.get("enrage_range")
            digest = row.get("client_sha256")
            if (
                type(row.get("phase")) is not int
                or row["phase"] not in range(1, 6)
                or not _integer(row.get("sequence"))
            ):
                raise ValueError("Invalid phase or sequence")
            if (
                not isinstance(band, list)
                or len(band) != 2
                or not all(_integer(value) for value in band)
                or not 0 <= band[0] <= band[1] <= 4000
            ):
                raise ValueError("Invalid enrage band")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("An exact executable digest is required")
            if row.get("classification_validated") is True and (
                row.get("evidence_kind") != "user_labelled_native_capture"
                or not _integer(row.get("sample_count"), 1)
            ):
                raise ValueError("Classification requires labelled native samples")
            rows.append(freeze(row))
        self.rows = tuple(rows)

    @classmethod
    def load(cls, path=CATALOGUE_PATH):
        """Missing P6 file uses the explicitly restricted historical labels."""
        path = Path(path)
        return cls(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else cls()

    def lookup(self, phase, enrage, sequence, digest, context):
        if self.historical_fallback:
            if (
                phase != 1
                or enrage != 300
                or digest != HISTORICAL_CLIENT
                or sequence not in HISTORICAL_LABELS
                or context != "isolated_p1_melee"
            ):
                return None
            return {
                "name": HISTORICAL_LABELS[sequence],
                "classification_validated": True,
                "counts_as_auto": sequence == 28929,
                "required_protection": "melee" if sequence == 28929 else None,
                "evidence_kind": "user_labelled_native_capture",
                "timing_validated": False,
                "validated": False,
            }
        matches = [
            row
            for row in self.rows
            if row["phase"] == phase
            and row["sequence"] == sequence
            and row["client_sha256"] == digest
            and row.get("context") == context
            and row["enrage_range"][0] <= enrage <= row["enrage_range"][1]
        ]
        return matches[0] if len(matches) == 1 else None


class EncounterTracker:
    """Stateful native-event consumer. Calls must be serialized by its owner.

    Snapshot/event identities and clocks are checked independently. Queue loss,
    unknown loss counts, sequence gaps, stale data and scene changes invalidate
    accumulated state. A current labelled event may still describe a threat
    after a gap, but never establishes complete incoming-attack coverage.
    """

    def __init__(
        self,
        table=None,
        *,
        context=None,
        maximum_age_ms=MAXIMUM_AGE_MS,
        maximum_event_age_ms=MAXIMUM_EVENT_AGE_MS,
        maximum_pending=MAXIMUM_PENDING,
    ):
        if any(
            type(value) is not int or value <= 0
            for value in (maximum_age_ms, maximum_event_age_ms, maximum_pending)
        ):
            raise ValueError("Tracker bounds must be positive integers")
        self.table = table if table is not None else AttackTable.load()
        self.context = context
        self.maximum_age_ms, self.maximum_event_age_ms = maximum_age_ms, maximum_event_age_ms
        self.maximum_pending = maximum_pending
        self._identity, self._sample, self._now, self._phase = None, None, None, None
        self._last_event, self._next_id = None, 1
        self._seen = {}
        self._pending, self._auto_count = [], 0
        self._index, self._next = None, None

    def _invalidate(self):
        self._pending, self._auto_count = [], 0
        self._index, self._next = None, None

    def _threats(self, event, row):
        window = row.get("cue_to_prayer_check_ms")
        prayer = PRAYER_IDS.get(row.get("required_protection"))
        valid = (
            row.get("validated") is True
            and row.get("timing_validated") is True
            and row.get("classification_validated") is True
        )
        valid = (
            valid
            and row.get("timing_evidence_kind") == "controlled_protection_trial"
            and row.get("evidence_kind") == "user_labelled_native_capture"
        )
        valid = (
            valid and prayer is not None and isinstance(window, (tuple, list)) and len(window) == 2
        )
        valid = (
            valid
            and all(type(value) in (int, float) and math.isfinite(value) for value in window)
            and 0 <= window[0] <= window[1]
        )
        # A multi-hit attack needs separate measured component windows. Never
        # represent an entire multi-hit mechanic as one calibrated hitsplat.
        components = row.get("hit_components")
        valid = valid and isinstance(components, (tuple, list)) and len(components) == 1
        self._pending.append(
            IncomingThreat(
                self._next_id,
                event["instance_id"],
                event["action_sequence"],
                event["observed_ns"],
                row["name"],
                True,
                event["observed_ns"] + round(window[0] * NS_PER_MS) if valid else None,
                event["observed_ns"] + round(window[1] * NS_PER_MS) if valid else None,
                prayer if valid else None,
                1 if valid else 0,
                None if valid else "protection_check_unmeasured",
            )
        )
        self._next_id += 1

    def step(self, world):
        """Return immutable state; propose no inputs and never invent safe idle time."""
        if not isinstance(world, WorldState):
            raise TypeError("EncounterTracker requires WorldState")
        reasons = []
        identity = (world.session, world.raw.get("executable_sha256"))
        if identity != self._identity:
            self._invalidate()
            self._last_event, self._sample, self._now, self._phase = None, None, None, None
            self._seen.clear()
            self._identity = identity
            reasons.append("session_or_build_changed")
        fresh = (
            world.session.complete
            and world.status in ("available", "partial")
            and _integer(world.now_ns)
            and _integer(world.observed_ns)
            and 0 <= world.now_ns - world.observed_ns <= self.maximum_age_ms * NS_PER_MS
        )
        if not fresh or not _integer(world.sample_sequence, 1):
            self._invalidate()
            return self._state(world, None, ("snapshot_unavailable_or_stale",))
        if (self._now is not None and world.now_ns < self._now) or (
            self._sample is not None and world.sample_sequence < self._sample
        ):
            self._invalidate()
            self._last_event = None
            self._seen.clear()
            self._now, self._sample = world.now_ns, world.sample_sequence
            return self._state(world, None, ("clock_or_sample_regressed",))
        self._now, self._sample = world.now_ns, world.sample_sequence
        phase = (
            world.telos.phase.value
            if _fresh(world.telos.phase, self.maximum_age_ms)
            and type(world.telos.phase.value) is int
            and world.telos.phase.value in range(1, 6)
            else None
        )
        if phase != self._phase:
            # Pending impacts can cross a phase boundary. Preserve them; only
            # cycle position becomes unknown until a measured anchor is seen.
            self._index, self._next, self._phase = None, None, phase
        enrage = (
            world.telos.enrage.value if _fresh(world.telos.enrage, self.maximum_age_ms) else None
        )
        if phase is None:
            reasons.append("phase_unknown")
        batch = world.events.npc_actions
        if (
            not batch.records.known
            or type(batch.lost.value) is not int
            or batch.lost.value != 0
            or batch.lost.reason is not None
            or not batch.lost.source
        ):
            self._invalidate()
            self._last_event = None
            self._seen.clear()
            reasons.append("npc_event_queue_gap_or_unknown")
        npcs_known = _fresh(world.npcs, self.maximum_age_ms)
        actors = {
            npc.instance_id.value
            for npc in world.npcs.value or ()
            if npcs_known
            and _fresh(npc.instance_id, self.maximum_age_ms)
            and _fresh(npc.name, self.maximum_age_ms)
            and _fresh(npc.config_id, self.maximum_age_ms)
            and npc.config_id.value == ACTIVE_TELOS_CONFIG
            and npc.name.value.partition(",")[0].strip().casefold() == "telos"
        }
        if len(actors) != 1:
            self._invalidate()
            actors = set()
            reasons.append("telos_not_observed_or_ambiguous")
        if batch.records.reason is not None:
            self._invalidate()
            return self._state(world, phase, ("action_records_invalid",))
        for event in batch.records.value or ():
            session = SessionIdentity(
                event.get("pid"), event.get("process_creation_time"), event.get("scene_epoch")
            )
            if session != world.session:
                reasons.append("foreign_session_event")
                continue
            sequence, observed = event.get("sequence"), event.get("observed_ns")
            if (
                not _integer(sequence, 1)
                or not _integer(observed)
                or not 0 <= world.now_ns - observed <= self.maximum_event_age_ms * NS_PER_MS
            ):
                self._invalidate()
                reasons.append("invalid_or_stale_event")
                continue
            if self._last_event is not None and sequence <= self._last_event:
                if self._seen.get(sequence) != event:
                    self._invalidate()
                    reasons.append("event_sequence_regressed_or_reused")
                continue  # Exact duplicate drains never create a second threat.
            if self._last_event is not None and sequence != self._last_event + 1:
                self._invalidate()
                reasons.append("event_sequence_gap")
            self._last_event = sequence
            self._seen[sequence] = event
            if len(self._seen) > MAXIMUM_RECENT_EVENTS:
                del self._seen[next(iter(self._seen))]
            if event.get("instance_id") not in actors:
                continue
            row = (
                self.table.lookup(
                    phase, enrage, event.get("action_sequence"), identity[1], self.context
                )
                if type(enrage) in (int, float)
                else None
            )
            if (
                row is None
                or row.get("classification_validated") is not True
                or not row.get("name")
            ):
                self._index, self._next = None, None
                reasons.append("sequence_unlabelled_or_context_unknown")
                continue
            self._threats(event, row)
            self._next = None
            if row.get("counts_as_auto") is True:
                self._auto_count += 1
                if self._index is not None:
                    self._index += 1
            else:
                self._index, self._next = None, None
            cycle = row.get("cycle")
            if (
                isinstance(cycle, Mapping)
                and row.get("counts_as_auto") is False
                and cycle.get("validated") is True
                and cycle.get("evidence_kind") == "user_labelled_native_capture"
                and _integer(cycle.get("index_after"))
            ):
                self._index = cycle["index_after"]
                self._next = (
                    cycle.get("next_sequence") if _integer(cycle.get("next_sequence")) else None
                )
        self._pending = [
            threat
            for threat in self._pending
            if threat.check_latest_ns is None or threat.check_latest_ns >= world.now_ns
        ]
        if len(self._pending) > self.maximum_pending:
            self._invalidate()
            reasons.append("pending_threat_overflow")
        reasons.append("incoming_attack_coverage_uncalibrated")
        return self._state(world, phase, tuple(dict.fromkeys(reasons)))

    def _state(self, world, phase, reasons):
        return EncounterState(
            world.session,
            world.observed_ns,
            phase,
            self._index,
            self._auto_count,
            self._next,
            (
                "measured_cycle_anchor"
                if self._next is not None
                else "labelled_cues_only" if self._pending else "unknown"
            ),
            tuple(self._pending),
            None,
            reasons,
        )


class EncounterObserver:
    """S0a replay adapter. Inspect ``state`` after each step; no Intent is emitted."""

    def __init__(self, tracker=None):
        self.tracker = tracker if tracker is not None else EncounterTracker()
        self.state = None

    def step(self, world):
        self.state = self.tracker.step(world)
        return ()


def create_stack():
    """Construct a fresh observe-only tracker for ``replay --stack``."""
    return EncounterObserver()
