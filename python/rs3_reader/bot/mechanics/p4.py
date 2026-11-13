"""Phase-4 strategy leads. Font positions and options are never guessed."""

from .geometry import Rule

RULES = (
    Rule(4, "font", "interact"),
    Rule(4, "golem_target", "target"),
    Rule(4, "anima_bomb", "avoid"),
)
