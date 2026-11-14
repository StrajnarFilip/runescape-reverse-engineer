"""Phase-3 strategy leads; unknown meter/beam/golem state never becomes safe."""

from .geometry import Rule

RULES = (
    Rule(3, "meter_extreme", "avoid"),
    Rule(3, "golem_target", "target"),
    Rule(3, "anima_virus", "interact"),
    Rule(3, "red_beam", "enter"),
)
