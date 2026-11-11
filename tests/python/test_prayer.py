"""Exercise the exported C prayer planner through ctypes, without game input."""

import ctypes
import json
import unittest

from rs3_reader.native import (
    REPOSITORY_ROOT,
    CombatState,
    CombatStructure,
    NPCAnimation,
    PlayerHitEvent,
    PlayerHitInfo,
    Reader,
    Status,
    library_path,
    load_library,
)
from rs3_reader.prayer import (
    OVERHEAD_PRAYER_MASK,
    PLANNER_STRUCTURES,
    Action,
    Frame,
    FrameFlags,
    Planner,
    Prayer,
    Threat,
    ThreatFlags,
)

START_NS = 10_000_000_000
LIVE_FLAGS = FrameFlags.CONNECTED | FrameFlags.ACTIVE_KNOWN | FrameFlags.POINTS_KNOWN
ALL_FLAGS = LIVE_FLAGS | FrameFlags.COVERAGE_KNOWN
PRAYER_POINTS = 840
INCOMPLETE_COVERAGE_REASON = 9
# A PID that never belongs to a running client.
ABSENT_PID = 0xFFFFFFFF


def load_settings():
    return json.loads((REPOSITORY_ROOT / "profiles" / "prayer_flick.json").read_text())


def frame(active_prayer, coverage_until_ns=0, flags=LIVE_FLAGS):
    """A fresh frame at START_NS with every overhead available."""
    return Frame(
        START_NS,
        START_NS,
        1,
        1,
        coverage_until_ns,
        flags,
        active_prayer,
        OVERHEAD_PRAYER_MASK,
        PRAYER_POINTS,
    )


@unittest.skipUnless(library_path().is_file(), "Build rs3_native for FFI checks")
class PrayerPlannerTests(unittest.TestCase):
    def test_additive_abi_sizes(self):
        library = load_library()
        self.assertEqual(
            library.rs3_combat_sizeof(CombatStructure.STATE), ctypes.sizeof(CombatState)
        )
        self.assertEqual(
            library.rs3_combat_sizeof(CombatStructure.ANIMATION), ctypes.sizeof(NPCAnimation)
        )
        self.assertEqual(
            library.rs3_combat_sizeof(CombatStructure.PLAYER_HIT_INFO),
            ctypes.sizeof(PlayerHitInfo),
        )
        self.assertEqual(
            library.rs3_combat_sizeof(CombatStructure.PLAYER_HIT_EVENT),
            ctypes.sizeof(PlayerHitEvent),
        )
        with Planner(load_settings()) as planner:
            for structure_id, structure in PLANNER_STRUCTURES.items():
                self.assertEqual(
                    planner.library.rs3_prayer_sizeof(structure_id), ctypes.sizeof(structure)
                )

    def test_soul_split_proposal_needs_a_newer_observed_acknowledgement(self):
        with Planner(load_settings()) as planner:
            current_frame = frame(Prayer.MAGIC, coverage_until_ns=2 * START_NS, flags=ALL_FLAGS)
            decision = planner.step(current_frame)
            self.assertEqual((decision.action, decision.prayer), (Action.SET, Prayer.SOUL_SPLIT))
            planner.note_sent(decision.command_id, current_frame.now_ns)
            current_frame.now_ns += 10_000_000
            self.assertEqual(planner.step(current_frame).action, Action.WAIT_ACK)
            current_frame.sequence += 1
            current_frame.observed_ns = current_frame.now_ns
            current_frame.active_prayer = Prayer.SOUL_SPLIT
            self.assertEqual(planner.step(current_frame).action, Action.HOLD)
        with self.assertRaises(RuntimeError):
            planner.step(current_frame)

    def test_unclassified_live_coverage_never_authorizes_soul_split(self):
        with Planner(load_settings()) as planner:
            current_frame = frame(Prayer.MAGIC)
            decision = planner.step(current_frame)
            self.assertEqual(
                (decision.action, decision.reason), (Action.HOLD, INCOMPLETE_COVERAGE_REASON)
            )
            now_ns = current_frame.now_ns
            unknown_attack = Threat(1, 42, now_ns, now_ns, Prayer.UNKNOWN, 0)
            self.assertEqual(planner.step(current_frame, [unknown_attack]).action, Action.BLOCKED)

    def test_unavailable_player_hitmarks_are_not_stale_records(self):
        with Reader(pid=ABSENT_PID, worker_hz=0) as reader:
            reader.sample()
            self.assertEqual(reader.player_hit_info.status, Status.UNAVAILABLE)
            self.assertEqual(reader.player_hit_info.hit_count, 0)
            self.assertEqual(reader.drain_player_hit_events(), ([], 0))

    def test_replaced_proposal_rejects_the_old_command_id(self):
        with Planner(load_settings()) as planner:
            current_frame = frame(Prayer.SOUL_SPLIT)
            attack_ns = 10_600_000_000
            threat = Threat(1, 42, attack_ns, attack_ns, Prayer.MAGIC, ThreatFlags.TIMING_VALIDATED)
            old_decision = planner.step(current_frame, [threat])
            threat.prayer = Prayer.MELEE
            new_decision = planner.step(current_frame, [threat])
            self.assertNotEqual(old_decision.command_id, new_decision.command_id)
            with self.assertRaises(RuntimeError):
                planner.note_sent(old_decision.command_id, current_frame.now_ns)


if __name__ == "__main__":
    unittest.main()
