"""D5 proposal-only phase handlers; P4/A2 absence blocks all real movement.

Default phase rules are explicitly unvalidated guide leads. Inject calibrated
rules, fresh P4 geometry/hazards, and A2 path/interaction validators to exercise
the engine. Tests use synthetic contracts; no recorded hazard mapping or live
path execution is claimed. Relative coordinates are the only arena geometry.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from ..intents import ActionLane, Evidence, Intent, Interact, MoveTo, Priority
from ..world_state import EntityRef, Observation, WorldState
from . import p1, p2, p3, p4, p5
from .geometry import ArenaGeometry, Rule

NS_PER_MS = 1_000_000
DEFAULT_RULES = p1.RULES + p2.RULES + p3.RULES + p4.RULES + p5.RULES


@dataclass(frozen=True)
class MechanicsReport:
    phase: Optional[int]
    reasons: Tuple[str, ...]
    fault: Optional[str] = None


class MechanicsEngine:
    """One pointer proposal per frame, confirmed from later native observations."""

    def __init__(
        self,
        rules=DEFAULT_RULES,
        *,
        path_validator=None,
        interaction_validator=None,
        maximum_age_ms=150,
        intent_lifetime_ms=100,
        confirmation_timeout_ms=1800,
    ):
        if any(
            type(value) is not int or value <= 0
            for value in (maximum_age_ms, intent_lifetime_ms, confirmation_timeout_ms)
        ):
            raise ValueError("Mechanics limits must be positive integers")
        self.rules = tuple(rules)
        for rule in self.rules:
            if (
                not isinstance(rule, Rule)
                or type(rule.phase) is not int
                or rule.phase not in range(1, 6)
                or rule.mode not in ("avoid", "enter", "target", "interact")
                or type(rule.validated) is not bool
                or not rule.source
            ):
                raise ValueError("Invalid mechanic rule")
            if rule.validated and (
                not isinstance(rule.client_sha256, str)
                or len(rule.client_sha256) != 64
                or any(character not in "0123456789abcdef" for character in rule.client_sha256)
                or not isinstance(rule.enrage_range, tuple)
                or len(rule.enrage_range) != 2
                or any(type(value) is not int for value in rule.enrage_range)
                or not 0 <= rule.enrage_range[0] <= rule.enrage_range[1] <= 4000
            ):
                raise ValueError("Calibrated strategy requires exact client and enrage scope")
        self.path_validator, self.interaction_validator = path_validator, interaction_validator
        self.maximum_age_ms, self.intent_lifetime_ms = maximum_age_ms, intent_lifetime_ms
        self.confirmation_timeout_ms = confirmation_timeout_ms
        self._identity, self._offers, self._pending, self._fault = None, {}, None, None
        self._last_frame = None
        self.last_report = MechanicsReport(None, ("P4_A2_and_strategy_calibration_required",))

    def _fresh(self, observation):
        return (
            bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(self.maximum_age_ms)
        )

    def _context(self, world):
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or type(world.now_ns) is not int
            or type(world.observed_ns) is not int
            or type(world.sample_sequence) is not int
            or not 0 <= world.now_ns - world.observed_ns <= self.maximum_age_ms * NS_PER_MS
            or not self._fresh(world.telos.phase)
            or type(world.telos.phase.value) is not int
        ):
            return None
        if (
            not self._fresh(world.hazards)
            or world.hazards.complete is not True
            or not self._fresh(world.player.tile)
        ):
            return None
        geometry = ArenaGeometry.from_world(world, self.maximum_age_ms)
        if geometry is None or geometry.to_local(world.player.tile.value) is None:
            return None
        for entry in world.hazards.value:
            if (
                entry.get("validated") is not True
                or type(entry.get("active")) is not bool
                or type(entry.get("dangerous")) is not bool
                or not entry.get("source")
                or not entry.get("id")
                or not entry.get("kind")
            ):
                return None
            if entry["kind"] not in {rule.hazard for rule in self.rules}:
                return None
            if any(not geometry.contains(tile) for tile in entry.get("tiles", ())):
                return None
        return geometry

    def _select(self, world, geometry):
        phase = world.telos.phase.value
        current = geometry.to_local(world.player.tile.value)
        hazards = world.hazards.value
        dangerous = {
            tuple(tile)
            for entry in hazards
            if entry["active"] and entry["dangerous"]
            for tile in entry.get("tiles", ())
        }
        for rule in self.rules:
            if rule.phase != phase or not rule.validated:
                continue
            if (
                world.raw.get("executable_sha256") != rule.client_sha256
                or not self._fresh(world.telos.enrage)
                or type(world.telos.enrage.value) not in (int, float)
                or not rule.enrage_range[0] <= world.telos.enrage.value <= rule.enrage_range[1]
            ):
                continue
            matches = [
                entry
                for entry in hazards
                if entry["kind"] == rule.hazard and entry["active"] and entry.get("phase") == phase
            ]
            for hazard in matches:
                evidence = [
                    Evidence("telos.phase", world.telos.phase),
                    Evidence("hazard:" + str(hazard["id"]), world.hazards),
                    Evidence(
                        "arena",
                        Observation(
                            {
                                "anchor": {
                                    "x": geometry.anchor.x,
                                    "y": geometry.anchor.y,
                                    "plane": geometry.anchor.plane,
                                },
                                "quarter_turns": geometry.quarter_turns,
                            },
                            0,
                            rule.source,
                        ),
                    ),
                ]
                if rule.mode in ("avoid", "enter"):
                    tiles = {tuple(tile) for tile in hazard.get("tiles", ())}
                    if (
                        rule.mode == "avoid"
                        and current not in tiles
                        or rule.mode == "enter"
                        and current in tiles
                    ):
                        continue
                    candidates = set(geometry.safe_tiles) - dangerous
                    if rule.mode == "enter":
                        candidates &= tiles
                    for offset in sorted(
                        candidates,
                        key=lambda tile: (
                            max(abs(tile[0] - current[0]), abs(tile[1] - current[1])),
                            tile,
                        ),
                    ):
                        destination = geometry.to_world(offset)
                        if destination == world.player.tile.value or self.path_validator is None:
                            continue
                        verdict = self.path_validator(world, geometry, destination)
                        if (
                            not isinstance(verdict, Observation)
                            or not self._fresh(verdict)
                            or verdict.value is not True
                        ):
                            continue
                        return (
                            MoveTo(destination),
                            rule,
                            hazard,
                            tuple(evidence + [Evidence("validated_path", verdict)]),
                        )
                elif (
                    self.interaction_validator is not None
                    and rule.option
                    and rule.option in hazard.get("options", ())
                ):
                    try:
                        target = EntityRef(**hazard["entity"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if target.kind not in ("npc", "object") or target.instance_id is None:
                        continue
                    if target.kind == "npc":
                        actors = [
                            npc
                            for npc in world.npcs.value or ()
                            if self._fresh(world.npcs)
                            and self._fresh(npc.instance_id)
                            and npc.instance_id.value == target.instance_id
                            and (
                                target.runtime_id is None
                                or self._fresh(npc.runtime_id)
                                and npc.runtime_id.value == target.runtime_id
                            )
                            and self._fresh(npc.health.current)
                            and type(npc.health.current.value) in (int, float)
                            and npc.health.current.value > 0
                        ]
                        if len(actors) != 1:
                            continue
                    verdict = self.interaction_validator(world, target, rule.option)
                    if (
                        isinstance(verdict, Observation)
                        and self._fresh(verdict)
                        and verdict.value is True
                    ):
                        return (
                            Interact(target, rule.option),
                            rule,
                            hazard,
                            tuple(evidence + [Evidence("validated_interaction", verdict)]),
                        )
        return None

    def step(self, world):
        """Never move from an unknown hazard, anchor, strategy or route."""
        if not isinstance(world, WorldState):
            raise TypeError("MechanicsEngine requires WorldState")
        identity = (world.session, world.raw.get("executable_sha256"))
        if self._identity is None:
            self._identity = identity
        elif identity != self._identity:
            self._fault = "session_or_build_changed"
        self._offers = {}
        geometry = self._context(world)
        if geometry is not None:
            if self._last_frame is not None and (
                world.now_ns < self._last_frame[0] or world.sample_sequence < self._last_frame[1]
            ):
                self._fault = "clock_or_sample_regressed"
            self._last_frame = (world.now_ns, world.sample_sequence)
        if self._pending and geometry is not None:
            intent, sent_ns, hazard_id, mode = self._pending
            if self.confirm(intent, None, world).value is True:
                self._pending = None
            elif world.now_ns - sent_ns > self.confirmation_timeout_ms * NS_PER_MS:
                self._fault = "confirmation_timeout"
        if self._fault or geometry is None or self._pending:
            self.last_report = MechanicsReport(
                world.telos.phase.value,
                ("geometry_hazards_or_confirmation_unavailable",),
                self._fault,
            )
            return ()
        selected = self._select(world, geometry)
        if selected is None:
            self.last_report = MechanicsReport(
                world.telos.phase.value, ("no_calibrated_safe_response",)
            )
            return ()
        action, rule, hazard, evidence = selected
        intent = Intent(
            "d5:{}:{}:{}:{}:{}".format(
                world.session.pid,
                world.session.process_creation_time,
                world.session.scene_epoch,
                world.sample_sequence,
                hazard["id"],
            ),
            "mechanics",
            action,
            Priority.MECHANICS,
            ActionLane.POINTER,
            world.now_ns,
            world.now_ns + self.intent_lifetime_ms * NS_PER_MS,
            world.session,
            world.sample_sequence,
            evidence,
            world.observed_ns,
        )
        self._offers[intent.intent_id] = (intent, hazard["id"], rule.mode)
        self.last_report = MechanicsReport(world.telos.phase.value, ())
        return (intent,)

    def can_send(self, intent, world):
        """A2/D6 revalidation checks a fresh same response, not old coordinates."""
        offer = self._offers.get(intent.intent_id)
        geometry = self._context(world)
        if (
            offer is None
            or geometry is None
            or self._fault
            or self._pending
            or intent.session != world.session
        ):
            return False
        selected = self._select(world, geometry)
        return (
            selected is not None and selected[0] == intent.action and selected[2]["id"] == offer[1]
        )

    def note_sent(self, intent_id, sent_ns):
        """D6 reports acceptance; later native state must confirm the response."""
        offer = self._offers.get(intent_id)
        if (
            offer is None
            or self._pending
            or type(sent_ns) is not int
            or not offer[0].issued_ns <= sent_ns <= offer[0].deadline_ns
        ):
            raise ValueError("Unknown or expired accepted mechanic intent")
        self._pending = (offer[0], sent_ns, offer[1], offer[2])

    def note_rejected(self, intent_id, reason, sent_ns):
        """Retain a rejected send as diagnostics without advancing state."""
        self.last_report = MechanicsReport(
            self.last_report.phase, ("send_rejected:" + str(reason),), self._fault
        )

    def confirm(self, intent, before, world):
        """Return positive proof only from a newer position/target/resolution field."""
        if (
            not self._pending
            or self._pending[0].intent_id != intent.intent_id
            or world.session != intent.session
            or world.sample_sequence <= intent.sample_sequence
            or world.observed_ns <= self._pending[1]
        ):
            return Observation(reason="not_confirmed")
        if (
            isinstance(intent.action, MoveTo)
            and self._fresh(world.player.tile)
            and world.player.tile.value == intent.action.tile
        ):
            return Observation(True, world.player.tile.age_ms, world.player.tile.source)
        if (
            isinstance(intent.action, Interact)
            and self._pending[3] == "target"
            and intent.action.target.kind == "npc"
            and self._fresh(world.player.target)
            and world.player.target.value.kind == intent.action.target.kind
            and all(
                getattr(intent.action.target, key) is None
                or getattr(world.player.target.value, key) == getattr(intent.action.target, key)
                for key in ("instance_id", "runtime_id", "config_id")
            )
        ):
            return Observation(True, world.player.target.age_ms, world.player.target.source)
        if self._fresh(world.hazards):
            matching = [
                entry
                for entry in world.hazards.value
                if entry.get("id") == self._pending[2]
                and entry.get("validated") is True
                and entry.get("resolved") is True
            ]
            if len(matching) == 1:
                return Observation(True, world.hazards.age_ms, matching[0]["source"])
        return Observation(reason="not_confirmed")


def create_stack():
    """Default guide leads are disabled; missing P4/A2 never produces a move."""
    return MechanicsEngine()
