"""Non-channel damage spends available adrenaline and waits for exact native outcomes."""

import unittest
from types import SimpleNamespace

from rs3_reader.bot.damage_cadence import (
    DAMAGE_ABILITIES,
    ORIGINAL_CLIENT_SHA256,
    DamageCadence,
    current_telos_target,
    ready_damage,
)
from rs3_reader.native import (
    Ability,
    AbilityFlags,
    ActorSpatial,
    EncounterRaw,
    EncounterState,
    EquipmentMatch,
    NPC,
    NPCFlags,
    Reason,
    SpatialFlags,
    SpatialTargetKind,
    Status,
)


def snapshot(adrenaline=1200):
    rows = [
        Ability(
            struct_id=identity,
            flags=AbilityFlags.IN_BOOK | AbilityFlags.TIMER_VALID,
            equipment_match=EquipmentMatch.ALLOWED,
            reason=Reason.NONE,
            remaining_ms=5000,
            adrenaline_requirement_raw=600 if identity in (14688, 14686) else 0,
            start_tick=100,
        )
        for identity, _ in DAMAGE_ABILITIES
    ]
    return SimpleNamespace(
        player=SimpleNamespace(
            gcd_reason=Reason.NONE, gcd_remaining_ms=600, variable=lambda _: adrenaline
        ),
        info=SimpleNamespace(pid=1, process_creation_time=2, sequence=10, monotonic_ns=100),
        combat=SimpleNamespace(scene_epoch=3),
        inventory_info=SimpleNamespace(ability_count=len(rows)),
        abilities=rows,
    )


class DamageCadenceTests(unittest.TestCase):
    def test_damage_requires_same_frame_instance_bit_and_exact_native_combat_target(self):
        reader = snapshot()
        reader.info.status = Status.PARTIAL
        reader.info.executable_sha256 = ORIGINAL_CLIENT_SHA256.encode()
        reader.info.npc_count = 1
        reader.encounter = EncounterState(
            pid=1,
            process_creation_time=2,
            scene_epoch=3,
            sample_sequence=10,
            status=Status.PARTIAL,
            raw_count=1,
        )
        reader.encounter.raw[0] = EncounterRaw(
            varp_id=6359, value=(1 << 29) | 1000, valid=1, observed_ns=100
        )
        reader.spatial = SimpleNamespace(
            player=ActorSpatial(
                valid_mask=SpatialFlags.TARGET,
                observed_ns=100,
                target_kind=SpatialTargetKind.NPC,
                target_runtime_id=20,
            )
        )
        boss = NPC(
            instance_id=4,
            runtime_id=20,
            config_id=22891,
            flags=NPCFlags.CONFIG | NPCFlags.HEALTH,
            health_current=100000,
        )
        reader.npcs = [boss]
        self.assertTrue(current_telos_target(reader, boss, 100))
        reader.encounter.raw[0].value = 1000
        self.assertFalse(current_telos_target(reader, boss, 100))
        reader.encounter.raw[0].value |= 1 << 29
        reader.encounter.sample_sequence = 9
        self.assertFalse(current_telos_target(reader, boss, 100))
        reader.encounter.sample_sequence = 10
        reader.spatial.player.target_runtime_id = 21
        self.assertFalse(current_telos_target(reader, boss, 100))
        reader.spatial.player.target_runtime_id = 20
        self.assertFalse(current_telos_target(reader, boss, 100_000_101))

    def test_affordable_ultimate_is_used_while_basics_are_cooling(self):
        reader = snapshot()
        reader.abilities[0].remaining_ms = 500
        self.assertEqual(ready_damage(reader, reserve_tenths=500)[:2], (14688, "Meteor Strike"))
        self.assertIsNone(ready_damage(reader, basic_only=True))

    def test_resource_reserve_and_equipment_eligibility_are_independent(self):
        reader = snapshot(adrenaline=700)
        reader.abilities[0].remaining_ms = 0
        reader.abilities[3].remaining_ms = 0
        self.assertEqual(ready_damage(reader, reserve_tenths=500)[:2], (14701, "Greater Fury"))
        reader.abilities[3].equipment_match = EquipmentMatch.BLOCKED
        self.assertIsNone(ready_damage(reader, reserve_tenths=500))
        self.assertEqual(ready_damage(reader)[0], 14688)
        reader.abilities[3].equipment_match = EquipmentMatch.ALLOWED
        self.assertEqual(ready_damage(reader, available_names={"Greater Fury"})[0], 14701)

    def test_unmapped_and_failed_timers_do_not_become_first_use_permission(self):
        reader = snapshot()
        row = reader.abilities[0]
        for reason in (Reason.UNMAPPED_TIMER, Reason.READ_FAILED):
            row.reason = reason
            self.assertIsNone(ready_damage(reader))
        row.reason = Reason.NOT_SET
        self.assertEqual(ready_damage(reader)[0], 14688)
        reader.player.gcd_remaining_ms = 601
        self.assertIsNone(ready_damage(reader))

    def test_same_frame_and_generic_gcd_do_not_acknowledge_or_allow_a_second_input(self):
        reader = snapshot()
        cadence = DamageCadence()
        choice = (14688, "Meteor Strike", 100)
        cadence.sent(reader, choice, 100)
        row = reader.abilities[0]
        row.start_tick = 200
        self.assertIsNone(cadence.observe(reader))
        with self.assertRaises(RuntimeError):
            cadence.sent(reader, choice, 100)
        reader.info.sequence += 1
        reader.info.monotonic_ns += 50_000_000
        row.start_tick = 100
        self.assertIsNone(cadence.observe(reader))
        row.start_tick = 200
        self.assertEqual(cadence.observe(reader)["outcome"], "cast_confirmed")
        self.assertIsNone(cadence.pending)
        self.assertIsNone(cadence.fault)

    def test_missing_outcome_and_changed_process_do_not_retry(self):
        for changed_process in (False, True):
            reader = snapshot()
            cadence = DamageCadence()
            cadence.sent(reader, (14688, "Meteor Strike", 100), 100)
            if changed_process:
                reader.info.process_creation_time += 1
            else:
                reader.info.monotonic_ns += 2_500_000_000
                reader.abilities[0].remaining_ms = 0
            outcome = cadence.observe(reader)
            self.assertEqual(
                outcome["outcome"], "session_changed" if changed_process else "cast_not_observed"
            )
            self.assertIsNotNone(cadence.fault)
            with self.assertRaises(RuntimeError):
                cadence.sent(reader, (14688, "Meteor Strike", 100), 100)

    def test_attempt_end_keeps_an_unconfirmed_outcome_without_inventing_a_timestamp(self):
        reader = snapshot()
        cadence = DamageCadence()
        cadence.sent(reader, (14688, "Meteor Strike", 100), 100)
        outcome = cadence.finish("player_hp_zero")
        self.assertEqual(outcome["outcome"], "unconfirmed_at_attempt_end")
        self.assertIsNone(outcome["observed_native_ns"])
        self.assertIsNone(cadence.pending)
        self.assertIsNone(cadence.finish("attempt_ended"))


if __name__ == "__main__":
    unittest.main()
