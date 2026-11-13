"""Phase-1 strategy leads. No lead authorizes movement without calibration."""

from .geometry import Rule

RULES = (
    Rule(1, "uppercut", "avoid"),
    Rule(1, "hold_still", "avoid"),
    Rule(1, "tendrils", "target"),
    Rule(1, "melee_distance", "enter"),
    Rule(1, "green_beam", "enter"),
)
