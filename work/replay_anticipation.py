"""Replay logged Telos fights through the controller's forecast and score each Hold Still.

For every attempt log under training-data/, Telos animations are fed tick by tick through
telos_zero.PhaseOne, phase by phase (a new PhaseOne after each 28963 transition, carrying the
previous expected special). At every received tick the forecast is asked for an action with
Anticipation ready; a press puts Anticipation on cooldown. Each Hold Still is reported with the
lead of the last press before it: a good forecast lands a few ticks before the stun.

Usage: python work/replay_anticipation.py [--verbose]
"""

from __future__ import annotations

import argparse
import glob
import json
import runpy
from pathlib import Path

from workspace import REPOSITORY_ROOT, add_import_paths

add_import_paths(package=True)

from rs3_reader.prayer import Prayer  # noqa: E402

CONTROLLER = runpy.run_path(str(REPOSITORY_ROOT / "work" / "telos_zero.py"))
PhaseOne = CONTROLLER["PhaseOne"]
HOLD_STILL = CONTROLLER["HOLD_STILL"]
ACTIVE_TELOS = CONTROLLER["ACTIVE_TELOS"]
TRANSITION = 28963
NS_PER_TICK = 600_000_000
# Anticipation's cooldown in received ticks (replay assumption; the live cooldown gates it).
ANTICIPATION_COOLDOWN_TICKS = 40
# A press counts as useful when it lands this many ticks before Hold Still.
USEFUL_LEAD_TICKS = (1, 12)


def telos_animations(path):
    """Return [(received tick, animation, event sequence)] for the active Telos."""
    rows = []
    for line in Path(path).open(encoding="utf-8"):
        record = json.loads(line)
        if (
            record.get("event") == "npc_animation"
            and record.get("config_id") == ACTIVE_TELOS
            and isinstance(record.get("game_tick"), int)
        ):
            rows.append((record["game_tick"], record["animation_id"], record["native_sequence"]))
    return rows


def replay(path, verbose=False):
    """Return a list of Hold Still results: (phase, tick, lead or None)."""
    animations = telos_animations(path)
    if len(animations) < 10:
        return []
    by_tick = {}
    for tick, animation, sequence in animations:
        by_tick.setdefault(tick, []).append((animation, sequence))
    phase_number, phase = 1, PhaseOne(phase=1)
    in_transition = False
    ready_tick, last_press = 0, None
    results = []
    for tick in range(animations[0][0], animations[-1][0] + 1):
        actions = []
        for animation, sequence in by_tick.get(tick, []):
            if animation == TRANSITION:
                in_transition = True
                continue
            if in_transition:
                # The first animation after the transition opens the next phase.
                in_transition = False
                phase_number += 1
                if phase_number > 5:
                    return results
                phase = PhaseOne(phase=phase_number, carried_expected=phase.expected)
                last_press = None
            if animation == HOLD_STILL:
                lead = tick - last_press if last_press is not None else None
                results.append((phase_number, tick, lead))
                if verbose:
                    print(f"  P{phase_number} Hold Still @{tick} lead={lead}")
                last_press = None
            actions.append(
                {
                    "event_sequence": sequence,
                    "action_sequence": animation,
                    "observed_ns": tick * NS_PER_TICK,
                    "received_tick": tick,
                }
            )
        phase.observe(actions)
        if in_transition:
            continue
        timers = {"Anticipation": 0 if tick >= ready_tick else 1000}
        action = phase.next_action(
            tick * NS_PER_TICK + 1,
            timers,
            0,
            Prayer.SOUL_SPLIT,
            received_tick=tick,
            flicking=True,
        )
        if action is not None and action.ability == "Anticipation":
            phase.sent(action, tick * NS_PER_TICK)
            phase.pending = None  # Acknowledged immediately in replay.
            phase.forecast_handled = True
            ready_tick = tick + ANTICIPATION_COOLDOWN_TICKS
            last_press = tick
            if verbose:
                print(f"  P{phase_number} Anticipation @{tick} (expected {phase.expected})")
    return results


def main():
    """Score every attempt log and print per-phase totals."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()
    totals = {}
    for path in sorted(glob.glob(str(REPOSITORY_ROOT / "training-data" / "telos-*.jsonl"))):
        results = replay(path, arguments.verbose)
        if not results:
            continue
        line = []
        for phase_number, tick, lead in results:
            good = lead is not None and USEFUL_LEAD_TICKS[0] <= lead <= USEFUL_LEAD_TICKS[1]
            hit, total = totals.get(phase_number, (0, 0))
            totals[phase_number] = (hit + good, total + 1)
            line.append(f"P{phase_number}@{tick}:{lead if lead is not None else 'miss'}")
        print(Path(path).name, " ".join(line))
    for phase_number in sorted(totals):
        hit, total = totals[phase_number]
        print(
            f"P{phase_number}: {hit}/{total} Hold Stills anticipated "
            f"{USEFUL_LEAD_TICKS} ticks ahead"
        )


if __name__ == "__main__":
    main()
