"""Phase-2 strategy leads; meter and beam behavior require measured state."""

from .geometry import Rule

RULES = (
    Rule(2, "onslaught", "avoid"),
    Rule(2, "black_beam", "avoid"),
    Rule(2, "anima_virus", "interact"),
)
