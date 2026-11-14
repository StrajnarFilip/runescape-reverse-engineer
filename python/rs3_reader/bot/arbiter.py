"""D6 serialized single writer to an injected S0a Actuator.

Priority is survival > prayer > mechanics > rotation. Budgets apply per verified
server tick and per lane; there is no one-input-per-tick rule. Unknown tick,
stale data and unknown action preconditions never authorize input. A returned
acceptance is not game success. Only newer observations acknowledge it.

Construction is disabled by default. No native/serial backend is imported here.
``create_stack`` constructs an explicitly recording-only S0a replay adapter.
Track L owns live enablement, backend creation and supervisor integration.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from .actuator import RecordingActuator, SendResult
from .intents import ActionLane, ActivateAbility, ConsumeSlot, Intent, Interact, MoveTo, SetPrayer
from .world_state import Observation, SessionIdentity, WorldState, to_jsonable

NS_PER_MS = 1_000_000
EXCLUSIVE_LANES = {
    ActionLane.GCD,
    ActionLane.FOOD,
    ActionLane.POTION,
    ActionLane.PRAYER,
    ActionLane.POINTER,
}


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class ArbiterPolicy:
    maximum_age_ms: int = 150
    confirmation_timeout_ms: int = 1800
    maximum_inputs_per_tick: int = 12
    maximum_history: int = 4096
    maximum_pending: int = 128
    maximum_proposals: int = 256
    lane_limits: Mapping[ActionLane, int] = field(
        default_factory=lambda: {
            ActionLane.GCD: 1,
            ActionLane.OFF_GCD: 2,
            ActionLane.FOOD: 1,
            ActionLane.POTION: 1,
            ActionLane.PRAYER: 4,
            ActionLane.POINTER: 3,
        }
    )

    def __post_init__(self):
        for name in (
            "maximum_age_ms",
            "confirmation_timeout_ms",
            "maximum_inputs_per_tick",
            "maximum_history",
            "maximum_pending",
            "maximum_proposals",
        ):
            if not _integer(getattr(self, name), 1):
                raise ValueError("Arbiter bounds must be positive integers: " + name)
        limits = {ActionLane(key): value for key, value in self.lane_limits.items()}
        if set(limits) != set(ActionLane) or any(not _integer(value) for value in limits.values()):
            raise ValueError("Every lane needs an explicit nonnegative budget")
        object.__setattr__(self, "lane_limits", MappingProxyType(limits))


@dataclass(frozen=True)
class Decision:
    intent_id: str
    producer: str
    lane: str
    priority: int
    status: str
    reason: Optional[str]
    sent_ns: Optional[int] = None


@dataclass(frozen=True)
class DecisionLog:
    session: SessionIdentity
    sample_sequence: Optional[int]
    observed_ns: Optional[int]
    now_ns: Optional[int]
    game_tick: Optional[int]
    decisions: Tuple[Decision, ...]
    observations: Tuple[Mapping, ...]
    pending_ids: Tuple[str, ...]
    fault: Optional[str]

    def to_record(self):
        """Return one compact decision-log row; accepted input is not success."""
        return dict(
            to_jsonable(self),
            schema_version=1,
            kind="arbiter_decision",
            input_accepted=any(item.status == "sent" for item in self.decisions),
            game_success_inferred=False,
        )


@dataclass(frozen=True)
class _Pending:
    intent: Intent
    sent_ns: int
    before: WorldState


class Arbiter:
    """Serialized arbitration with bounded history and observation-based pending state.

    Pass proposals to step(), or register producer objects by their Intent
    producer name. Optional hooks are can_send(intent, world), note_sent(id, ns),
    note_rejected(id, reason, ns), and confirm(intent, before, after). The last
    returns an Observation[bool], never an unproven boolean. Every rejected or
    accepted result is also available in results, and via optional on_result.
    Deferred proposals are not queued; producers recompute on the next sample.
    """

    def __init__(
        self,
        actuator,
        *,
        clock,
        producers=None,
        policy=None,
        enabled=False,
        world_provider=None,
        pointer_validator=None,
        prayer_validator=None,
        on_result=None,
        log_stream=None,
    ):
        if (
            not callable(clock)
            or not callable(getattr(actuator, "send", None))
            or not callable(getattr(actuator, "close", None))
        ):
            raise TypeError("An actuator and a native-domain clock are required")
        if type(enabled) is not bool:
            raise TypeError("enabled must be explicit boolean")
        self.actuator, self.clock, self.policy = actuator, clock, policy or ArbiterPolicy()
        self.producers = MappingProxyType(dict(producers or {}))
        self.world_provider, self.pointer_validator = world_provider, pointer_validator
        self.prayer_validator = prayer_validator
        self.on_result, self.log_stream, self._enabled = on_result, log_stream, enabled
        self._lock, self._stepping, self._closed = RLock(), False, False
        self._identity, self._time, self._sample, self._tick = None, None, None, None
        self._last_clock = None
        self._counts, self._history, self._pending = {}, {}, {}
        self._fault = None
        self.results, self.last_proposals, self.last_log = (), (), None

    def _now(self):
        value = self.clock()
        if not _integer(value):
            raise ValueError("invalid_native_clock")
        if self._last_clock is not None and value < self._last_clock:
            raise ValueError("native_clock_regressed")
        self._last_clock = value
        return value

    def _fresh(self, observation, world, now):
        return (
            bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(self.policy.maximum_age_ms)
            and observation.age_ms + (now - world.now_ns) / NS_PER_MS <= self.policy.maximum_age_ms
        )

    def _stop(self, reason):
        self._fault = self._fault or reason
        if not self._closed:
            self._closed = True
            try:
                self.actuator.close()
            except Exception:
                self._fault = "actuator_close_failed_input_state_unknown"

    def _backend_fault_reason(self):
        """A1 exposes uncertainty separately from ordinary rejected SendResults."""
        fault = getattr(self.actuator, "fault", None)
        if fault is None:
            return None
        if not isinstance(fault, str) or not fault:
            return "actuator_fault_state_invalid"
        return "actuator_fault:" + fault

    def close(self):
        """Stop dispatch and release backend-owned keys, idempotently."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self.actuator.close()

    def _frame_reason(self, world, now):
        if not isinstance(world, WorldState):
            return "world_unavailable"
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or not _integer(world.observed_ns)
            or not _integer(world.now_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0 <= now - world.observed_ns <= self.policy.maximum_age_ms * NS_PER_MS
            or world.now_ns > now
        ):
            return "snapshot_unavailable_or_stale"
        digest = world.raw.get("executable_sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return "executable_hash_unavailable"
        return None

    def _tick_index(self, world, now):
        tick = world.game_tick
        if (
            not self._fresh(tick, world, now)
            or not isinstance(tick.value, Mapping)
            or not _integer(tick.value.get("index"))
        ):
            return None
        return tick.value["index"]

    def _notify(self, intent, result, results):
        results.append((intent, result))
        producer = self.producers.get(intent.producer)
        callback = getattr(producer, "note_sent" if result.accepted else "note_rejected", None)
        if callback:
            if result.accepted:
                callback(intent.intent_id, result.sent_ns)
            else:
                callback(intent.intent_id, result.reason, result.sent_ns)
        if self.on_result:
            self.on_result(intent, result)

    def _observation(self, pending, world, now):
        intent, before, action = pending.intent, pending.before, pending.intent.action
        if world.sample_sequence <= intent.sample_sequence or world.observed_ns <= pending.sent_ns:
            return None
        if isinstance(action, SetPrayer):
            if (
                self._fresh(world.player.active_prayer, world, now)
                and world.player.active_prayer.value == action.prayer
            ):
                return "requested_prayer_observed"
        elif isinstance(action, ActivateAbility):
            if self._fresh(world.abilities, world, now) and any(
                entry.get("struct_id") == action.ability_id
                and _number((entry.get("cooldown") or {}).get("remaining_ms"))
                and entry["cooldown"]["remaining_ms"] > 0
                and entry["cooldown"].get("reason") is None
                for entry in world.abilities.value
            ):
                return "ability_cooldown_observed"
        elif isinstance(action, ConsumeSlot):
            if self._fresh(world.inventory, world, now):
                previous = next(
                    (item for item in before.inventory.value if item.get("slot") == action.slot),
                    None,
                )
                matches = [
                    item for item in world.inventory.value if item.get("slot") == action.slot
                ]
                if previous is not None and (
                    (not matches and world.inventory.complete is True)
                    or len(matches) == 1
                    and any(
                        matches[0].get(key) != previous.get(key)
                        for key in ("item_id", "quantity", "tracking_token")
                    )
                ):
                    return "inventory_precondition_changed_not_proof_of_consumption"
        elif isinstance(action, MoveTo):
            if (
                self._fresh(world.player.tile, world, now)
                and world.player.tile.value == action.tile
            ):
                return "destination_tile_observed"
        callback = getattr(self.producers.get(intent.producer), "confirm", None)
        if callback:
            observation = callback(intent, before, world)
            if not isinstance(observation, Observation):
                raise TypeError("Confirmation requires an Observation")
            if self._fresh(observation, world, now) and observation.value is True:
                return "producer_confirmation_observed"
        return None

    def _reconcile(self, world, now):
        observations = []
        for key, pending in tuple(self._pending.items()):
            reason = self._observation(pending, world, now)
            if reason:
                del self._pending[key]
                observations.append(
                    MappingProxyType({"intent_id": pending.intent.intent_id, "reason": reason})
                )
            elif now - pending.sent_ns > self.policy.confirmation_timeout_ms * NS_PER_MS:
                self._stop("confirmation_timeout")
        return tuple(observations)

    @staticmethod
    def _action_key(intent):
        if isinstance(intent.action, ActivateAbility):
            return "ability", intent.action.ability_id
        return (intent.lane.value,)

    def _precondition(self, intent, world, now):
        action = intent.action
        if isinstance(action, ActivateAbility):
            if not self._fresh(world.abilities, world, now):
                return "abilities_unknown"
            matches = [
                entry
                for entry in world.abilities.value
                if entry.get("struct_id") == action.ability_id
            ]
            if len(matches) != 1 or matches[0].get("castable") is not True:
                return "ability_castability_unknown_or_blocked"
            timer = matches[0].get("cooldown") or {}
            if (
                timer.get("reason") is not None
                or not _number(timer.get("remaining_ms"))
                or timer["remaining_ms"] != 0
            ):
                return "ability_timer_unknown_or_busy"
            if intent.lane == ActionLane.GCD:
                gcd = world.player.gcd
                if (
                    not self._fresh(gcd, world, now)
                    or not isinstance(gcd.value, Mapping)
                    or gcd.value.get("reason") is not None
                    or not _number(gcd.value.get("remaining_ms"))
                    or gcd.value["remaining_ms"] != 0
                ):
                    return "gcd_unknown_or_busy"
        elif isinstance(action, SetPrayer):
            if self.prayer_validator is None or self.prayer_validator(intent, world) is not True:
                return "prayer_timing_validation_unavailable"
            if action.prayer == "none":
                return "prayer_none_unmapped"
            active, points = world.player.active_prayer, world.player.prayer.current
            if (
                not self._fresh(active, world, now)
                or active.value
                not in ("none", "melee", "magic", "ranged", "soul_split", "necromancy")
                or not self._fresh(points, world, now)
                or not _number(points.value)
                or points.value <= 0
            ):
                return "prayer_state_unknown_or_unavailable"
            if active.value == action.prayer:
                return "already_active"
        elif isinstance(action, ConsumeSlot):
            if not self._fresh(world.inventory, world, now):
                return "inventory_unknown"
            matches = [item for item in world.inventory.value if item.get("slot") == action.slot]
            if (
                len(matches) != 1
                or matches[0].get("item_id") != action.expected_item_id
                or not _integer(matches[0].get("quantity"), 1)
                or action.expected_tracking_token is not None
                and matches[0].get("tracking_token") != action.expected_tracking_token
            ):
                return "inventory_precondition_changed"
        elif isinstance(action, (MoveTo, Interact)):
            if self.pointer_validator is None or self.pointer_validator(intent, world) is not True:
                return "pointer_validation_unavailable"
        callback = getattr(self.producers.get(intent.producer), "can_send", None)
        if callback and callback(intent, world) is not True:
            return "producer_precondition_changed"
        return None

    def _intent_reason(self, intent, world, now):
        if intent.session != world.session:
            return "intent_session_mismatch"
        if intent.issued_ns > now:
            return "before_issue_time"
        if intent.expired(now):
            return "deadline_expired"
        if (
            intent.observed_ns is None
            or not 0 <= now - intent.observed_ns <= self.policy.maximum_age_ms * NS_PER_MS
            or not _integer(intent.sample_sequence, 1)
            or intent.sample_sequence > world.sample_sequence
        ):
            return "intent_snapshot_unavailable_or_stale"
        elapsed = (now - intent.issued_ns) / NS_PER_MS
        if any(
            not evidence.observation.source
            or evidence.observation.reason is not None
            or not evidence.observation.is_fresh(self.policy.maximum_age_ms)
            or evidence.observation.age_ms + elapsed > self.policy.maximum_age_ms
            for evidence in intent.evidence
        ):
            return "evidence_unavailable_or_stale"
        return self._precondition(intent, world, now)

    def _decision(self, intent, status, reason, sent_ns=None):
        return Decision(
            intent.intent_id,
            intent.producer,
            intent.lane.value,
            int(intent.priority),
            status,
            reason,
            sent_ns,
        )

    def _dispatch(self, world, intents, decisions, results):
        for intent in sorted(intents, key=lambda item: (-int(item.priority), item.deadline_ns)):
            now = self._now()
            current = self.world_provider() if self.world_provider else world
            reason = self._frame_reason(current, now)
            if reason and self._pending:
                self._stop("snapshot_lost_during_dispatch_with_pending_input")
            if (
                reason is None
                and (current.session, current.raw.get("executable_sha256")) != self._identity
            ):
                self._stop("session_or_build_changed_during_dispatch")
            if reason is None and current.sample_sequence < self._sample:
                self._stop("snapshot_regressed_during_dispatch")
            if self._closed or self._fault:
                decisions.append(self._decision(intent, "rejected", self._fault or "closed"))
                self._notify(
                    intent,
                    SendResult(intent.intent_id, False, now, self._fault or "closed"),
                    results,
                )
                continue
            reason = reason or self._intent_reason(intent, current, now)
            if reason:
                decisions.append(self._decision(intent, "rejected", reason))
                self._notify(intent, SendResult(intent.intent_id, False, now, reason), results)
                continue
            tick = self._tick_index(current, now)
            if tick is None:
                decisions.append(self._decision(intent, "deferred", "server_tick_unknown"))
                continue
            if self._tick is not None and tick < self._tick:
                self._stop("server_tick_regressed")
                decisions.append(self._decision(intent, "rejected", self._fault))
                self._notify(intent, SendResult(intent.intent_id, False, now, self._fault), results)
                continue
            if tick != self._tick:
                self._tick, self._counts = tick, {}
            self._history = {
                key: deadline for key, deadline in self._history.items() if deadline >= now
            }
            action_key = self._action_key(intent)
            busy = (
                action_key in self._pending
                or intent.lane in EXCLUSIVE_LANES
                and any(pending.intent.lane == intent.lane for pending in self._pending.values())
            )
            if busy:
                decisions.append(self._decision(intent, "deferred", "awaiting_confirmation"))
                continue
            if intent.intent_id in self._history:
                decisions.append(self._decision(intent, "rejected", "duplicate_intent"))
                self._notify(
                    intent, SendResult(intent.intent_id, False, now, "duplicate_intent"), results
                )
                continue
            if (
                sum(self._counts.values()) >= self.policy.maximum_inputs_per_tick
                or self._counts.get(intent.lane, 0) >= self.policy.lane_limits[intent.lane]
            ):
                decisions.append(self._decision(intent, "deferred", "tick_budget_exhausted"))
                continue
            if (
                len(self._history) >= self.policy.maximum_history
                or len(self._pending) >= self.policy.maximum_pending
            ):
                decisions.append(
                    self._decision(intent, "deferred", "bounded_history_or_pending_full")
                )
                continue
            self._history[intent.intent_id] = intent.deadline_ns
            self._counts[intent.lane] = self._counts.get(intent.lane, 0) + 1
            try:
                result = self.actuator.send(intent)
            except Exception:
                self._stop("actuator_send_failed_input_state_unknown")
                decisions.append(self._decision(intent, "fault", self._fault))
                self._notify(intent, SendResult(intent.intent_id, False, now, self._fault), results)
                continue
            if (
                not isinstance(result, SendResult)
                or result.intent_id != intent.intent_id
                or type(result.accepted) is not bool
                or not _integer(result.sent_ns)
                or result.sent_ns < intent.issued_ns
            ):
                self._stop("invalid_actuator_result")
                decisions.append(
                    self._decision(intent, "fault", "invalid_actuator_result_input_state_unknown")
                )
                self._notify(
                    intent,
                    SendResult(
                        intent.intent_id, False, now, "invalid_actuator_result_input_state_unknown"
                    ),
                    results,
                )
                continue
            backend_fault = self._backend_fault_reason()
            if backend_fault:
                self._stop(backend_fault)
            status = "sent" if result.accepted else "rejected"
            if backend_fault and not result.accepted:
                status = "fault"
            decisions.append(
                self._decision(
                    intent, status, self._fault if backend_fault else result.reason, result.sent_ns
                )
            )
            if result.accepted:
                self._pending[action_key] = _Pending(intent, result.sent_ns, current)
                if result.sent_ns > intent.deadline_ns:
                    self._stop("actuator_accepted_after_deadline")
            completed_ns = self._now()
            if not now <= result.sent_ns <= completed_ns:
                self._stop("actuator_timestamp_outside_dispatch_window")
            self._notify(intent, result, results)

    def step(self, world, proposals=None):
        """Arbitrate one frame and return its decision log; serialized across callers."""
        if not isinstance(world, WorldState):
            raise TypeError("Arbiter requires WorldState")
        with self._lock:
            if self._stepping:
                raise RuntimeError("reentrant_arbiter_step")
            self._stepping = True
            decisions, results, observations, now = [], [], (), world.now_ns
            self.last_proposals = ()
            try:
                now = self._now()
                backend_fault = self._backend_fault_reason()
                if backend_fault:
                    self._stop(backend_fault)
                frame_reason = self._frame_reason(world, now)
                if self._identity is not None and world.session != self._identity[0]:
                    self._stop("session_changed")
                if frame_reason is None:
                    identity = (world.session, world.raw.get("executable_sha256"))
                    if self._identity is None:
                        self._identity = identity
                    elif identity != self._identity:
                        self._stop("executable_changed")
                    if self._time is not None and (
                        now < self._time or world.sample_sequence < self._sample
                    ):
                        self._stop("clock_or_sample_regressed")
                    self._time, self._sample = now, world.sample_sequence
                    if not self._fault:
                        observations = self._reconcile(world, now)
                elif self._pending:
                    self._stop("snapshot_lost_with_pending_input")
                if proposals is None:

                    def collect():
                        for name, producer in self.producers.items():
                            for intent in producer.step(world):
                                if not isinstance(intent, Intent) or intent.producer != name:
                                    raise ValueError("producer_identity_mismatch")
                                yield intent

                    proposals = collect()
                intents = []
                for intent in proposals:
                    if len(intents) >= self.policy.maximum_proposals or not isinstance(
                        intent, Intent
                    ):
                        raise ValueError("invalid_or_excessive_proposals")
                    intents.append(intent)
                self.last_proposals = tuple(intents)
                if not self._enabled or self._closed or self._fault or frame_reason:
                    reason = self._fault or (
                        "closed" if self._closed else frame_reason or "disabled"
                    )
                    for intent in intents:
                        decisions.append(self._decision(intent, "deferred", reason))
                else:
                    self._dispatch(world, intents, decisions, results)
            except Exception as error:
                self._stop("arbiter_exception:" + type(error).__name__)
                handled = {decision.intent_id for decision in decisions}
                for intent in self.last_proposals:
                    if intent.intent_id not in handled:
                        decisions.append(self._decision(intent, "rejected", self._fault))
            finally:
                self._stepping = False
            self.results = tuple(results)
            record = DecisionLog(
                world.session,
                world.sample_sequence,
                world.observed_ns,
                now,
                self._tick,
                tuple(decisions),
                observations,
                tuple(pending.intent.intent_id for pending in self._pending.values()),
                self._fault,
            )
            self.last_log = record
            if self.log_stream is not None:
                try:
                    self.log_stream.write(
                        json.dumps(
                            record.to_record(),
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        + "\n"
                    )
                except Exception:
                    self._stop("decision_log_write_failed")
                    record = DecisionLog(
                        record.session,
                        record.sample_sequence,
                        record.observed_ns,
                        record.now_ns,
                        record.game_tick,
                        record.decisions,
                        record.observations,
                        record.pending_ids,
                        self._fault,
                    )
                    self.last_log = record
            return record


class ReplayArbiter:
    """S0a adapter that can ONLY use RecordingActuator, never hardware input."""

    def __init__(self, producers=None):
        self.world = None
        self.actuator = RecordingActuator(clock=lambda: self.world.now_ns)
        self.arbiter = Arbiter(
            self.actuator, clock=lambda: self.world.now_ns, producers=producers, enabled=True
        )

    def step(self, world):
        """Log arbitration separately; S0a's output continues to describe proposals."""
        self.world = world
        self.arbiter.step(world)
        return self.arbiter.last_proposals


def create_stack():
    """Construct a recording-only replay arbiter with no live producers attached."""
    return ReplayArbiter()
