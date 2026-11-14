"""D7 observable trip steps; no acquisition, actuator, sleeps or guessed UI input.

The shipped profile is explicitly unconfigured. Fixture routes can exercise
bank/preset/travel/setup/fight/loot/recovery without a game. Every accepted
action waits for a newer postcondition; a failed step stops rather than retries.
P5 interface attachment alone is not proof a control is visible/clickable.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .intents import ActionLane, Evidence, Intent, Interact, MoveTo, Priority
from .world_state import EntityRef, Observation, Tile, WorldState, freeze

ROOT = Path(__file__).resolve().parents[3]
PROFILE_PATH = ROOT / "profiles/trip.json"
NS_PER_MS = 1_000_000
BACKPACK_SLOTS = 28
MAXIMUM_ENRAGE = 4000


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_profile(path=PROFILE_PATH):
    """Load policy, leaving unknown route/preset/loot fields explicitly unknown."""
    return validate_profile(json.loads(Path(path).read_text()))


def validate_profile(document):
    """Reject unsupported bounds and configured routes without explicit provenance."""
    if document.get("schema_version") != 1 or type(document.get("configured")) is not bool:
        raise ValueError("Expected version-1 trip policy")
    for key in (
        "maximum_age_ms",
        "action_timeout_ms",
        "fight_timeout_ms",
        "maximum_seconds",
        "maximum_kills",
    ):
        if not _integer(document.get(key), 1):
            raise ValueError("Invalid positive trip bound: " + key)
    if (
        not _integer(document.get("maximum_streak"))
        or not all(
            _integer(document.get(key))
            for key in ("minimum_enrage", "maximum_enrage", "target_enrage")
        )
        or not 0
        <= document["minimum_enrage"]
        <= document["target_enrage"]
        <= document["maximum_enrage"]
        <= MAXIMUM_ENRAGE
    ):
        raise ValueError("Invalid enrage/streak policy")
    for key in ("minimum_health_fraction", "minimum_prayer_fraction"):
        if not _number(document.get(key)) or not 0 < document[key] <= 1:
            raise ValueError("Invalid readiness threshold")
    if document["configured"]:
        if (
            not document.get("source")
            or type(document.get("practice")) is not bool
            or not _integer(document.get("preset_id"), 1)
        ):
            raise ValueError("Configured trip needs explicit setup")
        if not document.get("expected_inventory") or not document.get("expected_equipment"):
            raise ValueError("Preset signatures are required")
        for node in (document["bank"], document["entrance"]):
            if (
                node.get("validated") is not True
                or not node.get("source")
                or not node.get("target")
                or not node.get("option")
            ):
                raise ValueError("Unvalidated trip target")
            EntityRef(**node["target"])
        Tile(**document["bank"]["tile"])
        for key in ("route", "return_route"):
            if not document[key] or len(document[key]) > 256:
                raise ValueError("A bounded calibrated route is required")
            for node in document[key]:
                if (
                    node.get("validated") is not True
                    or not node.get("source")
                    or node.get("kind") not in ("walk", "interact")
                ):
                    raise ValueError("Unknown route node")
                Tile(**node["tile"])
                if node["kind"] == "interact":
                    EntityRef(**node["target"])
        for key in ("expected_inventory", "expected_equipment"):
            slots = set()
            for item in document[key]:
                if (
                    not _integer(item.get("slot"))
                    or item["slot"] in slots
                    or not _integer(item.get("item_id"))
                    or not _integer(item.get("minimum_quantity"), 1)
                ):
                    raise ValueError("Invalid preset signature")
                if key == "expected_inventory" and item["slot"] >= BACKPACK_SLOTS:
                    raise ValueError("Backpack signature slot out of bounds")
                slots.add(item["slot"])
        loot = document["loot_policy"]
        if (
            loot.get("validated") is not True
            or not loot.get("source")
            or loot.get("choice") not in ("claim", "continue_if_ready")
        ):
            raise ValueError("Loot policy is unconfigured")
        death = document["death_policy"]
        if (
            death.get("action") not in ("stop", "recover")
            or death.get("validated") is not True
            or not death.get("source")
        ):
            raise ValueError("Death policy is unconfigured")
    return freeze(document)


@dataclass(frozen=True)
class TripReport:
    state: str
    reasons: Tuple[str, ...]
    kills: int
    stopped: bool
    fault: Optional[str]


@dataclass(frozen=True)
class _Offer:
    intent: Intent
    expected: str
    value: object
    next_state: str


class TripLoop:
    """Single-step state machine. Only D6/A2 may execute its pointer proposals."""

    def __init__(
        self,
        profile=None,
        *,
        path_validator=None,
        interaction_validator=None,
        wall_clock=time.monotonic,
    ):
        self.profile = load_profile() if profile is None else validate_profile(profile)
        self.path_validator, self.interaction_validator = path_validator, interaction_validator
        self.wall_clock, self.started = wall_clock, wall_clock()
        self.state, self.kills, self.route_index = "BANK", 0, 0
        self._identity, self._pending, self._fault, self._stopped = None, None, None, False
        self._offers, self._since, self._result_id = {}, None, None
        self._seen_results = set()
        self._last_frame = None
        self.last_report = TripReport(self.state, ("unconfigured",), 0, False, None)

    def _fresh(self, observation):
        return (
            bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(self.profile["maximum_age_ms"])
        )

    def _observation(self, world, key):
        value = world.raw.get(key)
        if not isinstance(value, Mapping):
            return Observation(reason="unmapped")
        age = value.get("age_ms")
        captured_age = world.raw.get("native", {}).get("snapshot_age_ms")
        captured_age = captured_age if _number(captured_age) else 0
        if _number(age):
            age += max(0, (world.now_ns - world.observed_ns) / NS_PER_MS - captured_age)
        result = Observation(value.get("value"), age, value.get("source"), value.get("reason"))
        return result if self._fresh(result) else Observation(reason="unavailable_or_stale")

    def _ui_flag(self, world, kind):
        if not self._ui_fresh(world):
            return None
        raw = world.raw.get("ui", {})
        entries = list(world.ui.open.value or ()) + list(raw.get("required", ()))
        matches = [
            entry
            for entry in entries
            if entry.get("kind") == kind
            and type(entry.get("open")) is bool
            and entry.get("reason") is None
            and entry.get("source")
        ]
        values = {entry["open"] for entry in matches}
        if len(values) != 1:
            return None
        return next(iter(values))

    def _ui_fresh(self, world):
        observation = world.ui.open
        return (
            bool(observation.source)
            and observation.is_fresh(self.profile["maximum_age_ms"])
            and observation.reason in (None, "other_interfaces_visibility_and_options_unmapped")
        )

    def _control(self, world, kind, role, value=None):
        if not self._ui_fresh(world):
            return None
        panels = [
            entry
            for entry in world.ui.open.value
            if entry.get("kind") == kind
            and entry.get("open") is True
            and entry.get("visible") is True
            and entry.get("reason") is None
            and entry.get("source")
        ]
        controls = [
            control
            for panel in panels
            for control in panel.get("controls", ())
            if control.get("role") == role
            and control.get("validated") is True
            and control.get("visible") is True
            and control.get("source")
            and control.get("option")
            and (value is None or control.get("value") == value)
        ]
        if len(controls) != 1:
            return None
        try:
            target = EntityRef(**controls[0]["target"])
        except (KeyError, TypeError, ValueError):
            return None
        if target.kind != "interface" or target.instance_id is None:
            return None
        return Interact(target, controls[0]["option"])

    def _supplies(self, world):
        if (
            not self._fresh(world.inventory)
            or world.inventory.complete is not True
            or not self._fresh(world.equipment)
        ):
            return False
        actual = {item.get("slot"): item for item in world.inventory.value}
        if (
            len(world.inventory.value) != BACKPACK_SLOTS
            or len(actual) != BACKPACK_SLOTS
            or set(actual) != set(range(BACKPACK_SLOTS))
        ):
            return False
        expected = {item["slot"]: item for item in self.profile["expected_inventory"]}
        for slot, item in actual.items():
            if slot in expected:
                desired = expected[slot]
                if (
                    item.get("empty") is not False
                    or item.get("item_id") != desired["item_id"]
                    or not _integer(item.get("quantity"), desired["minimum_quantity"])
                ):
                    return False
            elif not self.profile["allow_extra_inventory_items"] and item.get("empty") is not True:
                return False
        equipment = world.equipment.value.get("slots")
        if not isinstance(equipment, (tuple, list)):
            return False
        for expected_item in self.profile["expected_equipment"]:
            matches = [item for item in equipment if item.get("slot") == expected_item["slot"]]
            if (
                len(matches) != 1
                or matches[0].get("item_id") != expected_item["item_id"]
                or not _integer(matches[0].get("quantity"), expected_item["minimum_quantity"])
            ):
                return False
        for resource, minimum in (
            (world.player.health, self.profile["minimum_health_fraction"]),
            (world.player.prayer, self.profile["minimum_prayer_fraction"]),
        ):
            if (
                not self._fresh(resource.current)
                or not self._fresh(resource.maximum)
                or not _number(resource.current.value)
                or not _number(resource.maximum.value)
                or resource.maximum.value <= 0
                or not minimum <= resource.current.value / resource.maximum.value <= 1
            ):
                return False
        return True

    def _setup(self, world):
        observed = self._observation(world, "instance_setup")
        return (
            observed.value
            if observed.known
            and isinstance(observed.value, Mapping)
            and observed.value.get("validated") is True
            else None
        )

    def _fight(self, world, enrage):
        return (
            self._fresh(world.telos.phase)
            and type(world.telos.phase.value) is int
            and world.telos.phase.value == 1
            and self._fresh(world.telos.enrage)
            and _number(world.telos.enrage.value)
            and world.telos.enrage.value == enrage
        )

    def _flag_for_result(self, world, key, result_id):
        observed = self._observation(world, key)
        return (
            observed.known
            and isinstance(observed.value, Mapping)
            and observed.value.get("validated") is True
            and observed.value.get("result_id") == result_id
            and observed.value.get("complete") is True
        )

    def _confirmed(self, offer, sent_ns, world):
        if world.sample_sequence <= offer.intent.sample_sequence or world.observed_ns <= sent_ns:
            return False
        if offer.expected == "at_tile":
            return self._fresh(world.player.tile) and world.player.tile.value == offer.value
        if offer.expected == "bank_open":
            return self._ui_flag(world, "bank_preset") is True
        if offer.expected == "bank_closed":
            return self._ui_flag(world, "bank_preset") is False
        if offer.expected == "supplies":
            return self._supplies(world)
        if offer.expected == "instance_open":
            return (
                self._ui_flag(world, "instance") is True or self._ui_flag(world, "enrage") is True
            )
        if offer.expected in ("enrage", "practice"):
            setup = self._setup(world)
            return setup is not None and setup.get(offer.expected) == offer.value
        if offer.expected == "fight":
            return self._fight(world, offer.value)
        if offer.expected in ("loot_claimed", "recovered"):
            return self._flag_for_result(world, offer.expected, offer.value)
        return False

    def _move_or_interact(self, world, node):
        return (
            MoveTo(Tile(**node["tile"]))
            if node.get("kind", "walk") == "walk"
            else Interact(EntityRef(**node["target"]), node["option"])
        )

    def _plan(self, world):
        p = self.profile
        state = self.state
        if state == "BANK":
            if self._ui_flag(world, "bank_preset") is True:
                return None, "PRESET"
            tile = Tile(**p["bank"]["tile"])
            if not self._fresh(world.player.tile):
                return None, None
            if world.player.tile.value != tile:
                return (MoveTo(tile), "at_tile", tile, "BANK"), None
            return (
                Interact(EntityRef(**p["bank"]["target"]), p["bank"]["option"]),
                "bank_open",
                True,
                "PRESET",
            ), None
        if state == "PRESET":
            if self._supplies(world):
                return None, "CLOSE_BANK"
            action = self._control(world, "bank_preset", "load_preset", p["preset_id"])
            return ((action, "supplies", True, "CLOSE_BANK"), None) if action else (None, None)
        if state == "CLOSE_BANK":
            if self._ui_flag(world, "bank_preset") is False:
                return None, "TRAVEL"
            action = self._control(world, "bank_preset", "close")
            return ((action, "bank_closed", False, "TRAVEL"), None) if action else (None, None)
        if state in ("TRAVEL", "RETURN"):
            nodes = p["route"] if state == "TRAVEL" else p["return_route"]
            if self.route_index >= len(nodes):
                return None, "INSTANCE" if state == "TRAVEL" else "BANK"
            node = nodes[self.route_index]
            tile = Tile(**node["tile"])
            if self._fresh(world.player.tile) and world.player.tile.value == tile:
                self.route_index += 1
                return None, state
            return (self._move_or_interact(world, node), "at_tile", tile, state), None
        if state == "INSTANCE":
            if self._ui_flag(world, "enrage") is True or self._ui_flag(world, "instance") is True:
                return None, "ENRAGE"
            return (
                Interact(EntityRef(**p["entrance"]["target"]), p["entrance"]["option"]),
                "instance_open",
                True,
                "ENRAGE",
            ), None
        if state == "ENRAGE":
            setup = self._setup(world)
            if setup is None:
                return None, None
            key = "practice" if setup.get("practice") != p["practice"] else "enrage"
            target = p["practice"] if key == "practice" else p["target_enrage"]
            if setup.get(key) == target:
                return None, "ENTER"
            action = self._control(
                world, "instance" if key == "practice" else "enrage", "set_" + key, target
            )
            return ((action, key, target, "ENRAGE"), None) if action else (None, None)
        if state == "ENTER":
            if not self._supplies(world):
                self._stop("preset_changed_before_enter")
                return None, None
            setup = self._setup(world)
            if (
                setup is None
                or setup.get("enrage") != p["target_enrage"]
                or setup.get("practice") is not p["practice"]
            ):
                return None, None
            action = self._control(world, "instance", "enter") or self._control(
                world, "enrage", "enter"
            )
            return (
                ((action, "fight", p["target_enrage"], "FIGHT"), None) if action else (None, None)
            )
        if state == "LOOT":
            policy = p["loot_policy"]
            next_value = self._observation(world, "next_instance_enrage")
            data = (
                next_value.value
                if next_value.known and isinstance(next_value.value, Mapping)
                else {}
            )
            continuing = (
                policy["choice"] == "continue_if_ready"
                and self.kills < p["maximum_kills"]
                and self._fresh(world.telos.streak)
                and _integer(world.telos.streak.value)
                and world.telos.streak.value < p["maximum_streak"]
                and self._supplies(world)
                and data.get("validated") is True
                and data.get("result_id") == self._result_id
                and _integer(data.get("enrage"))
                and p["minimum_enrage"] <= data["enrage"] <= p["maximum_enrage"]
            )
            action = self._control(world, "loot", "continue" if continuing else "claim")
            if not action:
                return None, None
            return (
                (action, "fight", data["enrage"], "FIGHT")
                if continuing
                else (action, "loot_claimed", self._result_id, "AFTER_CLAIM")
            ), None
        if state == "AFTER_CLAIM":
            if self.kills >= p["maximum_kills"]:
                self._stop("kill_limit", fault=False)
                return None, None
            self.route_index = 0
            return None, "RETURN"
        if state == "DEATH":
            policy = p["death_policy"]
            if policy["action"] == "stop":
                self._stop("player_death")
                return None, None
            if policy.get("validated") is not True or not policy.get("source"):
                return None, None
            action = self._control(world, "death", "recover")
            return (
                ((action, "recovered", self._result_id, "BANK"), None) if action else (None, None)
            )
        return None, None

    def _validated_action(self, world, action):
        validator = (
            self.path_validator if isinstance(action, MoveTo) else self.interaction_validator
        )
        if validator is None:
            return None
        result = validator(world, action)
        return (
            result
            if isinstance(result, Observation) and self._fresh(result) and result.value is True
            else None
        )

    def _stop(self, reason, fault=True):
        self._stopped = True
        self._fault = reason if fault else None
        self.state = "STOPPED"
        self.last_report = TripReport(self.state, (reason,), self.kills, True, self._fault)

    def _advance(self, state, now_ns):
        if state != self.state and state in ("BANK", "TRAVEL", "RETURN"):
            self.route_index = 0
        self.state, self._since = state, now_ns

    def step(self, world):
        """Advance only on observed postconditions and never click an unknown control."""
        if not isinstance(world, WorldState):
            raise TypeError("TripLoop requires WorldState")
        self._offers = {}
        if self._stopped:
            return ()
        if not self.profile["configured"]:
            self.last_report = TripReport(
                self.state,
                ("route_preset_mode_and_loot_policy_unconfigured",),
                self.kills,
                False,
                None,
            )
            return ()
        if self.wall_clock() - self.started >= self.profile["maximum_seconds"]:
            self._stop("wall_time_limit", fault=False)
            return ()
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or not _integer(world.now_ns)
            or not _integer(world.observed_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0
            <= world.now_ns - world.observed_ns
            <= self.profile["maximum_age_ms"] * NS_PER_MS
        ):
            self._stop("snapshot_unavailable_or_stale")
            return ()
        if self._last_frame is not None and (
            world.now_ns < self._last_frame[0] or world.sample_sequence < self._last_frame[1]
        ):
            self._stop("clock_or_sample_regressed")
            return ()
        self._last_frame = (world.now_ns, world.sample_sequence)
        identity = (world.session, world.raw.get("executable_sha256"))
        if self._identity is None:
            self._identity = identity
        elif identity != self._identity:
            expected = (
                self._pending
                and self._pending[0].expected in ("at_tile", "fight")
                and self._confirmed(self._pending[0], self._pending[1], world)
            )
            if not expected or (
                world.session.pid,
                world.session.process_creation_time,
                identity[1],
            ) != (
                self._identity[0].pid,
                self._identity[0].process_creation_time,
                self._identity[1],
            ):
                self._stop("unexpected_session_or_build_change")
                return ()
            self._identity = identity
        if self._since is None:
            self._since = world.now_ns
        if (
            self._fresh(world.telos.enrage)
            and _number(world.telos.enrage.value)
            and not self.profile["minimum_enrage"]
            <= world.telos.enrage.value
            <= self.profile["maximum_enrage"]
        ):
            self._stop("enrage_limit")
            return ()
        if (
            self.state not in ("FIGHT", "LOOT", "AFTER_CLAIM")
            and self._fresh(world.telos.streak)
            and _integer(world.telos.streak.value)
            and world.telos.streak.value > self.profile["maximum_streak"]
        ):
            self._stop("streak_limit")
            return ()
        if self._pending:
            offer, sent_ns = self._pending
            if self._confirmed(offer, sent_ns, world):
                self._advance(offer.next_state, world.now_ns)
                self._pending = None
                self._since = world.now_ns
            elif world.now_ns - sent_ns > self.profile["action_timeout_ms"] * NS_PER_MS:
                self._stop("postcondition_timeout:" + offer.expected)
            self.last_report = TripReport(
                self.state,
                ("awaiting_observed_postcondition",),
                self.kills,
                self._stopped,
                self._fault,
            )
            return ()
        if self.state == "FIGHT":
            result = self._observation(world, "encounter_result")
            data = result.value if result.known and isinstance(result.value, Mapping) else {}
            if (
                data.get("validated") is True
                and data.get("kind") in ("kill", "death")
                and data.get("id")
                and data["id"] not in self._seen_results
            ):
                self._seen_results.add(data["id"])
                self._result_id = data["id"]
                self.state = "LOOT" if data["kind"] == "kill" else "DEATH"
                self.kills += int(data["kind"] == "kill")
                self._since = world.now_ns
            elif world.now_ns - self._since > self.profile["fight_timeout_ms"] * NS_PER_MS:
                self._stop("fight_result_timeout")
            self.last_report = TripReport(
                self.state, ("combat_result_required",), self.kills, self._stopped, self._fault
            )
            return ()
        if world.now_ns - self._since > self.profile["action_timeout_ms"] * NS_PER_MS:
            self._stop("state_data_timeout:" + self.state)
            return ()
        planned, next_state = self._plan(world)
        if self._stopped:
            return ()
        if next_state is not None:
            if next_state != self.state:
                self._advance(next_state, world.now_ns)
            self.last_report = TripReport(self.state, (), self.kills, False, None)
            return ()
        if planned:
            action, expected, value, next_state = planned
            verdict = self._validated_action(world, action)
            if verdict is not None:
                evidence = (
                    Evidence(
                        "trip.state",
                        Observation(
                            {"state": self.state, "preset_id": self.profile["preset_id"]},
                            0,
                            self.profile["source"],
                        ),
                    ),
                    Evidence("trip.action_validation", verdict),
                )
                intent = Intent(
                    "d7:{}:{}:{}:{}:{}".format(
                        world.session.pid,
                        world.session.process_creation_time,
                        world.session.scene_epoch,
                        world.sample_sequence,
                        self.state,
                    ),
                    "trip",
                    action,
                    Priority.MECHANICS,
                    ActionLane.POINTER,
                    world.now_ns,
                    world.now_ns + self.profile["maximum_age_ms"] * NS_PER_MS,
                    world.session,
                    world.sample_sequence,
                    evidence,
                    world.observed_ns,
                )
                self._offers[intent.intent_id] = _Offer(intent, expected, value, next_state)
                self.last_report = TripReport(self.state, (), self.kills, False, None)
                return (intent,)
        self.last_report = TripReport(
            self.state,
            ("mapped_control_route_or_postcondition_unavailable",),
            self.kills,
            False,
            None,
        )
        return ()

    def can_send(self, intent, world):
        """D6/A2 revalidate the current step and control before dispatch."""
        offer = self._offers.get(intent.intent_id)
        if offer is None or self._pending or self._stopped or intent.session != world.session:
            return False
        if (
            not _integer(world.now_ns)
            or not _integer(world.observed_ns)
            or not 0
            <= world.now_ns - world.observed_ns
            <= self.profile["maximum_age_ms"] * NS_PER_MS
        ):
            return False
        planned, _ = self._plan(world)
        return (
            planned is not None
            and planned[0] == intent.action
            and self._validated_action(world, intent.action) is not None
        )

    def note_sent(self, intent_id, sent_ns):
        """Accept a D6 notification, then wait for the declared postcondition."""
        offer = self._offers.get(intent_id)
        if (
            offer is None
            or self._pending
            or not _integer(sent_ns)
            or not offer.intent.issued_ns <= sent_ns <= offer.intent.deadline_ns
        ):
            raise ValueError("Unknown or expired accepted trip intent")
        self._pending = (offer, sent_ns)

    def note_rejected(self, intent_id, reason, sent_ns):
        """A rejected send does not move the trip to its next state."""
        self.last_report = TripReport(
            self.state, ("send_rejected:" + str(reason),), self.kills, self._stopped, self._fault
        )

    def confirm(self, intent, before, world):
        """Expose a fresh observed postcondition to D6 without claiming causality."""
        if (
            self._pending
            and self._pending[0].intent.intent_id == intent.intent_id
            and self._confirmed(self._pending[0], self._pending[1], world)
        ):
            return Observation(True, 0, "validated_trip_postcondition")
        return Observation(reason="not_confirmed")


def create_stack():
    """The default unconfigured profile emits no route/preset/loot guesses."""
    return TripLoop()
