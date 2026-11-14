"""D3 survival intent producer; standard library, no acquisition or input.

Policy lives in profiles/survival.json. Catalog families/effect tags identify
consumables; names and remembered slots do not. Missing effects, castability,
timers, threat eligibility or exit bindings remain unknown. With today's
partial perception, health/prayer threshold proposals remain useful in replay.
Only D6 dispatches proposals, and L controls live enablement.

After an accepted send, D6 calls note_sent(). A newer observation, not the
proposal itself, acknowledges input. Pending lanes do not repeat. Timeouts
latch a producer fault until reset; neither proposed nor accepted food is
optimistically removed from the observed inventory.
"""

from __future__ import annotations

import json
import math
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .intents import (
    BACKPACK_SLOT_COUNT,
    ActionLane,
    ActivateAbility,
    ConsumeSlot,
    Evidence,
    Intent,
    Interact,
    Priority,
)
from .world_state import EntityRef, Observation, WorldState, freeze

NS_PER_MS = 1_000_000
PROFILE_PATH = Path(__file__).resolve().parents[3] / "profiles" / "survival.json"
PROTECTION_NAMES = {2: "melee", 3: "magic", 4: "ranged", 6: "necromancy"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def load_profile(path=PROFILE_PATH):
    """Return independently owned immutable policy; reject malformed thresholds."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_profile(document)


def validate_profile(document):
    """Validate configurable bounds and exact metadata selectors, never item names."""
    if document.get("schema_version") != 1:
        raise ValueError("Expected version-1 survival policy")
    for name in ("maximum_age_ms", "intent_lifetime_ms", "acknowledgement_timeout_ms"):
        if not _integer(document.get(name), 1):
            raise ValueError("Policy duration must be a positive integer: " + name)
    thresholds = document["thresholds"]
    for name in (
        "food_health_fraction",
        "brew_health_fraction",
        "critical_health_fraction",
        "restore_prayer_fraction",
        "heavy_hit_health_fraction",
    ):
        if not _number(thresholds.get(name)) or not 0 < thresholds[name] <= 1:
            raise ValueError("Invalid threshold: " + name)
    if (
        not thresholds["critical_health_fraction"]
        <= thresholds["brew_health_fraction"]
        <= thresholds["food_health_fraction"]
    ):
        raise ValueError("Health thresholds must increase from critical to food")
    if not _integer(thresholds.get("defensive_lookahead_ms"), 1):
        raise ValueError("Invalid defensive lookahead")
    roles = set()
    for role in document["item_roles"]:
        if (
            not role.get("source")
            or role.get("role") in roles
            or role.get("operation") not in ("eat", "drink")
        ):
            raise ValueError("Every item role needs a unique identity and provenance")
        roles.add(role["role"])
        if (
            any(not _integer(family) for family in role["family_ids"])
            or type(role.get("undosed")) is not bool
        ):
            raise ValueError("Invalid catalog-family selector")
        if not role["required_effects"] or any(
            effect not in ("heals", "restores_prayer", "restores_stats")
            for effect in role["required_effects"]
        ):
            raise ValueError("Unknown catalog effect tag")
    for name in ("potion_priority", "critical_potion_priority"):
        if len(set(document[name])) != len(document[name]) or any(
            role not in roles for role in document[name]
        ):
            raise ValueError("Potion priorities require unique declared roles")
    for rule in document["defensives"]:
        if rule.get("ability_id") is not None and not _integer(rule["ability_id"]):
            raise ValueError("Invalid defensive ability identity")
        if rule.get("trigger") not in ("stun_or_bind", "predicted") or rule.get("lane") not in (
            "gcd",
            "off_gcd",
        ):
            raise ValueError("Invalid defensive rule")
    exit_rule = document["exit"]
    if (
        type(exit_rule.get("validated")) is not bool
        or exit_rule["validated"]
        and (not _integer(exit_rule.get("ability_id")) or not exit_rule.get("source"))
    ):
        raise ValueError("Emergency exit requires explicit calibrated identity")
    return freeze(document)


@dataclass(frozen=True)
class SurvivalReport:
    reasons: Tuple[str, ...]
    pending_lanes: Tuple[str, ...]
    fault: Optional[str]


class SurvivalManager:
    """Produce highest-priority intents from one immutable frame at a time.

    Effects may be a partial requested set: only an explicitly present confirmed
    true/false entry establishes presence/absence. Defensives require both a
    fresh castable=True result and zero known cooldown. Equipment match alone
    does not establish castability. D1's unresolved threats never time a cast.
    """

    def __init__(self, profile=None, *, encounter_provider=None):
        if encounter_provider is not None and not callable(encounter_provider):
            raise TypeError("encounter_provider must supply current immutable D1 state")
        self.profile = load_profile() if profile is None else validate_profile(profile)
        self.encounter_provider = encounter_provider
        self._session, self._sample, self._time = None, None, None
        self._offers, self._pending, self._fault = {}, {}, None
        self._offer_roles = {}
        self._world, self._encounter = None, None
        self.last_report = SurvivalReport((), (), None)

    def reset(self):
        """Clear acknowledged-input state after the caller has re-observed safely."""
        self._offers, self._pending, self._fault = {}, {}, None
        self._offer_roles = {}
        self._session, self._sample, self._time, self._world = None, None, None, None
        self._encounter = None

    def _fresh(self, observation, *, collection=False):
        return (
            bool(observation.source)
            and observation.is_fresh(self.profile["maximum_age_ms"])
            and (
                observation.reason is None
                or collection
                and observation.reason
                in ("unmapped", "unvalidated_source", "effect_presence_unmapped")
            )
        )

    def _effect(self, world, role):
        effects = world.player.effects
        if self._fresh(effects, collection=True):
            matches = [entry for entry in effects.value if entry.get("id") == role]
            if len(matches) == 1:
                entry = matches[0]
                candidate = entry.get("candidate", {})
                if (
                    entry.get("reason") is None
                    and type(entry.get("active")) is bool
                    and entry.get("source")
                    and candidate.get("presence_validated", True) is True
                ):
                    return Observation(entry["active"], effects.age_ms, entry["source"])
        return Observation(reason="effect_presence_unknown")

    def _ability(self, world, ability_id):
        if not _integer(ability_id) or not self._fresh(world.abilities):
            return None
        matches = [entry for entry in world.abilities.value if entry.get("struct_id") == ability_id]
        if len(matches) != 1:
            return None
        entry = matches[0]
        timer = entry.get("cooldown") or {}
        if (
            entry.get("castable") is not True
            or timer.get("reason") is not None
            or timer.get("remaining_ms") != 0
            or type(timer.get("remaining_ms")) is bool
        ):
            return None
        return entry

    def _items(self, world, role_name):
        if not self._fresh(world.inventory):
            return []
        rule = next(rule for rule in self.profile["item_roles"] if rule["role"] == role_name)
        matches = []
        for item in world.inventory.value:
            if (
                item.get("empty") is not False
                or not _integer(item.get("quantity"), 1)
                or not _integer(item.get("item_id"))
                or not _integer(item.get("tracking_token"), 1)
                or not _integer(item.get("slot"))
                or item["slot"] >= BACKPACK_SLOT_COUNT
            ):
                continue
            if (
                "metadata_reason" not in item
                or item.get("metadata_reason") is not None
                or item.get("identity_ambiguous") is not False
            ):
                continue
            if any(
                (item.get("effects") or {}).get(effect) is not True
                for effect in rule["required_effects"]
            ):
                continue
            if rule["undosed"]:
                if (
                    "doses" not in item
                    or "family_id" not in item
                    or item.get("doses") is not None
                    or item.get("family_id") is not None
                ):
                    continue
            elif item.get("family_id") not in rule["family_ids"] or not _integer(
                item.get("doses"), 1
            ):
                continue
            matches.append(item)
        return sorted(matches, key=lambda item: item["slot"])

    def _resource(self, resource):
        if (
            self._fresh(resource.current)
            and self._fresh(resource.maximum)
            and _number(resource.current.value)
            and _number(resource.maximum.value)
            and resource.maximum.value > 0
            and 0 <= resource.current.value <= resource.maximum.value
        ):
            return resource.current.value / resource.maximum.value
        return None

    def _intent(self, world, action, lane, trigger, evidence, *, deadline=None):
        deadline = (
            min(world.now_ns + self.profile["intent_lifetime_ms"] * NS_PER_MS, deadline)
            if deadline is not None
            else world.now_ns + self.profile["intent_lifetime_ms"] * NS_PER_MS
        )
        intent = Intent(
            "d3:{}:{}:{}:{}:{}:{}".format(
                world.session.pid,
                world.session.process_creation_time,
                world.session.scene_epoch,
                world.sample_sequence,
                lane.value,
                trigger,
            ),
            "survival",
            action,
            Priority.SURVIVAL,
            lane,
            world.now_ns,
            deadline,
            world.session,
            world.sample_sequence,
            tuple(evidence),
            world.observed_ns,
        )
        self._offers[intent.intent_id] = intent
        self._offer_roles[intent.intent_id] = trigger
        return intent

    def can_send(self, intent, world):
        """Recheck current purpose on isolated decision state, without acknowledging input.

        Reuse the proposal rules rather than maintain a second eligibility
        implementation. The preview owns its offer/pending dictionaries; its
        reconciliation cannot alter the live producer's pending inputs, report
        or fault. Observed worlds and policy are immutable. A current encounter
        provider is preferred; otherwise use the state supplied to step().
        """
        offered = self._offers.get(intent.intent_id)
        if (
            offered is None
            or offered != intent
            or self._fault
            or intent.lane in self._pending
            or not isinstance(world, WorldState)
            or world.session != intent.session
            or self._world is None
            or world.raw.get("executable_sha256") != self._world.raw.get("executable_sha256")
            or not _integer(world.now_ns)
            or not _integer(world.sample_sequence, 1)
            or world.sample_sequence < intent.sample_sequence
            or not intent.issued_ns <= world.now_ns <= intent.deadline_ns
        ):
            return False
        if isinstance(intent.action, ConsumeSlot):
            # Preserve the pre-send quantity as well as ID/token. Otherwise a
            # manual stack change before dispatch could later look like input
            # acknowledgement against the older proposal frame.
            slots = []
            for inventory in (self._world.inventory, world.inventory):
                if not self._fresh(inventory):
                    return False
                matches = [
                    entry for entry in inventory.value if entry.get("slot") == intent.action.slot
                ]
                if len(matches) != 1:
                    return False
                slots.append(
                    tuple(matches[0].get(key) for key in ("item_id", "quantity", "tracking_token"))
                )
            if slots[0] != slots[1]:
                return False
        incoming = self.encounter_provider() if self.encounter_provider else self._encounter
        preview = copy(self)
        preview._offers, preview._offer_roles = {}, {}
        preview._pending = dict(self._pending)
        candidates = preview.step(world, incoming)

        def threat_evidence(proposal):
            return tuple(
                evidence.observation.value
                for evidence in proposal.evidence
                if evidence.field == "incoming_threat"
            )

        return any(
            candidate.action == intent.action
            and candidate.lane == intent.lane
            and preview._offer_roles[candidate.intent_id] == self._offer_roles[intent.intent_id]
            and threat_evidence(candidate) == threat_evidence(intent)
            for candidate in candidates
        )

    def note_sent(self, intent_id, sent_ns):
        """D6 calls this ONLY after accepted input; it is not acknowledgement."""
        intent = self._offers.get(intent_id)
        if (
            intent is None
            or not _integer(sent_ns)
            or not intent.issued_ns <= sent_ns <= intent.deadline_ns
            or intent.lane in self._pending
        ):
            raise ValueError("Unknown, expired or already-pending accepted intent")
        before = None
        if isinstance(intent.action, ConsumeSlot):
            before = next(
                (
                    item
                    for item in self._world.inventory.value
                    if item.get("slot") == intent.action.slot
                ),
                None,
            )
        self._pending[intent.lane] = (intent, sent_ns, before)

    def _acknowledge(self, world, reasons):
        for lane, (intent, sent_ns, before) in tuple(self._pending.items()):
            confirmed = False
            newer = world.sample_sequence > intent.sample_sequence and world.observed_ns > sent_ns
            action = intent.action
            if newer and isinstance(action, ConsumeSlot) and self._fresh(world.inventory):
                after = next(
                    (item for item in world.inventory.value if item.get("slot") == action.slot),
                    None,
                )
                if (after is None and world.inventory.complete is True) or (
                    after is not None
                    and any(
                        after.get(key) != before.get(key)
                        for key in ("item_id", "quantity", "tracking_token")
                    )
                ):
                    # A move can also change a slot. Invalidate the old proposal;
                    # do not claim a changed slot proves the food was consumed.
                    confirmed = True
                    reasons.append("inventory_precondition_changed_after_input")
            elif newer and isinstance(action, ActivateAbility) and self._fresh(world.abilities):
                confirmed = any(
                    entry.get("struct_id") == action.ability_id
                    and _number((entry.get("cooldown") or {}).get("remaining_ms"))
                    and entry["cooldown"]["remaining_ms"] > 0
                    and entry["cooldown"].get("reason") is None
                    for entry in world.abilities.value
                )
            elif newer and isinstance(action, Interact):
                confirmed = self._effect(world, "anima_virus").value is False
            if confirmed:
                del self._pending[lane]
            elif world.now_ns - sent_ns > self.profile["acknowledgement_timeout_ms"] * NS_PER_MS:
                self._fault = "acknowledgement_timeout"

    def _defensive(self, world, encounter, reasons):
        stun, bind = self._effect(world, "stun"), self._effect(world, "bind")
        threats = _field(encounter, "pending_threats", ())
        threats = threats if isinstance(threats, (tuple, list)) else ()
        for rule in self.profile["defensives"]:
            lane = ActionLane(rule["lane"])
            if lane in self._pending or self._ability(world, rule["ability_id"]) is None:
                continue
            blockers = [(role, self._effect(world, role)) for role in rule["exclusive_effects"]]
            if any(observation.value is not False for _, observation in blockers):
                continue
            evidence = [Evidence("abilities", world.abilities, "fresh castable and ready timer")]
            evidence.extend(
                Evidence("player.effects." + role, observation, "explicitly observed absent")
                for role, observation in blockers
            )
            deadline = None
            if rule["trigger"] == "stun_or_bind":
                trigger = stun if stun.value is True else bind
                if trigger.value is not True:
                    continue
                evidence.append(Evidence("player.stun_or_bind", trigger))
            else:
                encounter_ns = _field(encounter, "observed_ns")
                if (
                    _field(encounter, "session") != world.session
                    or not _integer(encounter_ns)
                    or not 0
                    <= world.now_ns - encounter_ns
                    <= self.profile["maximum_age_ms"] * NS_PER_MS
                ):
                    continue
                candidates = []
                for threat in threats:
                    earliest, latest = _field(threat, "check_earliest_ns"), _field(
                        threat, "check_latest_ns"
                    )
                    damage = _field(threat, "damage_upper_bound")
                    options = _field(threat, "defensive_options")
                    if (
                        not _integer(_field(threat, "event_id"), 1)
                        or type(_field(threat, "flags")) is not int
                        or _field(threat, "flags") != 1
                        or _field(threat, "classification_validated") is not True
                        or not isinstance(options, (tuple, list))
                        or rule["role"] not in options
                    ):
                        continue
                    if (
                        not _integer(earliest)
                        or not _integer(latest)
                        or not world.now_ns < earliest <= latest
                        or earliest - world.now_ns
                        > self.profile["thresholds"]["defensive_lookahead_ms"] * NS_PER_MS
                    ):
                        continue
                    maximum = world.player.health.maximum
                    if (
                        not self._fresh(maximum)
                        or not _number(maximum.value)
                        or maximum.value <= 0
                        or not _number(damage)
                        or damage
                        < maximum.value * self.profile["thresholds"]["heavy_hit_health_fraction"]
                    ):
                        continue
                    if rule["requires_protection"] and (
                        not self._fresh(world.player.active_prayer)
                        or world.player.active_prayer.value
                        != PROTECTION_NAMES.get(_field(threat, "prayer"))
                    ):
                        continue
                    candidates.append(threat)
                if not candidates:
                    continue
                threat = min(candidates, key=lambda entry: _field(entry, "check_earliest_ns"))
                deadline = _field(threat, "check_earliest_ns") - 1
                if rule["requires_protection"]:
                    evidence.append(Evidence("player.active_prayer", world.player.active_prayer))
                evidence.append(
                    Evidence(
                        "incoming_threat",
                        Observation(
                            {
                                "event_id": _field(threat, "event_id"),
                                "prayer": _field(threat, "prayer"),
                                "damage_upper_bound": _field(threat, "damage_upper_bound"),
                                "check_earliest_ns": deadline + 1,
                                "check_latest_ns": _field(threat, "check_latest_ns"),
                            },
                            0,
                            "validated_encounter_threat",
                        ),
                    )
                )
            return self._intent(
                world,
                ActivateAbility(rule["ability_id"]),
                lane,
                rule["role"],
                evidence,
                deadline=deadline,
            )
        if stun.value is True or bind.value is True:
            reasons.append("stun_response_unavailable_or_unmapped")
        if any(_field(threat, "flags") != 1 for threat in threats):
            reasons.append("defensive_timing_unvalidated")
        return None

    def step(self, world, encounter=None):
        """Return survival Intent proposals; never change game state or dispatch."""
        if not isinstance(world, WorldState):
            raise TypeError("SurvivalManager requires WorldState")
        reasons, intents = [], []
        self._offers = {}
        self._offer_roles = {}
        if world.session != self._session:
            self.reset()
            self._session = world.session
        self._encounter = encounter
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
            self.last_report = SurvivalReport(
                ("snapshot_unavailable_or_stale",),
                tuple(lane.value for lane in self._pending),
                self._fault,
            )
            return ()
        if self._time is not None and (
            world.now_ns < self._time or world.sample_sequence < self._sample
        ):
            self._fault = "clock_or_sample_regressed"
        self._time, self._sample, self._world = world.now_ns, world.sample_sequence, world
        self._acknowledge(world, reasons)
        if self._fault:
            self.last_report = SurvivalReport(
                tuple(reasons), tuple(lane.value for lane in self._pending), self._fault
            )
            return ()
        thresholds = self.profile["thresholds"]
        health, prayer = self._resource(world.player.health), self._resource(world.player.prayer)
        if health is None:
            reasons.append("health_unknown")
        wants = {
            "food": health is not None and health <= thresholds["food_health_fraction"],
            "brew": health is not None and health <= thresholds["brew_health_fraction"],
            "restore": prayer is not None and prayer <= thresholds["restore_prayer_fraction"],
        }
        critical = health is not None and health <= thresholds["critical_health_fraction"]
        heal_offered = False
        roles = ["food"] + list(
            self.profile["critical_potion_priority"]
            if critical
            else self.profile["potion_priority"]
        )
        occupied = set(self._pending)
        for role in roles:
            rule = next(rule for rule in self.profile["item_roles"] if rule["role"] == role)
            lane = ActionLane.FOOD if rule["operation"] == "eat" else ActionLane.POTION
            if not wants.get(role) or lane in occupied:
                continue
            items = self._items(world, role)
            if not items:
                reasons.append(role + "_unavailable_or_unmapped")
                continue
            item = items[0]
            resource = world.player.prayer if role == "restore" else world.player.health
            evidence = (
                Evidence("prayer" if role == "restore" else "health", resource.current),
                Evidence("resource_maximum", resource.maximum),
                Evidence(
                    "inventory",
                    world.inventory,
                    "exact slot, item ID, family and dose preconditions",
                ),
            )
            intents.append(
                self._intent(
                    world,
                    ConsumeSlot(
                        item["slot"], item["item_id"], rule["operation"], item.get("tracking_token")
                    ),
                    lane,
                    role,
                    evidence,
                )
            )
            occupied.add(lane)
            heal_offered = heal_offered or role in ("food", "brew")
        defensive = self._defensive(world, encounter, reasons)
        if defensive:
            intents.append(defensive)
            occupied.add(defensive.lane)
        virus = self._effect(world, "anima_virus")
        if virus.value is True and ActionLane.POINTER not in occupied:
            routes = [
                entry
                for entry in world.hazards.value or ()
                if self._fresh(world.hazards)
                and entry.get("kind") == "virus_cleanse"
                and entry.get("validated") is True
                and entry.get("target")
                and entry.get("option")
                and entry.get("source")
            ]
            if len(routes) == 1:
                route = routes[0]
                try:
                    target = EntityRef(**route["target"])
                except (TypeError, ValueError):
                    target = None
                if (
                    target is not None
                    and target.kind in ("object", "npc", "interface")
                    and (
                        target.instance_id is not None
                        or target.runtime_id is not None
                        or target.kind == "interface"
                        and target.config_id is not None
                    )
                ):
                    intents.append(
                        self._intent(
                            world,
                            Interact(target, route["option"]),
                            ActionLane.POINTER,
                            "virus_cleanse",
                            (
                                Evidence("anima_virus", virus),
                                Evidence("cleanse_target", world.hazards),
                            ),
                        )
                    )
                else:
                    reasons.append("virus_cleanse_target_identity_unknown")
            else:
                reasons.append("virus_cleanse_unmapped_or_ambiguous")
        if critical and not heal_offered and not defensive and not self._pending:
            exit_rule = self.profile["exit"]
            if (
                exit_rule["validated"]
                and ActionLane.GCD not in occupied
                and self._ability(world, exit_rule["ability_id"]) is not None
            ):
                intents.append(
                    self._intent(
                        world,
                        ActivateAbility(exit_rule["ability_id"]),
                        ActionLane.GCD,
                        "emergency_exit",
                        (
                            Evidence("critical_health", world.player.health.current),
                            Evidence("exit_ready", world.abilities),
                        ),
                    )
                )
            else:
                reasons.append("emergency_exit_unmapped_or_unavailable")
        self.last_report = SurvivalReport(
            tuple(reasons), tuple(lane.value for lane in self._pending), self._fault
        )
        return tuple(intents)


def create_stack():
    """Create the threshold/effect-only S0a replay producer; no actuator is loaded."""
    return SurvivalManager()
