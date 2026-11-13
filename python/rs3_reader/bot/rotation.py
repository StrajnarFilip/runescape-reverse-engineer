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
from .world_state import Observation, WorldState, freeze

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
        if not fresh(target) or target.value.kind != "npc" or not fresh(world.npcs):
            return None
        actors = [
            npc
            for npc in world.npcs.value
            if fresh(npc.instance_id)
            and npc.instance_id.value == target.value.instance_id
            and (
                target.value.runtime_id is None
                or fresh(npc.runtime_id)
                and npc.runtime_id.value == target.value.runtime_id
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
        """D6 pre-dispatch validation; a changed phase, gear or chord rejects it."""
        offer = self._offers.get(intent.intent_id)
        profile = self._profile(world)
        if (
            offer is None
            or profile is None
            or intent.session != world.session
            or profile["phase"] != offer[0]
            or self._fault
            or self._pending
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
            self._offers[intent.intent_id] = (profile["phase"], rule, binding, intent)
            self.last_report = RotationReport(profile["phase"], rule["id"], (), None, None)
            return (intent,)
        self.last_report = RotationReport(
            profile["phase"], None, ("no_rule_has_known_readiness_and_binding",), None, None
        )
        return ()


def create_stack():
    """Construct a proposal-only S0a replay stack; no actuator is loaded."""
    return RotationEngine()
