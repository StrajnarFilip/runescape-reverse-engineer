"""The bounded dummy probe never treats absent effects or another target as ready."""

import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rs3_reader.native import (
    Ability,
    AbilityFlags,
    Effect,
    EquipmentMatch,
    Item,
    NPC,
    NPCFlags,
    Reason,
    Status,
    Variable,
)

probe = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "work" / "calibrate_special_effects.py")
)


class SpecialEffectCalibrationTests(unittest.TestCase):
    def test_cadence_mode_never_dispatches_a_special_when_all_basics_are_cooling(self):
        """Exercise the loop, including the former fallthrough into a physical spec."""
        info = SimpleNamespace(
            pid=1,
            process_creation_time=2,
            executable_sha256=probe["EXPECTED_HASH"].encode(),
            status=Status.AVAILABLE,
            monotonic_ns=100,
        )
        reader = SimpleNamespace(
            sample=Mock(),
            info=info,
            combat=SimpleNamespace(scene_epoch=3),
            player=SimpleNamespace(
                variable=lambda identity: 10400 if identity == Variable.HEALTH else 1200,
                gcd_reason=Reason.NONE,
                gcd_remaining_ms=0,
            ),
            monotonic_ns=lambda: 100,
            drain_npc_action_events=lambda: ([], 0),
            drain_player_hit_events=lambda: ([], 0),
            effects_info=SimpleNamespace(count=1),
            effects=[Effect(effect_id=15, raw_expiry=0, raw_valid_mask=1)],
        )
        info.logic_tick = 1000
        device = Mock()
        keys = SimpleNamespace(get_async_key_state=lambda _: 0, game_window=lambda _: 1)
        attempt = SimpleNamespace(
            frame=Mock(),
            write=Mock(),
            inventory_lost=0,
            snapshot_events=SimpleNamespace(npc_hits_lost=0),
        )
        target = dict(kind="npc", config_id=16027, instance_id=1, runtime_id=2)
        loop = probe["run"]
        with patch.dict(
            loop.__globals__,
            {
                "time": SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 2]), sleep=Mock()),
                "NativeTelemetry": Mock(),
                "WorldState": Mock(),
                "selected_dummy": Mock(return_value=target),
                "equipped": Mock(return_value=True),
                "ready_basic": Mock(return_value=None),
                "role_descriptor": Mock(return_value=(object(), {})),
            },
        ):
            self.assertEqual(
                loop(reader, device, keys, attempt, duration=1, basic_casts=8), "time_limit"
            )
        reader.sample.assert_called_once()
        device.dispatch_key_batch.assert_not_called()

    def test_basic_precast_requires_the_last_gcd_tick_and_its_own_timer_to_fit(self):
        ability = Ability(
            struct_id=14679,
            flags=AbilityFlags.IN_BOOK | AbilityFlags.TIMER_VALID,
            equipment_match=EquipmentMatch.ALLOWED,
            reason=Reason.NONE,
            remaining_ms=300,
            adrenaline_requirement_raw=0,
            start_tick=100,
        )
        reader = SimpleNamespace(
            player=SimpleNamespace(gcd_reason=0, gcd_remaining_ms=600),
            inventory_info=SimpleNamespace(ability_count=1),
            abilities=[ability],
        )
        self.assertEqual(probe["ready_basic"](reader), (14679, "Adaptive Strike", 100))
        reader.player.gcd_remaining_ms = 601
        self.assertIsNone(probe["ready_basic"](reader))
        reader.player.gcd_remaining_ms = 200
        self.assertIsNone(probe["ready_basic"](reader))
        ability.remaining_ms = 0
        ability.adrenaline_requirement_raw = 100
        self.assertIsNone(probe["ready_basic"](reader))

    def test_basic_first_use_is_bounded_permission_and_read_failures_still_block(self):
        ability = Ability(
            struct_id=14679,
            flags=AbilityFlags.IN_BOOK,
            equipment_match=EquipmentMatch.ALLOWED,
            reason=Reason.NOT_SET,
            adrenaline_requirement_raw=0,
        )
        reader = SimpleNamespace(
            player=SimpleNamespace(gcd_reason=Reason.NOT_SET, gcd_remaining_ms=0),
            inventory_info=SimpleNamespace(ability_count=1),
            abilities=[ability],
        )
        self.assertIsNotNone(probe["ready_basic"](reader))
        reader.player.gcd_reason = Reason.READ_FAILED
        self.assertIsNone(probe["ready_basic"](reader))

    def test_other_bar_basics_fill_an_adaptive_strike_cooldown_gap(self):
        rows = [
            Ability(
                struct_id=identity,
                flags=AbilityFlags.IN_BOOK | AbilityFlags.TIMER_VALID,
                equipment_match=EquipmentMatch.ALLOWED,
                reason=Reason.NONE,
                remaining_ms=5000,
                adrenaline_requirement_raw=0,
            )
            for identity, _ in probe["BASIC_ABILITIES"]
        ]
        reader = SimpleNamespace(
            player=SimpleNamespace(gcd_reason=0, gcd_remaining_ms=600),
            inventory_info=SimpleNamespace(ability_count=len(rows)),
            abilities=rows,
        )
        dismember = next(row for row in rows if row.struct_id == 44244)
        dismember.remaining_ms = 0
        self.assertEqual(probe["ready_basic"](reader)[:2], (44244, "Dismember"))

    def test_unset_effect_is_not_an_expired_zero(self):
        effect = Effect(effect_id=15, raw_expiry=0, raw_valid_mask=0)
        reader = SimpleNamespace(effects_info=SimpleNamespace(count=1), effects=[effect])
        self.assertIsNone(probe["effect_endpoint"](reader, 15))
        effect.raw_valid_mask = 1
        self.assertEqual(probe["effect_endpoint"](reader, 15), 0)

    def test_equipment_requires_current_readable_identity(self):
        item = Item(container_id=94, slot=3, item_id=52543, quantity=1)
        info = SimpleNamespace(flags=2, backpack_count=0, equipment_count=1)
        reader = SimpleNamespace(inventory_info=info, items=[item])
        self.assertTrue(probe["equipped"](reader, 3, 52543))
        info.flags = 0
        self.assertFalse(probe["equipped"](reader, 3, 52543))
        info.flags = 2
        item.flags = 2
        self.assertFalse(probe["equipped"](reader, 3, 52543))

    def test_only_the_exact_native_training_dummy_can_authorize_the_probe(self):
        target = dict(kind="npc", instance_id=4, runtime_id=22, config_id=16027)
        npc = NPC(
            instance_id=4,
            runtime_id=22,
            config_id=16027,
            flags=NPCFlags.CONFIG,
            name=b"Training dummy",
        )
        reader = SimpleNamespace(
            spatial=SimpleNamespace(player=object()), info=SimpleNamespace(npc_count=1), npcs=[npc]
        )
        helper = probe["selected_dummy"]
        with patch.dict(
            helper.__globals__,
            {
                "spatial_actor_record": Mock(
                    return_value={"target": {"value": target, "reason": None}}
                )
            },
        ):
            self.assertEqual(helper(reader), target)
            npc.config_id = 22891
            self.assertIsNone(helper(reader))
            npc.config_id = 16027
            npc.instance_id = 5
            self.assertIsNone(helper(reader))

    def test_bound_role_rejects_a_label_from_a_different_process_lifetime(self):
        label = SimpleNamespace(
            flags=15,
            sample_sequence=10,
            pid=1,
            process_creation_time=3,
            name=b"Weapon Special Attack",
            item_id=-1,
        )
        reader = SimpleNamespace(
            info=SimpleNamespace(sequence=10, pid=1, process_creation_time=2),
            read_action_label=Mock(return_value=label),
        )
        with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
            probe["role_descriptor"](reader, ability_id=28430, name="Weapon Special Attack")


if __name__ == "__main__":
    unittest.main()
