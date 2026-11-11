"""Version-1 Telos decision contracts. Importing this package loads no native DLL.

Use WorldState.from_telemetry(record) at the perception boundary. Measurements
are Observation objects; collection values are immutable tuples/mappings.
Unknown, missing age and incomplete collections remain distinguishable.
See world_state's module docstring for P1-P5 placeholder ownership, intents for
action lanes and priorities, and replay for the decision-stack plugin contract.
"""

from .actuator import Actuator, RecordingActuator, SendResult
from .intents import (
    ActionLane,
    ActivateAbility,
    ConsumeOperation,
    ConsumeSlot,
    Evidence,
    Intent,
    Interact,
    MoveTo,
    Priority,
    SetPrayer,
)
from .world_state import (
    CONTRACT_VERSION,
    EntityRef,
    Facing,
    Observation,
    SessionIdentity,
    Tile,
    WorldState,
)

__all__ = [
    "CONTRACT_VERSION",
    "Actuator",
    "ActionLane",
    "ActivateAbility",
    "ConsumeOperation",
    "ConsumeSlot",
    "EntityRef",
    "Evidence",
    "Facing",
    "Intent",
    "Interact",
    "MoveTo",
    "Observation",
    "Priority",
    "RecordingActuator",
    "SendResult",
    "SessionIdentity",
    "SetPrayer",
    "Tile",
    "WorldState",
]
