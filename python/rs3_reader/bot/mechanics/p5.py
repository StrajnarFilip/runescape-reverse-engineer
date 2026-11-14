"""Phase-5 strategy leads; no recorded P5 hazards are available yet."""

from .geometry import Rule

RULES = (
    Rule(5, "rock_impact", "avoid"),
    Rule(5, "final_bomb", "avoid"),
    Rule(5, "anima_virus", "interact"),
    Rule(5, "golem_target", "target"),
    Rule(5, "red_beam", "enter"),
    Rule(5, "green_beam", "enter"),
)
