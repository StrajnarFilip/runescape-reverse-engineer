"""A2 movement proposals, observed minimap calibration and outcome confirmation.

No DLL, mouse, keyboard, image or process access occurs here. Only the arbiter
sends intents. Native UI/view geometry, path checks and interaction hit tests
are explicit injected dependencies; missing dependencies authorize nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Optional

from .intents import ActionLane, ActivateAbility, Evidence, Intent, Interact, MoveTo, Priority
from .world_state import EntityRef, Observation, SessionIdentity, Tile, WorldState

NS_PER_MS = 1_000_000
MAX_TILE = 16384
MAX_CALIBRATION_SAMPLES = 256
MIN_CALIBRATION_SAMPLES = 6
MIN_RELATIVE_AREA = 1e-6


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _tile(value):
    return isinstance(value, Tile) and value.x < MAX_TILE and value.y < MAX_TILE


def _round_pixel(value):
    """Match C round(), including negative-monitor half-pixels."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


@dataclass(frozen=True)
class MinimapRegion:
    """Native physical-pixel circle and a complete camera/zoom/window signature.

    ``validated`` is supplied by the native UI acquisition owner, never inferred
    from a fit. The signature must change on camera, minimap zoom, DPI, window or
    UI-layout changes. No current P5 field yet supplies this complete record.
    """

    session: SessionIdentity
    observed_ns: int
    view_signature: str
    center_x: int
    center_y: int
    radius: int
    source: str
    validated: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.session, SessionIdentity)
            or not self.session.complete
            or type(self.observed_ns) is not int
            or self.observed_ns < 0
            or not isinstance(self.view_signature, str)
            or not self.view_signature.strip()
            or not isinstance(self.source, str)
            or not self.source.strip()
            or type(self.validated) is not bool
            or any(type(v) is not int for v in (self.center_x, self.center_y, self.radius))
            or self.radius <= 0
        ):
            raise ValueError("Minimap region requires explicit native geometry/provenance")


@dataclass(frozen=True)
class MinimapSample:
    """A labelled click and later native arrival; a click alone is not a sample."""

    session: SessionIdentity
    view_signature: str
    origin: Tile
    arrived: Tile
    screen_x: float
    screen_y: float
    source: str
    confirmed: bool = False


def _solve_three(matrix, vector):
    """Solve one bounded 3x3 normal system with scaled pivot checks."""
    augmented = [list(row) + [value] for row, value in zip(matrix, vector)]
    scale = max(abs(value) for row in matrix for value in row)
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if not scale or abs(augmented[pivot][column]) <= scale * MIN_RELATIVE_AREA:
            raise ValueError("Calibration displacements do not span two independent axes")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(3):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [
                    value - factor * other
                    for value, other in zip(augmented[row], augmented[column])
                ]
    return tuple(row[-1] for row in augmented)


@dataclass(frozen=True)
class MinimapCalibration:
    """Fitted physical-pixel axes, scoped to the measured UI/view and process."""

    region: MinimapRegion
    plane: int
    east_dx: float
    east_dy: float
    north_dx: float
    north_dy: float
    maximum_error_pixels: float
    sample_count: int

    def __post_init__(self):
        if (
            not isinstance(self.region, MinimapRegion)
            or self.region.validated is not True
            or type(self.plane) is not int
            or not 0 <= self.plane <= 3
            or any(
                not _finite(v)
                for v in (
                    self.east_dx,
                    self.east_dy,
                    self.north_dx,
                    self.north_dy,
                    self.maximum_error_pixels,
                )
            )
            or self.maximum_error_pixels < 0
            or type(self.sample_count) is not int
            or not MIN_CALIBRATION_SAMPLES <= self.sample_count <= MAX_CALIBRATION_SAMPLES
        ):
            raise ValueError("Calibration requires a measured finite native-view transform")
        norm = math.hypot(self.east_dx, self.east_dy) * math.hypot(self.north_dx, self.north_dy)
        determinant = self.east_dx * self.north_dy - self.east_dy * self.north_dx
        if not _finite(norm) or not norm or abs(determinant) / norm <= MIN_RELATIVE_AREA:
            raise ValueError("Minimap axes are degenerate")
        longest_axis = max(
            math.hypot(self.east_dx, self.east_dy), math.hypot(self.north_dx, self.north_dy)
        )
        half_cell_margin = abs(determinant) / (2 * longest_axis)
        if self.maximum_error_pixels + math.sqrt(0.5) >= half_cell_margin:
            raise ValueError("Fit error and pixel rounding cannot reliably select one tile")

    @classmethod
    def fit(cls, samples, region, *, tolerance_pixels=2.0):
        """Fit confirmed arrivals; reserve the last two samples for validation.

        The safe radius is supplied by native UI evidence, not estimated from
        successful clicks. Nothing measures pixels in an image or asks for
        hand-measured scale/rotation. Unlabelled, collinear or changed-view
        observations fail instead of producing a guessed transform.
        """
        samples = tuple(samples)
        if (
            not isinstance(region, MinimapRegion)
            or region.validated is not True
            or not _finite(tolerance_pixels)
            or tolerance_pixels <= 0
            or not MIN_CALIBRATION_SAMPLES <= len(samples) <= MAX_CALIBRATION_SAMPLES
        ):
            raise ValueError("Calibration needs native bounds and six confirmed samples")
        coordinates = []
        for sample in samples:
            if (
                not isinstance(sample, MinimapSample)
                or sample.confirmed is not True
                or not isinstance(sample.source, str)
                or not sample.source.strip()
                or sample.session != region.session
                or sample.view_signature != region.view_signature
                or not _tile(sample.origin)
                or not _tile(sample.arrived)
                or sample.origin.plane != sample.arrived.plane
                or sample.origin.plane != samples[0].origin.plane
                or not _finite(sample.screen_x)
                or not _finite(sample.screen_y)
                or math.hypot(sample.screen_x - region.center_x, sample.screen_y - region.center_y)
                > region.radius
            ):
                raise ValueError("Unconfirmed, foreign or inconsistent calibration sample")
            coordinates.append(
                (1.0, sample.arrived.x - sample.origin.x, sample.arrived.y - sample.origin.y)
            )
        training = coordinates[:-2]
        normal = [[sum(row[i] * row[j] for row in training) for j in range(3)] for i in range(3)]
        axes = []
        for name in ("screen_x", "screen_y"):
            vector = [
                sum(row[i] * getattr(sample, name) for row, sample in zip(training, samples[:-2]))
                for i in range(3)
            ]
            axes.append(_solve_three(normal, vector))
        horizontal, vertical = axes
        determinant = horizontal[1] * vertical[2] - vertical[1] * horizontal[2]
        norm = math.hypot(horizontal[1], vertical[1]) * math.hypot(horizontal[2], vertical[2])
        if (
            not all(_finite(v) for axis in axes for v in axis)
            or not norm
            or (abs(determinant) / norm <= MIN_RELATIVE_AREA)
        ):
            raise ValueError("Minimap axes are degenerate")
        error = max(
            math.hypot(
                sum(value * weight for value, weight in zip(row, horizontal)) - sample.screen_x,
                sum(value * weight for value, weight in zip(row, vertical)) - sample.screen_y,
            )
            for row, sample in zip(coordinates, samples)
        )
        center_error = math.hypot(horizontal[0] - region.center_x, vertical[0] - region.center_y)
        if error > tolerance_pixels or center_error > tolerance_pixels:
            raise ValueError("Minimap fit disagrees with held-out arrivals or native center")
        return cls(
            region,
            samples[0].origin.plane,
            horizontal[1],
            vertical[1],
            horizontal[2],
            vertical[2],
            max(error, center_error),
            len(samples),
        )

    def project(self, origin, destination, current_region):
        """Return rounded physical pixels only in the observed safe circle."""
        if (
            not _tile(origin)
            or not _tile(destination)
            or origin.plane != self.plane
            or destination.plane != self.plane
            or not isinstance(current_region, MinimapRegion)
            or current_region.validated is not True
            or (
                current_region.session,
                current_region.view_signature,
                current_region.center_x,
                current_region.center_y,
                current_region.radius,
            )
            != (
                self.region.session,
                self.region.view_signature,
                self.region.center_x,
                self.region.center_y,
                self.region.radius,
            )
        ):
            return None
        east, north = destination.x - origin.x, destination.y - origin.y
        offset_x = east * self.east_dx + north * self.north_dx
        offset_y = east * self.east_dy + north * self.north_dy
        point = (
            _round_pixel(current_region.center_x + offset_x),
            _round_pixel(current_region.center_y + offset_y),
        )
        if (
            math.hypot(point[0] - current_region.center_x, point[1] - current_region.center_y)
            + (self.maximum_error_pixels + 1)
            > current_region.radius
        ):
            return None
        return point


@dataclass(frozen=True)
class MovementPolicy:
    maximum_age_ms: int = 150
    proposal_lifetime_ms: int = 300
    confirmation_timeout_ms: int = 3000
    maximum_pending: int = 64

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("Movement policy bounds must be positive integers")


@dataclass(frozen=True)
class MovementOutcome:
    intent_id: str
    status: str
    reason: str


@dataclass
class _Expectation:
    intent: Intent
    before: object
    destination: Optional[Tile] = None
    sent_ns: Optional[int] = None


class MovementPrimitives:
    """Produce proposals and confirm newer observations; never send input.

    Injected path/hit-test/ability validators must return literal True from
    current, mapped evidence. Missing validators disable that operation. The
    minimap region provider must read its native view signature each sample.
    Outcome time is bounded by confirmation_timeout_ms plus caller poll delay;
    this is a failure timeout, not a measured guarantee of successful movement.
    """

    def __init__(
        self,
        *,
        policy=None,
        calibration=None,
        region_provider=None,
        path_validator=None,
        interaction_validator=None,
        ability_validator=None,
        object_provider=None,
        object_confirmation=None,
    ):
        self.policy = policy or MovementPolicy()
        self.calibration, self.region_provider = calibration, region_provider
        self.path_validator, self.interaction_validator = path_validator, interaction_validator
        self.ability_validator = ability_validator
        self.object_provider, self.object_confirmation = object_provider, object_confirmation
        self._expectations = {}
        self._sequence = 0
        self.fault = None
        self.last_reason = None

    def _fresh(self, observation):
        return (
            bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(self.policy.maximum_age_ms)
        )

    def _world(self, world):
        if not isinstance(world, WorldState):
            return False
        native = world.raw.get("native")
        age_ms = native.get("snapshot_age_ms") if isinstance(native, Mapping) else None
        return (
            not self.fault
            and _finite(age_ms)
            and 0 <= age_ms <= self.policy.maximum_age_ms
            and world.session.complete
            and world.status in ("available", "partial")
            and type(world.observed_ns) is int
            and type(world.now_ns) is int
            and type(world.sample_sequence) is int
            and world.sample_sequence > 0
            and 0 <= world.now_ns - world.observed_ns <= self.policy.maximum_age_ms * NS_PER_MS
            and isinstance(world.raw.get("executable_sha256"), str)
            and len(world.raw["executable_sha256"]) == 64
            and all(c in "0123456789abcdef" for c in world.raw["executable_sha256"])
        )

    def _reject(self, reason):
        self.last_reason = reason
        return None

    def _npc(self, world, reference):
        if (
            not self._fresh(world.npcs)
            or type(reference.instance_id) is not int
            or reference.instance_id <= 0
            or type(reference.runtime_id) is not int
            or not 0 <= reference.runtime_id <= 65535
            or type(reference.config_id) is not int
        ):
            return None
        matches = [
            npc
            for npc in world.npcs.value
            if self._fresh(npc.instance_id)
            and npc.instance_id.value == reference.instance_id
            and self._fresh(npc.runtime_id)
            and npc.runtime_id.value == reference.runtime_id
            and self._fresh(npc.config_id)
            and npc.config_id.value == reference.config_id
            and self._fresh(npc.tile)
        ]
        return matches[0] if len(matches) == 1 else None

    def _move_valid(self, world, destination):
        if (
            not _tile(destination)
            or not self._fresh(world.player.tile)
            or not self.path_validator
            or self.path_validator(world, destination) is not True
            or not isinstance(self.calibration, MinimapCalibration)
            or not self.region_provider
        ):
            return False
        region = self.region_provider(world)
        return (
            isinstance(region, MinimapRegion)
            and region.session == world.session
            and 0 <= world.now_ns - region.observed_ns <= self.policy.maximum_age_ms * NS_PER_MS
            and self.calibration.project(world.player.tile.value, destination, region) is not None
        )

    def _interaction_valid(self, world, action):
        if not self.interaction_validator or self.interaction_validator(action, world) is not True:
            return False
        if action.target.kind == "npc":
            return self._npc(world, action.target) is not None and self._fresh(world.player.target)
        return (
            action.target.kind == "object"
            and self.object_confirmation is not None
            and self.object_provider is not None
            and self.object_provider(world, action.target) == action.target
        )

    def pointer_validator(self, intent, world):
        """D6 pre-dispatch callback; re-evaluate current position/view/identity."""
        if (
            not self._world(world)
            or intent.session != world.session
            or intent.issued_ns > world.now_ns
            or intent.expired(world.now_ns)
            or not isinstance(intent.action, (MoveTo, Interact))
        ):
            return False
        if isinstance(intent.action, MoveTo):
            return (
                self._move_valid(world, intent.action.tile)
                and world.player.tile.value != intent.action.tile
            )
        return self._interaction_valid(world, intent.action) and (
            intent.action.target.kind != "npc" or world.player.target.value != intent.action.target
        )

    def _offer(self, world, action, evidence, destination=None, lane=ActionLane.POINTER):
        if len(self._expectations) >= self.policy.maximum_pending:
            return self._reject("movement_pending_capacity")
        self._sequence += 1
        intent = Intent(
            f"A2:{world.now_ns}:{self._sequence}",
            "A2",
            action,
            Priority.MECHANICS,
            lane,
            world.now_ns,
            world.now_ns + self.policy.proposal_lifetime_ms * NS_PER_MS,
            world.session,
            world.sample_sequence,
            tuple(evidence),
            observed_ns=world.observed_ns,
        )
        self._expectations[intent.intent_id] = _Expectation(intent, world, destination)
        self.last_reason = None
        return intent

    def move_to(self, world, tile):
        """Propose a validated same-plane route; already-arrived is not a new move."""
        if not self._world(world) or not self._move_valid(world, tile):
            return self._reject("movement_path_or_minimap_unavailable")
        if world.player.tile.value == tile:
            return self._reject("already_at_destination")
        region = self.region_provider(world)
        return self._offer(
            world,
            MoveTo(tile),
            [
                Evidence("player.tile", world.player.tile),
                Evidence(
                    "A2.native_minimap_view",
                    Observation(
                        {
                            "signature": region.view_signature,
                            "fit_samples": self.calibration.sample_count,
                        },
                        max(0, world.now_ns - region.observed_ns) / NS_PER_MS,
                        region.source,
                    ),
                ),
                Evidence("A2.route_validation", Observation(True, 0, "explicit_path_validator")),
            ],
            tile,
        )

    def click_entity(self, world, instance, *, option="default"):
        """Propose an NPC click only with a mapped target and current hit-test validation."""
        if (
            not self._world(world)
            or not self._fresh(world.npcs)
            or type(instance) is not int
            or instance <= 0
        ):
            return self._reject("entity_observation_unavailable")
        matches = [npc for npc in world.npcs.value if npc.instance_id.value == instance]
        if len(matches) != 1:
            return self._reject("entity_identity_ambiguous_or_missing")
        npc = matches[0]
        if not all(
            self._fresh(value) and type(value.value) is int and value.value >= 0
            for value in (npc.runtime_id, npc.config_id)
        ):
            return self._reject("entity_identity_unmapped")
        reference = EntityRef("npc", instance, npc.runtime_id.value, npc.config_id.value)
        action = Interact(reference, option)
        if not self._interaction_valid(world, action):
            return self._reject("entity_target_or_hit_test_unavailable")
        if world.player.target.value == reference:
            return self._reject("already_targeted")
        return self._offer(
            world,
            action,
            [Evidence("npc.tile", npc.tile), Evidence("player.target", world.player.target)],
        )

    def select_target(self, world, instance):
        """Select an NPC; only a later native target identity confirms success."""
        return self.click_entity(world, instance, option="select_target")

    def click_object(self, world, tile, *, option="default"):
        """Require current object identity and explicit interaction acknowledgement.

        object_provider(world, Tile) resolves a location; calling it with an
        EntityRef must revalidate that identity against the current native scene.
        """
        if not self._world(world) or not _tile(tile) or not self.object_provider:
            return self._reject("object_source_unmapped")
        reference = self.object_provider(world, tile)
        if (
            not isinstance(reference, EntityRef)
            or reference.kind != "object"
            or type(reference.instance_id) is not int
            or reference.instance_id <= 0
            or type(reference.config_id) is not int
        ):
            return self._reject("object_identity_unmapped")
        action = Interact(reference, option)
        if not self._interaction_valid(world, action):
            return self._reject("object_hit_test_or_confirmation_unavailable")
        return self._offer(
            world,
            action,
            [
                Evidence(
                    "object.identity", Observation(reference, 0, "explicit_native_object_provider")
                )
            ],
        )

    def _ability_valid(self, world, ability_id, destination):
        if (
            not self._world(world)
            or not _tile(destination)
            or not self._fresh(world.player.tile)
            or destination.plane != world.player.tile.value.plane
            or destination == world.player.tile.value
            or not self._fresh(world.player.facing)
            or not self._fresh(world.abilities)
            or not self.path_validator
            or self.path_validator(world, destination) is not True
            or not self.ability_validator
            or self.ability_validator(world, ability_id, destination) is not True
        ):
            return False
        matches = [entry for entry in world.abilities.value if entry.get("struct_id") == ability_id]
        cooldown = matches[0].get("cooldown") if len(matches) == 1 else None
        return (
            len(matches) == 1
            and matches[0].get("castable") is True
            and isinstance(cooldown, Mapping)
            and cooldown.get("reason") is None
            and type(cooldown.get("remaining_ms")) is int
            and cooldown["remaining_ms"] == 0
        )

    def use_movement_ability(self, world, ability_id, destination, *, lane=None):
        """Propose a calibrated movement ability with an explicitly mapped GCD lane."""
        if lane not in (ActionLane.GCD, ActionLane.OFF_GCD) or not self._ability_valid(
            world, ability_id, destination
        ):
            return self._reject("movement_ability_geometry_or_castability_unavailable")
        return self._offer(
            world,
            ActivateAbility(ability_id),
            [
                Evidence("abilities", world.abilities),
                Evidence("player.facing", world.player.facing),
            ],
            destination,
            lane,
        )

    def can_send(self, intent, world):
        """D6 recheck for this producer's cached proposal, without dispatching."""
        expectation = self._expectations.get(intent.intent_id)
        if (
            expectation is None
            or expectation.intent != intent
            or expectation.sent_ns is not None
            or not self._world(world)
            or world.session != intent.session
            or world.raw.get("executable_sha256") != expectation.before.raw.get("executable_sha256")
            or intent.expired(world.now_ns)
        ):
            return False
        if isinstance(intent.action, ActivateAbility):
            return self._ability_valid(world, intent.action.ability_id, expectation.destination)
        return self.pointer_validator(intent, world)

    def step(self, world):
        """D6 producer callback: observe outcomes, then yield current unsent proposals."""
        self.observe(world)
        if self.fault:
            raise RuntimeError(self.fault)
        return tuple(
            item.intent
            for item in self._expectations.values()
            if item.sent_ns is None and self.can_send(item.intent, world)
        )

    def note_sent(self, intent_id, sent_ns):
        """D6 acceptance callback; sending is not confirmation and never causes a retry."""
        expectation = self._expectations.get(intent_id)
        if (
            expectation is None
            or expectation.sent_ns is not None
            or type(sent_ns) is not int
            or not expectation.intent.issued_ns <= sent_ns <= expectation.intent.deadline_ns
        ):
            raise ValueError("Unregistered, duplicate or expired movement send")
        expectation.sent_ns = sent_ns

    def note_rejected(self, intent_id, reason, rejected_ns):
        """D6 rejection callback; drop only an unsent proposal."""
        expectation = self._expectations.get(intent_id)
        if expectation is not None and expectation.sent_ns is None:
            del self._expectations[intent_id]

    def confirm(self, intent, before, after):
        """D6 confirmation callback; require newer native observations after actual acceptance."""
        expectation = self._expectations.get(intent.intent_id)
        if (
            expectation is None
            or expectation.intent != intent
            or expectation.sent_ns is None
            or not self._world(after)
            or after.session != intent.session
            or after.raw.get("executable_sha256") != before.raw.get("executable_sha256")
            or after.sample_sequence <= before.sample_sequence
            or after.observed_ns <= expectation.sent_ns
            or not 0
            <= after.now_ns - expectation.sent_ns
            <= self.policy.confirmation_timeout_ms * NS_PER_MS
        ):
            return Observation(None, None, "A2_confirmation", "awaiting_new_native_observation")
        confirmed = False
        if expectation.destination is not None:
            confirmed = (
                self._fresh(after.player.tile)
                and after.player.tile.value == expectation.destination
            )
        elif isinstance(intent.action, Interact) and intent.action.target.kind == "npc":
            confirmed = (
                self._fresh(after.player.target)
                and after.player.target.value == intent.action.target
                and before.player.target.value != intent.action.target
                and self._npc(after, intent.action.target) is not None
            )
        elif self.object_confirmation:
            confirmed = self.object_confirmation(intent, before, after) is True
        return Observation(
            confirmed, (after.now_ns - after.observed_ns) / NS_PER_MS, "A2_native_outcome"
        )

    def observe(self, world):
        """Report confirmations/failures. Timeout/session loss latches a fault; no blind retry."""
        outcomes = []
        for intent_id, expectation in list(self._expectations.items()):
            if expectation.sent_ns is None:
                if world.now_ns is not None and expectation.intent.expired(world.now_ns):
                    del self._expectations[intent_id]
                continue
            if (
                not self._world(world)
                or world.session != expectation.intent.session
                or (
                    world.raw.get("executable_sha256")
                    != expectation.before.raw.get("executable_sha256")
                )
            ):
                self.fault = "movement_observation_or_session_lost"
            elif world.now_ns < expectation.sent_ns:
                self.fault = "movement_clock_regressed"
            elif (
                world.now_ns - expectation.sent_ns > self.policy.confirmation_timeout_ms * NS_PER_MS
            ):
                self.fault = "movement_confirmation_timeout"
            elif self.confirm(expectation.intent, expectation.before, world).value is True:
                outcomes.append(MovementOutcome(intent_id, "confirmed", "new_native_observation"))
                del self._expectations[intent_id]
                continue
            else:
                continue
            outcomes.append(MovementOutcome(intent_id, "failed", self.fault))
            del self._expectations[intent_id]
        return tuple(outcomes)
