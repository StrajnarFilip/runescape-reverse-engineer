"""Synthetic contract exercise, NOT combat decision logic or a live controller."""

from rs3_reader.bot import (
    ActionLane,
    ActivateAbility,
    ConsumeOperation,
    ConsumeSlot,
    Evidence,
    Intent,
    Priority,
    SetPrayer,
)


class FixtureStack:
    def step(self, world):
        if world.sample_sequence != 1 or world.session.process_creation_time != 222:
            return ()
        examples = (
            ("food", ConsumeSlot(0, 101, ConsumeOperation.EAT), Priority.SURVIVAL, ActionLane.FOOD),
            (
                "potion",
                ConsumeSlot(1, 102, ConsumeOperation.DRINK),
                Priority.SURVIVAL,
                ActionLane.POTION,
            ),
            ("ability", ActivateAbility(103), Priority.ROTATION, ActionLane.GCD),
            ("prayer", SetPrayer("magic"), Priority.PRAYER, ActionLane.PRAYER),
        )
        return tuple(
            Intent(
                "fixture-" + name,
                "synthetic_example",
                action,
                priority,
                lane,
                world.now_ns,
                world.now_ns + 100_000_000,
                world.session,
                world.sample_sequence,
                (
                    Evidence(
                        "player.health.current", world.player.health.current, "contract exercise"
                    ),
                ),
                observed_ns=world.observed_ns,
            )
            for name, action, priority, lane in examples
        )


def create_stack():
    return FixtureStack()
