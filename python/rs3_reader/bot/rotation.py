"""D4 data-driven melee priority lists. No acquisition, DLL or input access.

Unknown timers, buffs, equipment match, target, binding or full castability do
not authorize a rule. Profiles are conservative policies, not validated PVME
speedkills. Adrenaline bounds are configurable reserves, never a conversion of
the cache's raw cost units. Rule selection proposes one GCD intent; only D6
may dispatch it. Accepted input waits for a newer ability-timer observation.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .intents import ActionLane, ActivateAbility, Evidence, Intent, Priority
from .world_state import EntityRef, Observation, SessionIdentity, WorldState, freeze

ROOT = Path(__file__).resolve().parents[3]
PROFILE_DIRECTORY = ROOT / "profiles" / "rotations"
ABILITY_PATH = ROOT / "profiles" / "ability_bindings_951_3.json"
NS_PER_MS = 1_000_000
PHASES = range(1, 6)
MODIFIER_MASK = 7  # Native Ctrl=1, Shift=2, Alt=4, not P5 game modifier codes.


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _key(value):
    return _integer(value) and (
        0x30 <= value <= 0x39 or 0x41 <= value <= 0x5A or 0x70 <= value <= 0x7B
    )


def _digest(document):
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RotationProfiles:
    """Load independently owned phase policies and verify their catalog identity."""

    def __init__(self, documents, definitions):
        digest = _digest(definitions)
        abilities = {entry["struct_id"]: entry for entry in definitions["abilities"]}
        if len(abilities) != len(definitions["abilities"]):
            raise ValueError("Duplicate ability definitions")
        profiles = {}
        for document in documents:
            phase = document.get("phase")
            if (
                document.get("schema_version") != 1
                or type(phase) is not int
                or phase not in PHASES
                or phase in profiles
            ):
                raise ValueError("Invalid or duplicate phase profile")
            if (
                document.get("definition_version") != definitions["version"]
                or document.get("ability_bindings_sha256") != digest
            ):
                raise ValueError("Ability definitions changed; review the rotation profiles")
            if type(document.get("live_validated")) is not bool:
                raise ValueError("Rotation requires an explicit validation status")
            for name in ("maximum_age_ms", "intent_lifetime_ms", "acknowledgement_timeout_ms"):
                if not _integer(document.get(name), 1):
                    raise ValueError("Invalid policy duration: " + name)
            hashes = document.get("client_sha256")
            if (
                not isinstance(hashes, list)
                or not hashes
                or any(
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(character not in "0123456789abcdef" for character in value)
                    for value in hashes
                )
            ):
                raise ValueError("Exact client digests are required")
            setup = document["intended_setup"]
            for name in ("weapon_item_ids", "unlocked_ability_ids"):
                if setup.get(name) is not None and (
                    not isinstance(setup[name], list)
                    or any(not _integer(value) for value in setup[name])
                ):
                    raise ValueError("Invalid configured setup identities")
            rule_ids = set()
            if not isinstance(document.get("rules"), list) or not document["rules"]:
                raise ValueError("A phase needs priority rules")
            for rule in document["rules"]:
                identity = rule.get("ability_id")
                definition = abilities.get(identity) if _integer(identity) else None
                if (
                    not rule.get("id")
                    or rule["id"] in rule_ids
                    or type(rule.get("enabled")) is not bool
                ):
                    raise ValueError("Rules require unique IDs and explicit enabled flags")
                rule_ids.add(rule["id"])
                if (
                    definition is None
                    or definition.get("in_ability_book") is not True
                    or definition.get("parent_stat") not in (1, 2)
                    or definition["name"] != rule.get("name")
                ):
                    raise ValueError("Rule is not a current melee ability: " + str(identity))
                if (
                    not _number(rule.get("minimum_adrenaline_percent"))
                    or rule["minimum_adrenaline_percent"] < 0
                    or not rule.get("source")
                ):
                    raise ValueError("Rule needs an explicit adrenaline policy and source")
                for effect in rule["effects"]:
                    if (
                        set(effect) - {"id", "active", "minimum_stacks", "minimum_remaining_ms"}
                        or not effect.get("id")
                        or type(effect.get("active")) is not bool
                    ):
                        raise ValueError("Unsupported effect condition")
                    for name in ("minimum_stacks", "minimum_remaining_ms"):
                        if name in effect and not _integer(effect[name]):
                            raise ValueError("Effect bounds must be nonnegative integers")
                binding = document["manual_bindings"].get(str(identity))
                if not isinstance(binding, Mapping) or type(binding.get("validated")) is not bool:
                    raise ValueError("Every rule needs explicit binding metadata")
                if (
                    type(binding.get("modifiers")) is not int
                    or binding["modifiers"] < 0
                    or binding["modifiers"] & ~MODIFIER_MASK
                ):
                    raise ValueError("Invalid native modifier mask")
                if binding["validated"] and (
                    not _key(binding.get("virtual_key")) or not binding.get("source")
                ):
                    raise ValueError("Validated manual binding requires a key and source")
            profiles[phase] = freeze(document)
        if set(profiles) != set(PHASES):
            raise ValueError("Exactly one rotation file for each phase 1-5 is required")
        self.profiles, self.definitions = freeze(profiles), freeze(abilities)

    @classmethod
    def load(cls, directory=PROFILE_DIRECTORY, ability_path=ABILITY_PATH):
        """Load five phase JSON files, rejecting missing, duplicate or stale data."""
        documents = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(Path(directory).glob("*.json"))
        ]
        return cls(documents, json.loads(Path(ability_path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class RotationReport:
    phase: Optional[int]
    rule: Optional[str]
    reasons: Tuple[str, ...]
    pending_intent: Optional[str]
    fault: Optional[str]


class RotationEngine:
    """One-frame priority evaluator. Serialize calls with its decision stack.

    ``can_send`` lets D6 recheck a selected rule against a newer frame. It does
    not refresh the original intent's deadline or evidence. ``note_sent`` marks
    accepted input only; cooldown feedback must acknowledge it before another
    proposal. No resource, buff, target or gear state is changed optimistically.
    """

    def __init__(self, profiles=None):
        self.profiles = RotationProfiles.load() if profiles is None else profiles
        self._identity, self._time, self._sample = None, None, None
        self._offers, self._pending, self._fault = {}, None, None
        self.last_report = RotationReport(None, None, (), None, None)

    def reset(self):
        """Clear state after explicit re-observation; no input or native reset occurs."""
        self._identity, self._time, self._sample = None, None, None
        self._offers, self._pending, self._fault = {}, None, None

    @staticmethod
    def _fresh(observation, profile):
        return (
            bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(profile["maximum_age_ms"])
        )

    def _binding(self, world, profile, identity):
        if world.action_bar.known:
            if not self._fresh(world.action_bar, profile):
                return None
            rows = [
                entry
                for entry in world.action_bar.value
                if entry.get("kind") == "ability" and entry.get("ability_struct_id") == identity
            ]
            valid = [
                entry
                for entry in rows
                if entry.get("reason") is None
                and entry.get("binding_reason") is None
                and entry.get("source")
                and isinstance(entry.get("key"), str)
            ]
            chords = []
            for entry in valid:
                label = entry["key"]
                # P5 currently validates plain/Ctrl function keys only. Never
                # turn a raw unknown modifier code into a known key binding.
                suffix = label.removeprefix("Ctrl+")
                if (
                    suffix.startswith("F")
                    and suffix[1:].isdigit()
                    and 1 <= int(suffix[1:]) <= 12
                    and label in (suffix, "Ctrl+" + suffix)
                ):
                    chords.append(
                        {
                            "virtual_key": 0x6F + int(suffix[1:]),
                            "modifiers": 1 if label.startswith("Ctrl+") else 0,
                            "source": entry["source"],
                        }
                    )
            if len(chords) != 1:
                return None
            return chords[0]
        manual = profile["manual_bindings"][str(identity)]
        return dict(manual) if manual["validated"] else None

    def _effects(self, world, profile, conditions):
        evidence = []
        for condition in conditions:
            collection = world.player.effects
            if not collection.source or not collection.is_fresh(profile["maximum_age_ms"]):
                return None
            matches = [entry for entry in collection.value if entry.get("id") == condition["id"]]
            if len(matches) != 1:
                return None
            entry = matches[0]
            if (
                entry.get("reason") is not None
                or not entry.get("source")
                or entry.get("active") is not condition["active"]
                or entry.get("candidate", {}).get("presence_validated", True) is not True
            ):
                return None
            for name, key in (
                ("minimum_stacks", "stacks"),
                ("minimum_remaining_ms", "remaining_ms"),
            ):
                if name in condition and (
                    not _number(entry.get(key)) or entry[key] < condition[name]
                ):
                    return None
            evidence.append(
                Evidence(
                    "player.effects." + condition["id"],
                    Observation(dict(entry), collection.age_ms, entry["source"]),
                )
            )
        return evidence

    def _evaluate(self, world, profile, rule):
        fresh = lambda observation: self._fresh(observation, profile)
        if (
            not rule["enabled"]
            or not fresh(world.abilities)
            or not fresh(world.equipment)
            or not fresh(world.player.gcd)
            or not fresh(world.player.adrenaline_percent)
        ):
            return None
        gcd = world.player.gcd.value
        if (
            not isinstance(gcd, Mapping)
            or gcd.get("reason") is not None
            or not _number(gcd.get("remaining_ms"))
            or gcd["remaining_ms"] != 0
        ):
            return None
        adrenaline = world.player.adrenaline_percent.value
        if (
            not _number(adrenaline)
            or adrenaline < 0
            or adrenaline < rule["minimum_adrenaline_percent"]
        ):
            return None
        matches = [
            entry for entry in world.abilities.value if entry.get("struct_id") == rule["ability_id"]
        ]
        if len(matches) != 1:
            return None
        ability = matches[0]
        timer = ability.get("cooldown") or {}
        if (
            ability.get("castable") is not True
            or ability.get("in_ability_book") is not True
            or ability.get("name") != rule["name"]
            or ability.get("equipment_match") != "allowed"
            or timer.get("reason") is not None
            or not _number(timer.get("remaining_ms"))
            or timer["remaining_ms"] != 0
        ):
            return None
        hand = world.equipment.value.get("main_hand") or {}
        gear = hand.get("equipment") or {}
        if (
            gear.get("weapon") is not True
            or gear.get("style") != "melee"
            or not _integer(hand.get("item_id"))
        ):
            return None
        setup = profile["intended_setup"]
        if setup["weapon_item_ids"] is not None and hand["item_id"] not in setup["weapon_item_ids"]:
            return None
        if (
            setup["unlocked_ability_ids"] is not None
            and rule["ability_id"] not in setup["unlocked_ability_ids"]
        ):
            return None
        target = world.player.target
        if (
            not fresh(target)
            or target.value.kind != "npc"
            or not _integer(target.value.instance_id, 1)
            or target.value.instance_id >= 2**64
            or not _integer(target.value.runtime_id)
            or target.value.runtime_id > 65535
            or not _integer(target.value.config_id)
            or target.value.config_id >= 2**31
            or not fresh(world.npcs)
        ):
            return None
        actors = [
            npc
            for npc in world.npcs.value
            if all(
                fresh(observation)
                and type(observation.value) is int
                and observation.value == expected
                for observation, expected in (
                    (npc.instance_id, target.value.instance_id),
                    (npc.runtime_id, target.value.runtime_id),
                    (npc.config_id, target.value.config_id),
                )
            )
        ]
        if (
            len(actors) != 1
            or not fresh(actors[0].health.current)
            or not _number(actors[0].health.current.value)
            or actors[0].health.current.value <= 0
        ):
            return None
        binding = self._binding(world, profile, rule["ability_id"])
        effects = self._effects(world, profile, rule["effects"])
        if binding is None or effects is None:
            return None
        evidence = [
            Evidence(
                "abilities",
                world.abilities,
                "known ready timer, equipment match and full castability",
            ),
            Evidence("player.gcd", world.player.gcd),
            Evidence("player.adrenaline_percent", world.player.adrenaline_percent),
            Evidence("equipment", world.equipment),
            Evidence("player.target", target),
            Evidence("target.instance_id", actors[0].instance_id),
            Evidence("target.runtime_id", actors[0].runtime_id),
            Evidence("target.config_id", actors[0].config_id),
            Evidence("target.health", actors[0].health.current),
            Evidence("telos.phase", world.telos.phase),
        ]
        evidence.extend(effects)
        evidence.append(
            Evidence(
                "rotation.binding",
                Observation(
                    binding,
                    world.action_bar.age_ms if world.action_bar.known else 0,
                    binding["source"],
                ),
            )
        )
        evidence.append(
            Evidence(
                "rotation.rule",
                Observation(
                    {
                        "phase": profile["phase"],
                        "id": rule["id"],
                        "live_validated": profile["live_validated"],
                    },
                    0,
                    "local_rotation_policy",
                ),
                "priority policy, not proof of optimal or validated execution",
            )
        )
        return tuple(evidence), binding

    def _profile(self, world):
        phase = world.telos.phase.value
        profile = self.profiles.profiles.get(phase) if type(phase) is int else None
        if (
            profile is None
            or not self._fresh(world.telos.phase, profile)
            or world.raw.get("executable_sha256") not in profile["client_sha256"]
        ):
            return None
        if (
            not world.session.complete
            or world.status not in ("available", "partial")
            or not _integer(world.now_ns)
            or not _integer(world.observed_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0 <= world.now_ns - world.observed_ns <= profile["maximum_age_ms"] * NS_PER_MS
        ):
            return None
        return profile

    def can_send(self, intent, world):
        """D6 validation of the offered action and its current exact NPC target."""
        offer = self._offers.get(intent.intent_id)
        profile = self._profile(world)
        if (
            offer is None
            or intent != offer[3]
            or profile is None
            or intent.session != world.session
            or profile["phase"] != offer[0]
            or self._fault
            or self._pending
            or world.player.target.value != offer[4]
        ):
            return False
        evaluated = self._evaluate(world, profile, offer[1])
        return evaluated is not None and all(
            evaluated[1].get(key) == offer[2].get(key) for key in ("virtual_key", "modifiers")
        )

    def note_sent(self, intent_id, sent_ns):
        """Record accepted input; a newer ability cooldown must acknowledge it."""
        offer = self._offers.get(intent_id)
        if (
            offer is None
            or self._pending
            or not _integer(sent_ns)
            or not offer[3].issued_ns <= sent_ns <= offer[3].deadline_ns
        ):
            raise ValueError("Unknown or already pending accepted rotation intent")
        self._pending = (offer[3], sent_ns)

    def note_rejected(self, intent_id, reason, sent_ns):
        """D6 rejection is diagnostic only; it never advances the rotation."""
        self.last_report = RotationReport(
            self.last_report.phase,
            self.last_report.rule,
            ("send_rejected:" + str(reason),),
            self._pending[0].intent_id if self._pending else None,
            self._fault,
        )

    def step(self, world):
        """Propose at most one offensive GCD ability from current observations."""
        if not isinstance(world, WorldState):
            raise TypeError("RotationEngine requires WorldState")
        identity = (world.session, world.raw.get("executable_sha256"))
        if identity != self._identity:
            self.reset()
            self._identity = identity
        self._offers = {}
        profile = self._profile(world)
        if profile is None:
            self.last_report = RotationReport(
                None,
                None,
                ("phase_profile_or_snapshot_unavailable",),
                self._pending[0].intent_id if self._pending else None,
                self._fault,
            )
            return ()
        if self._time is not None and (
            world.now_ns < self._time or world.sample_sequence < self._sample
        ):
            self._fault = "clock_or_sample_regressed"
        self._time, self._sample = world.now_ns, world.sample_sequence
        if self._pending:
            intent, sent_ns = self._pending
            newer = world.observed_ns > sent_ns and world.sample_sequence > intent.sample_sequence
            if (
                newer
                and self._fresh(world.abilities, profile)
                and any(
                    entry.get("struct_id") == intent.action.ability_id
                    and _number((entry.get("cooldown") or {}).get("remaining_ms"))
                    and entry["cooldown"]["remaining_ms"] > 0
                    and entry["cooldown"].get("reason") is None
                    for entry in world.abilities.value
                )
            ):
                self._pending = None
            elif world.now_ns - sent_ns > profile["acknowledgement_timeout_ms"] * NS_PER_MS:
                self._fault = "acknowledgement_timeout"
        if self._fault or self._pending:
            self.last_report = RotationReport(
                profile["phase"],
                None,
                ("awaiting_confirmation",),
                self._pending[0].intent_id if self._pending else None,
                self._fault,
            )
            return ()
        for rule in profile["rules"]:
            evaluated = self._evaluate(world, profile, rule)
            if evaluated is None:
                continue
            evidence, binding = evaluated
            intent = Intent(
                "d4:{}:{}:{}:{}:{}".format(
                    world.session.pid,
                    world.session.process_creation_time,
                    world.session.scene_epoch,
                    world.sample_sequence,
                    rule["id"],
                ),
                "rotation",
                ActivateAbility(rule["ability_id"]),
                Priority.ROTATION,
                ActionLane.GCD,
                world.now_ns,
                world.now_ns + profile["intent_lifetime_ms"] * NS_PER_MS,
                world.session,
                world.sample_sequence,
                evidence,
                world.observed_ns,
            )
            self._offers[intent.intent_id] = (
                profile["phase"],
                rule,
                binding,
                intent,
                world.player.target.value,
            )
            self.last_report = RotationReport(profile["phase"], rule["id"], (), None, None)
            return (intent,)
        self.last_report = RotationReport(
            profile["phase"], None, ("no_rule_has_known_readiness_and_binding",), None, None
        )
        return ()


def create_stack():
    """Construct a proposal-only S0a replay stack; no actuator is loaded."""
    return RotationEngine()


P1_SEQUENCE_PATH = PROFILE_DIRECTORY / "sequences" / "p1-pvme.json"
MAX_SEQUENCE_STEPS = 32
MAX_SEQUENCE_ACTIONS = 4


@dataclass(frozen=True)
class SequenceBatch:
    """An ordered request to an actuator adapter, not an accepted input or cast.

    All actions in a same_tick batch must be prepared before the first input.
    Their tuple order is significant. Server processing time remains unknown
    until separately measured; a native input batch alone does not establish it.
    """

    batch_id: str
    step_id: str
    actions: tuple
    same_tick: bool
    session: SessionIdentity
    target: EntityRef
    sample_sequence: int
    observed_ns: int
    issued_ns: int
    deadline_ns: int


class OrderedP1Rotation:
    """Pure acknowledgement-driven P1 coordinator for the supplied PVME order.

    The adapter supplies current per-action readiness and confirmations as
    Observations. This class acquires no memory and sends no input. It does
    not infer castability, upgrade unlocks, EoF contents or successful specials.
    An adapter must check those action-specific facts against native state.
    Missing readiness blocks the current step instead of trying a later one.
    """

    def __init__(self, document=None):
        document = (
            json.loads(P1_SEQUENCE_PATH.read_text(encoding="utf-8"))
            if document is None
            else document
        )
        steps = document.get("steps")
        if (
            document.get("schema_version") != 1
            or document.get("phase") != 1
            or document.get("movement_owner") != "user"
            or document.get("live_validated") is not False
            or not isinstance(steps, list)
            or not 1 <= len(steps) <= MAX_SEQUENCE_STEPS
            or not _integer(document.get("maximum_age_ms"), 1)
            or not _integer(document.get("acknowledgement_timeout_ms"), 1)
            or not isinstance(document.get("client_sha256"), str)
            or len(document["client_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in document["client_sha256"])
        ):
            raise ValueError("Invalid supervised P1 sequence")
        identifiers, step_ids = set(), set()
        for step in steps:
            actions = step.get("actions")
            if (
                not isinstance(step.get("id"), str)
                or not step["id"]
                or step["id"] in step_ids
                or type(step.get("same_tick")) is not bool
                or not isinstance(actions, list)
                or not 1 <= len(actions) <= MAX_SEQUENCE_ACTIONS
            ):
                raise ValueError("Invalid or duplicate P1 step")
            step_ids.add(step["id"])
            for action in actions:
                if (
                    not isinstance(action.get("id"), str)
                    or not action["id"]
                    or action["id"] in identifiers
                    or action.get("kind")
                    not in (
                        "ability",
                        "item",
                        "equip",
                        "weapon_special",
                        "eof_special",
                        "target_cycle",
                    )
                    or not isinstance(action.get("key"), str)
                    or not action["key"]
                ):
                    raise ValueError("Invalid or duplicate P1 action")
                identifiers.add(action["id"])
        self.document = freeze(document)
        self.index = 0
        self.session = None
        self.fault = None
        self.reasons = ()
        self._offer = None
        self._pending = None
        self._confirmed = set()
        self._last_sample = None
        self._last_time = None
        self._encounter_identity = None
        self._dormant_seen = False
        self._wake_observed_ns = None
        self._wake_tick = None

    @property
    def complete(self):
        """Whether every configured action received its own later confirmation."""
        return self.index == len(self.document["steps"])

    @property
    def current_step(self):
        """Return the next step without skipping unavailable or failed actions."""
        return None if self.complete else self.document["steps"][self.index]

    def _fresh(self, observation):
        return (
            isinstance(observation, Observation)
            and bool(observation.source)
            and observation.reason is None
            and observation.is_fresh(self.document["maximum_age_ms"])
        )

    def _target(self, world):
        """Resolve the exact live Telos lifetime; a target clear is not a wildcard."""
        target = world.player.target
        if (
            not self._fresh(target)
            or target.value.kind != "npc"
            or target.value.config_id != 22891
            or not _integer(target.value.instance_id, 1)
            or not _integer(target.value.runtime_id)
            or not self._fresh(world.npcs)
        ):
            return None
        matches = [
            npc
            for npc in world.npcs.value
            if all(
                self._fresh(observation) and observation.value == expected
                for observation, expected in (
                    (npc.instance_id, target.value.instance_id),
                    (npc.runtime_id, target.value.runtime_id),
                    (npc.config_id, target.value.config_id),
                )
            )
        ]
        if (
            len(matches) != 1
            or not self._fresh(matches[0].health.current)
            or not _number(matches[0].health.current.value)
            or matches[0].health.current.value <= 0
        ):
            return None
        return target.value

    def _encounter_npc(self, world):
        """Read one exact dormant/active Telos; a form change preserves its lifetime."""
        if not self._fresh(world.npcs):
            return None
        actors = [
            npc
            for npc in world.npcs.value
            if all(self._fresh(value) for value in (npc.instance_id, npc.runtime_id, npc.config_id))
            and _integer(npc.instance_id.value, 1)
            and _integer(npc.runtime_id.value)
            and npc.config_id.value
            in (self.document["dormant_config_id"], self.document["active_config_id"])
        ]
        if len(actors) != 1:
            return None
        actor = actors[0]
        return EntityRef(
            "npc", actor.instance_id.value, actor.runtime_id.value, actor.config_id.value
        )

    def step(self, world, readiness=None):
        """Offer the current whole batch only after every action is explicitly ready.

        Readiness is an adapter-owned mapping of action ID to Observation(True).
        It is permission to attempt that exact action, never a replacement for
        WorldState's castability field. Confirmations are supplied separately.
        """
        if not isinstance(world, WorldState):
            raise TypeError("OrderedP1Rotation requires WorldState")
        self._offer = None
        if self.fault or self.complete:
            return None
        if not world.session.complete:
            self.reasons = ("session_unavailable",)
            return None
        if self.session is None:
            self.session = world.session
        if self.session != world.session:
            self.fault = "session_changed"
            return None
        if (
            world.status not in ("available", "partial")
            or world.raw.get("executable_sha256") != self.document["client_sha256"]
            or not _integer(world.now_ns)
            or not _integer(world.observed_ns)
            or not _integer(world.sample_sequence, 1)
            or not 0
            <= world.now_ns - world.observed_ns
            <= self.document["maximum_age_ms"] * NS_PER_MS
        ):
            self.reasons = ("snapshot_unavailable_or_stale",)
            return None
        if self._last_sample is not None and (
            world.sample_sequence < self._last_sample or world.now_ns < self._last_time
        ):
            self.fault = "clock_or_sample_regressed"
            return None
        self._last_sample, self._last_time = world.sample_sequence, world.now_ns
        target = self._encounter_npc(world)
        if target is None:
            self.reasons = ("unique_telos_form_unavailable",)
            return None
        identity = target.instance_id, target.runtime_id
        if self._encounter_identity is None:
            self._encounter_identity = identity
        elif self._encounter_identity != identity:
            self.fault = "telos_lifetime_changed"
            return None
        dormant = target.config_id == self.document["dormant_config_id"]
        if dormant:
            self._dormant_seen = True
        elif self.document.get("require_dormant_start") and not self._dormant_seen:
            self.fault = "dormant_start_not_observed"
            return None
        elif self._wake_observed_ns is None:
            self._wake_observed_ns = world.observed_ns
            tick = world.game_tick
            if self._fresh(tick) and isinstance(tick.value, Mapping):
                self._wake_tick = tick.value.get("index")
        if self._pending:
            if (
                world.now_ns - self._pending[1]
                > self.document["acknowledgement_timeout_ms"] * NS_PER_MS
            ):
                self.fault = "acknowledgement_timeout"
            self.reasons = ("awaiting_individual_native_confirmations",)
            return None
        step = self.current_step
        if step.get("opening_only"):
            if not dormant:
                self.fault = "opening_window_missed"
                return None
        elif not (step.get("requires_wake_edge") and dormant) and (
            not self._fresh(world.telos.phase) or world.telos.phase.value != 1
        ):
            self.reasons = ("phase_one_not_observed",)
            return None
        if step.get("requires_wake_edge"):
            if dormant or self._wake_observed_ns is None:
                self.reasons = ("waiting_for_native_wake_candidate",)
                return None
            tick = world.game_tick
            if world.now_ns - self._wake_observed_ns > self.document[
                "maximum_age_ms"
            ] * NS_PER_MS or (
                self._wake_tick is not None
                and self._fresh(tick)
                and isinstance(tick.value, Mapping)
                and tick.value.get("index") != self._wake_tick
            ):
                self.fault = "wake_window_missed"
                return None
        if step.get("requires_target", True) and self._target(world) != target:
            self.reasons = ("exact_live_telos_target_unavailable",)
            return None
        readiness = {} if readiness is None else readiness
        if not isinstance(readiness, Mapping):
            raise TypeError("P1 readiness must be a mapping of action IDs to Observations")
        missing = tuple(
            "action_not_ready:" + action["id"]
            for action in step["actions"]
            if not self._fresh(readiness.get(action["id"]))
            or readiness[action["id"]].value is not True
        )
        self.reasons = missing
        if missing:
            return None
        self._offer = SequenceBatch(
            "p1:{}:{}:{}:{}:{}".format(
                world.session.pid,
                world.session.process_creation_time,
                world.session.scene_epoch,
                world.sample_sequence,
                step["id"],
            ),
            step["id"],
            step["actions"],
            step["same_tick"],
            world.session,
            target,
            world.sample_sequence,
            world.observed_ns,
            world.now_ns,
            world.observed_ns + self.document["maximum_age_ms"] * NS_PER_MS,
        )
        return self._offer

    def note_sent(self, batch, accepted_action_ids, sent_ns):
        """Record one complete ordered submission; partial submission latches a fault."""
        if (
            not isinstance(batch, SequenceBatch)
            or batch != self._offer
            or self._pending
            or self.fault
            or not _integer(sent_ns)
            or not batch.issued_ns <= sent_ns <= batch.deadline_ns
        ):
            raise ValueError("Unknown, stale or pending P1 batch")
        expected = tuple(action["id"] for action in batch.actions)
        if tuple(accepted_action_ids) != expected:
            self.fault = "partial_or_reordered_batch"
            self._offer = None
            return
        self._pending = batch, sent_ns
        self._offer = None
        self._confirmed.clear()

    def confirm(self, action_id, observation, world):
        """Accept one exact-action confirmation from a strictly newer native frame.

        Input acceptance, a generic GCD transition or an unlabelled adrenaline
        change is not an action-specific confirmation. Adapters must retain
        unknown results when their native acknowledgement channel is unmapped.
        """
        if self._pending is None or self.fault:
            return False
        batch, sent_ns = self._pending
        identifiers = {action["id"] for action in batch.actions}
        step = self.current_step
        target = self._encounter_npc(world) if isinstance(world, WorldState) else None
        same_actor = target is not None and (target.instance_id, target.runtime_id) == (
            batch.target.instance_id,
            batch.target.runtime_id,
        )
        if (
            action_id not in identifiers
            or action_id in self._confirmed
            or not isinstance(world, WorldState)
            or world.session != batch.session
            or world.status not in ("available", "partial")
            or world.raw.get("executable_sha256") != self.document["client_sha256"]
            or not _integer(world.sample_sequence, 1)
            or world.sample_sequence <= batch.sample_sequence
            or world.sample_sequence < self._last_sample
            or not _integer(world.observed_ns)
            or world.observed_ns <= sent_ns
            or not _integer(world.now_ns)
            or world.now_ns < self._last_time
            or not 0
            <= world.now_ns - world.observed_ns
            <= self.document["maximum_age_ms"] * NS_PER_MS
            or world.now_ns - sent_ns > self.document["acknowledgement_timeout_ms"] * NS_PER_MS
            or not same_actor
            or (
                not step.get("opening_only")
                and (not self._fresh(world.telos.phase) or world.telos.phase.value != 1)
            )
            or (step.get("requires_target", True) and self._target(world) != target)
            or (action_id == "target_cycle" and self._target(world) != target)
            or not self._fresh(observation)
            or observation.value is not True
        ):
            return False
        self._confirmed.add(action_id)
        if self._confirmed == identifiers:
            self.index += 1
            self._pending = None
            self._confirmed.clear()
            self.reasons = ()
        return True
