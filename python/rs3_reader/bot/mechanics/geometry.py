"""D5 geometry relative to a fresh, calibrated instanced-arena anchor."""

from dataclasses import dataclass
from typing import Mapping

from ..world_state import Observation, SessionIdentity, Tile

NS_PER_MS = 1_000_000


@dataclass(frozen=True)
class Rule:
    """A strategy lead or an explicitly client/enrage-scoped calibrated rule."""

    phase: int
    hazard: str
    mode: str
    validated: bool = False
    source: str = "guide lead: docs/telos-melee-research.rst"
    option: str = None
    client_sha256: str = None
    enrage_range: tuple = None


@dataclass(frozen=True)
class ArenaGeometry:
    """Observed anchor, quarter-turn orientation, bounds and local safe tiles."""

    anchor: Tile
    quarter_turns: int
    bounds: tuple
    safe_tiles: tuple

    def contains(self, offset):
        """Check local coordinate shape and observed bounds."""
        return (
            isinstance(offset, (tuple, list))
            and len(offset) == 2
            and all(type(value) is int for value in offset)
            and self.bounds[0] <= offset[0] <= self.bounds[1]
            and self.bounds[2] <= offset[1] <= self.bounds[3]
        )

    def to_world(self, offset):
        """Rotate an in-bounds local offset; never substitute an absolute arena tile."""
        if not self.contains(offset):
            raise ValueError("Relative arena position is unknown or out of bounds")
        x, y = offset
        for _ in range(self.quarter_turns):
            x, y = -y, x
        return Tile(self.anchor.x + x, self.anchor.y + y, self.anchor.plane)

    def to_local(self, tile):
        """Invert the anchor transform; unknown plane/outside tiles return None."""
        if tile.plane != self.anchor.plane:
            return None
        x, y = tile.x - self.anchor.x, tile.y - self.anchor.y
        for _ in range(self.quarter_turns):
            x, y = y, -x
        return (x, y) if self.contains((x, y)) else None

    @classmethod
    def from_world(cls, world, maximum_age_ms):
        """Accept only an explicit, fresh P4 arena envelope with matching identity."""
        raw = world.raw.get("arena")
        if not isinstance(raw, Mapping):
            return None
        age = raw.get("age_ms")
        captured_age = world.raw.get("native", {}).get("snapshot_age_ms")
        captured_age = captured_age if type(captured_age) in (int, float) else 0
        if type(age) in (int, float):
            age += max(0, (world.now_ns - world.observed_ns) / NS_PER_MS - captured_age)
        observation = Observation(raw.get("value"), age, raw.get("source"), raw.get("reason"))
        if (
            not observation.source
            or observation.reason is not None
            or not observation.is_fresh(maximum_age_ms)
        ):
            return None
        value = observation.value
        if not isinstance(value, Mapping) or value.get("validated") is not True:
            return None
        session = value.get("session", {})
        if (
            SessionIdentity(
                session.get("pid"), session.get("process_creation_time"), session.get("scene_epoch")
            )
            != world.session
        ):
            return None
        bounds, rotation = value.get("bounds"), value.get("quarter_turns")
        if (
            not isinstance(bounds, (tuple, list))
            or len(bounds) != 4
            or any(type(number) is not int for number in bounds)
            or bounds[0] > bounds[1]
            or bounds[2] > bounds[3]
            or type(rotation) is not int
            or rotation not in range(4)
        ):
            return None
        try:
            result = cls(
                Tile(**value["anchor"]),
                rotation,
                tuple(bounds),
                tuple(tuple(tile) for tile in value["safe_tiles"]),
            )
            if not result.safe_tiles or not all(
                result.contains(tile) for tile in result.safe_tiles
            ):
                return None
            for tile in result.safe_tiles:
                result.to_world(tile)
            return result
        except (KeyError, TypeError, ValueError):
            return None
