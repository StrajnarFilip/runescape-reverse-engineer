"""Dismember's renamed timer must not silently pick a recast or mix clock domains."""

import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

WORK_DIRECTORY = Path(__file__).resolve().parents[2] / "work"
with patch.object(sys, "path", [str(WORK_DIRECTORY), *sys.path]):
    bindings = runpy.run_path(str(WORK_DIRECTORY / "build_ability_bindings.py"))


class AbilityTimerAliasTests(unittest.TestCase):
    def test_dismember_resolves_the_base_pair_and_not_either_recast(self):
        variables = {
            "combatv2_cooldown_melee_dismember_start_client": 8380,
            "combatv2_cooldown_melee_dismember_end_client": 8381,
            "combatv2_cooldown_melee_dismember_recast_1_start_client": 8382,
            "combatv2_cooldown_melee_dismember_recast_1_end_client": 8383,
            "combatv2_cooldown_melee_dismember_recast_2_start_client": 8384,
            "combatv2_cooldown_melee_dismember_recast_2_end_client": 8385,
        }
        pair = bindings["client_timer_pair"]("combatv2_cooldown_strength_dismember", variables)
        self.assertEqual(pair, (8380, 8381, "client_symbol_pair_namespace_alias"))
        variables.pop("combatv2_cooldown_melee_dismember_end_client")
        self.assertIsNone(
            bindings["client_timer_pair"]("combatv2_cooldown_strength_dismember", variables)[2]
        )

    def test_an_exact_or_partial_original_pair_is_never_replaced_by_an_alias(self):
        stem = "combatv2_cooldown_strength_dismember"
        variables = {
            stem + "_start_client": 10,
            stem + "_end_client": 11,
            "combatv2_cooldown_melee_dismember_start_client": 8380,
            "combatv2_cooldown_melee_dismember_end_client": 8381,
        }
        self.assertEqual(
            bindings["client_timer_pair"](stem, variables), (10, 11, "client_symbol_pair")
        )
        variables.pop(stem + "_end_client")
        self.assertEqual(bindings["client_timer_pair"](stem, variables), (10, None, None))


if __name__ == "__main__":
    unittest.main()
