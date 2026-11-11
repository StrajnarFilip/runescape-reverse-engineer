"""Immutable action proposals. Producers propose; only track D6 sends them.

Higher numeric priority wins. Deadlines/issued_ns use the native monotonic
clock, not wall time. Evidence preserves observations rather than upgrading
guide assumptions into verified facts. A proposed action is neither accepted
input nor an observed outcome.

Lanes classify actions for budget policies; they are NOT a single
one-input-per-tick limit or a complete model of shared cooldown groups.
Food, potions, abilities and prayer changes may share a game tick.
Track D6/A1 enforce configured action/timer constraints and serialization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional, Tuple, Union

from .world_state import EntityRef, Observation, SessionIdentity, Tile, to_jsonable

BACKPACK_SLOT_COUNT = 28


def _require_integer(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(label + " must be a nonnegative integer")


class Priority(IntEnum):
    ROTATION = 100
    MECHANICS = 200
    PRAYER = 300
    SURVIVAL = 400


class ActionLane(str, Enum):
    GCD = "gcd"
    OFF_GCD = "off_gcd"
    FOOD = "food"
    POTION = "potion"
    PRAYER = "prayer"
    POINTER = "pointer"


class ConsumeOperation(str, Enum):
    EAT = "eat"
    DRINK = "drink"


@dataclass(frozen=True)
class SetPrayer:
    prayer: str

    def __post_init__(self):
        if self.prayer not in ("none", "melee", "ranged", "magic", "soul_split", "necromancy"):
            raise ValueError("Unknown prayer")


@dataclass(frozen=True)
class ActivateAbility:
    """ability_id is the catalog struct_id, never an action-bar slot."""

    ability_id: int

    def __post_init__(self):
        _require_integer(self.ability_id, "Ability catalog ID")


@dataclass(frozen=True)
class ConsumeSlot:
    """Zero-based backpack slot plus preconditions against moves/dose changes."""

    slot: int
    expected_item_id: int
    operation: ConsumeOperation
    expected_tracking_token: Optional[int] = None

    def __post_init__(self):
        _require_integer(self.slot, "Backpack slot")
        _require_integer(self.expected_item_id, "Expected item ID")
        if self.slot >= BACKPACK_SLOT_COUNT:
            raise ValueError("Backpack slot must be 0..27")
        if self.expected_tracking_token is not None:
            _require_integer(self.expected_tracking_token, "Expected tracking token")
        object.__setattr__(self, "operation", ConsumeOperation(self.operation))


@dataclass(frozen=True)
class MoveTo:
    tile: Tile

    def __post_init__(self):
        if not isinstance(self.tile, Tile):
            raise TypeError("Movement requires a Tile")


@dataclass(frozen=True)
class Interact:
    target: EntityRef
    option: str

    def __post_init__(self):
        if (
            not isinstance(self.target, EntityRef)
            or not isinstance(self.option, str)
            or not self.option
        ):
            raise ValueError("Interaction requires a target and a nonempty option")


Action = Union[SetPrayer, ActivateAbility, ConsumeSlot, MoveTo, Interact]
ACTION_KINDS = {
    SetPrayer: "set_prayer",
    ActivateAbility: "activate_ability",
    ConsumeSlot: "consume_slot",
    MoveTo: "move_to",
    Interact: "interact",
}


@dataclass(frozen=True)
class Evidence:
    """Field/rule that justified the proposal, with its original age and source."""

    field: str
    observation: Observation
    note: Optional[str] = None

    def __post_init__(self):
        if (
            not isinstance(self.field, str)
            or not self.field
            or not isinstance(self.observation, Observation)
        ):
            raise ValueError("Evidence requires a field/rule and its Observation")


@dataclass(frozen=True)
class Intent:
    intent_id: str
    producer: str
    action: Action
    priority: Priority
    lane: ActionLane
    issued_ns: int
    deadline_ns: int
    session: SessionIdentity
    sample_sequence: Optional[int]
    evidence: Tuple[Evidence, ...]
    observed_ns: Optional[int] = None  # Native snapshot time; A1 rejects missing provenance.

    def __post_init__(self):
        if (
            not isinstance(self.intent_id, str)
            or not self.intent_id
            or not isinstance(self.producer, str)
            or not self.producer
        ):
            raise ValueError("Intents require a nonempty ID and producer")
        if type(self.action) not in ACTION_KINDS:
            raise ValueError("Unsupported intent action")
        if (
            any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in (self.issued_ns, self.deadline_ns)
            )
            or self.deadline_ns < self.issued_ns
        ):
            raise ValueError("Intent deadline must not precede issue time")
        object.__setattr__(self, "priority", Priority(self.priority))
        object.__setattr__(self, "lane", ActionLane(self.lane))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not isinstance(self.session, SessionIdentity):
            raise TypeError("Intent session must be a SessionIdentity")
        if self.sample_sequence is not None:
            _require_integer(self.sample_sequence, "Sample sequence")
        if self.observed_ns is not None:
            _require_integer(self.observed_ns, "Native snapshot time")
            if self.observed_ns > self.issued_ns:
                raise ValueError("Intent cannot precede the snapshot that justified it")
        if not self.evidence or any(not isinstance(item, Evidence) for item in self.evidence):
            raise ValueError("Every intent requires evidence")
        if isinstance(self.action, SetPrayer):
            if self.lane != ActionLane.PRAYER:
                raise ValueError("Prayer changes use the prayer lane")
        elif isinstance(self.action, ActivateAbility):
            if self.lane not in (ActionLane.GCD, ActionLane.OFF_GCD):
                raise ValueError("An ability requires a catalog ID and ability lane")
        elif isinstance(self.action, ConsumeSlot):
            action = self.action
            operation = ConsumeOperation(action.operation)
            expected_lane = (
                ActionLane.FOOD if operation == ConsumeOperation.EAT else ActionLane.POTION
            )
            if self.lane != expected_lane:
                raise ValueError("Food and potions use separate lanes")
        elif self.lane != ActionLane.POINTER:
            raise ValueError("Movement and interaction use the pointer lane")

    @property
    def kind(self):
        return ACTION_KINDS[type(self.action)]

    def expired(self, now_ns):
        """The deadline is inclusive, matching the recording actuator."""
        return now_ns > self.deadline_ns

    def to_record(self):
        """Return the stable version-1 JSON representation of a proposal."""
        return dict(to_jsonable(self), kind=self.kind)
