"""D3 scenario and replay tests use synthetic telemetry and never send input."""

import hashlib
import io
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from rs3_reader.bot.intents import ActionLane, ActivateAbility, ConsumeSlot, Interact, Priority
from rs3_reader.bot.replay import replay
from rs3_reader.bot.survival import SurvivalManager, load_profile, validate_profile
from rs3_reader.bot.world_state import Observation, WorldState

MS = 1_000_000
PROFILE = Path(__file__).resolve().parents[2] / "profiles/survival.json"


def policy():
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def item(slot=0, *, item_id=100, family=None, doses=None, heal=True, restore=False, quantity=1):
    return {
        "slot": slot,
        "item_id": item_id,
        "family_id": family,
        "doses": doses,
        "quantity": quantity,
        "empty": False,
        "tracking_token": 10 + slot,
        "identity_ambiguous": False,
        "metadata_reason": None,
        "effects": {"heals": heal, "restores_prayer": restore},
        "name": "Untrusted display name",
    }


def ability(identity, *, castable=True, cooldown=0):
    return {
        "struct_id": identity,
        "castable": castable,
        "cooldown": {"remaining_ms": cooldown, "reason": None},
    }


def effect(identity, active):
    return {"id": identity, "active": active, "reason": None, "source": "synthetic_confirmed"}


def row(*, health=8000, prayer=990, items=(), abilities=(), effects=(), sequence=1, time_ms=10000):
    return {
        "schema_version": 1,
        "source": "synthetic_fixture",
        "status": "available",
        "pid": 1,
        "process_creation_time": 2,
        "monotonic_ms": time_ms,
        "native": {"sample_sequence": sequence, "snapshot_age_ms": 0},
        "combat": {"scene_epoch": 3, "active_prayer": "melee"},
        "health": {"current": health, "max": 10000},
        "prayer": {"current": prayer, "max": 990},
        "inventory": {"status": "available", "slots": list(items)},
        "abilities": {"status": "available", "entries": list(abilities)},
        "player": {"effects": {"status": "partial", "entries": list(effects), "complete": False}},
    }


def world(**kwargs):
    return WorldState.from_telemetry(row(**kwargs))


def encounter(value, **kwargs):
    threat = {
        "event_id": 1,
        "classification_validated": True,
        "flags": 1,
        "prayer": 2,
        "check_earliest_ns": value.now_ns + 500 * MS,
        "check_latest_ns": value.now_ns + 550 * MS,
        "damage_upper_bound": 7000,
        "defensive_options": ["resonance", "devotion"],
    }
    threat.update(kwargs)
    return SimpleNamespace(
        session=value.session, observed_ns=value.observed_ns, pending_threats=[threat]
    )


class SurvivalTests(unittest.TestCase):
    def test_local_low_health_capture_has_a_repeatable_golden_intent_log(self):
        root = Path(
            os.environ.get(
                "RS3_LOCAL_CAPTURE_ROOT", str(PROFILE.parents[1] / "work/prayer_research")
            )
        )
        path = root / "p2-encounter-manual-2026-11-11-01.jsonl"
        if not path.exists():
            self.skipTest("Raw gameplay captures intentionally stay local")
        digest, selected = hashlib.sha256(), []
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line)
                if len(selected) >= 24:
                    continue
                record = json.loads(line)
                current, maximum = record.get("health", {}).get("current"), record.get(
                    "health", {}
                ).get("max")
                current = current.get("value") if isinstance(current, dict) else current
                if (
                    isinstance(current, (int, float))
                    and isinstance(maximum, (int, float))
                    and maximum > 0
                    and 0 < current <= maximum * 0.4
                ):
                    selected.append(record)
        self.assertEqual(
            digest.hexdigest(), "2571e1db5280fc4233945b35a71f77e1610188849fe288aa8c6955f8a2b06618"
        )
        self.assertEqual(len(selected), 24)
        capture = "".join(json.dumps(record) + "\n" for record in selected)
        output = io.StringIO()
        replay(io.StringIO(capture), output, self.manager())
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(any(record["intents"] for record in rows))
        self.assertTrue(all(not record["input_sent"] for record in rows))
        self.assertEqual(
            hashlib.sha256(output.getvalue().encode()).hexdigest(),
            "8cc9273431af467afe4f8d0f828bb2b7bc279913d492cbe02ce555f5d4d28911",
        )

    def manager(self):
        return SurvivalManager(policy())

    def test_low_health_food_uses_current_slot_id_and_token(self):
        value = world(health=5000, items=[item(7)])
        intents = self.manager().step(value)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].action, ConsumeSlot(7, 100, "eat", 17))
        self.assertEqual(intents[0].priority, Priority.SURVIVAL)
        self.assertTrue(all(evidence.observation.source for evidence in intents[0].evidence))
        self.assertEqual(intents[0].observed_ns, value.observed_ns)

    def test_no_food_empty_inventory_and_unknown_health_do_not_default_to_zero(self):
        for options in (
            {"health": 5000},
            {"health": None, "items": [item()]},
            {"health": 8000, "items": []},
        ):
            self.assertEqual(self.manager().step(world(**options)), ())

    def test_critical_health_can_offer_food_and_brew_in_the_same_tick(self):
        intents = self.manager().step(
            world(health=2000, items=[item(), item(1, item_id=6685, family=19, doses=4)])
        )
        self.assertEqual([intent.lane for intent in intents], [ActionLane.FOOD, ActionLane.POTION])
        self.assertEqual(len({intent.issued_ns for intent in intents}), 1)

    def test_dose_changes_and_moves_follow_family_without_using_names(self):
        manager = self.manager()
        first = manager.step(world(health=3000, items=[item(1, item_id=23351, family=19, doses=6)]))
        second = manager.step(
            world(
                health=3000,
                sequence=2,
                time_ms=10010,
                items=[item(12, item_id=23353, family=19, doses=5)],
            )
        )
        self.assertEqual(first[0].action.slot, 1)
        self.assertEqual(second[0].action, ConsumeSlot(12, 23353, "drink", 22))
        misleading = item(family=999, doses=6)
        misleading["name"] = "Saradomin brew (6)"
        self.assertEqual(self.manager().step(world(health=3000, items=[misleading])), ())

    def test_ambiguous_missing_metadata_empty_dose_or_missing_effect_is_not_food(self):
        for patch in (
            {"identity_ambiguous": True},
            {"metadata_reason": "unmapped"},
            {"quantity": 0},
            {"effects": {}},
            {"doses": 0, "family_id": 19},
        ):
            candidate = item()
            candidate.update(patch)
            self.assertEqual(self.manager().step(world(health=5000, items=[candidate])), ())

    def test_low_prayer_chooses_restore_family_and_potion_lane(self):
        intents = self.manager().step(
            world(
                prayer=100,
                items=[item(4, item_id=3024, family=23, doses=4, heal=False, restore=True)],
            )
        )
        self.assertEqual(intents[0].action, ConsumeSlot(4, 3024, "drink", 14))
        self.assertEqual(intents[0].lane, ActionLane.POTION)

    def test_potion_priority_is_configured_and_never_two_potions_per_frame(self):
        inventory = [
            item(0, family=19, doses=4),
            item(1, family=23, doses=4, heal=False, restore=True),
        ]
        intents = self.manager().step(world(health=3500, prayer=100, items=inventory))
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].action.slot, 1)
        intents = self.manager().step(world(health=2000, prayer=100, items=inventory))
        self.assertEqual(intents[0].action.slot, 0)

    def test_predicted_heavy_hit_requires_calibrated_eligibility_and_castability(self):
        value = world(
            abilities=[ability(14713)],
            effects=[effect("resonance", False), effect("disruption_shield", False)],
        )
        manager = self.manager()
        intents = manager.step(value, encounter(value))
        self.assertEqual(intents[0].action, ActivateAbility(14713))
        for changes in (
            {"flags": 0},
            {"damage_upper_bound": None},
            {"defensive_options": []},
            {"check_earliest_ns": value.now_ns - 1},
        ):
            self.assertEqual(self.manager().step(value, encounter(value, **changes)), ())
        for castable in (None, False):
            unknown = world(
                abilities=[ability(14713, castable=castable)],
                effects=[effect("resonance", False), effect("disruption_shield", False)],
            )
            self.assertEqual(self.manager().step(unknown, encounter(unknown)), ())

    def test_foreign_or_stale_encounter_does_not_time_a_defensive(self):
        value = world(
            abilities=[ability(14713)],
            effects=[effect("resonance", False), effect("disruption_shield", False)],
        )
        for attribute, replacement in (("session", None), ("observed_ns", value.now_ns - 200 * MS)):
            incoming = encounter(value)
            setattr(incoming, attribute, replacement)
            self.assertEqual(self.manager().step(value, incoming), ())

    def test_unknown_or_active_one_hit_blocker_does_not_double_stack_resonance(self):
        for active in (None, True):
            value = world(
                abilities=[ability(14713)],
                effects=[effect("resonance", False), effect("disruption_shield", active)],
            )
            self.assertEqual(self.manager().step(value, encounter(value)), ())

    def test_devotion_requires_the_matching_protection_prayer(self):
        value = world(abilities=[ability(25028)], effects=[effect("devotion", False)])
        self.assertEqual(
            self.manager().step(value, encounter(value))[0].action, ActivateAbility(25028)
        )
        value = replace(
            value,
            player=replace(value.player, active_prayer=Observation("soul_split", 0, "fixture")),
        )
        self.assertEqual(self.manager().step(value, encounter(value)), ())

    def test_stun_uses_freedom_but_an_unmapped_stun_does_not(self):
        value = world(abilities=[ability(14711)], effects=[effect("stun", True)])
        self.assertEqual(self.manager().step(value)[0].action, ActivateAbility(14711))
        for active in (None, False):
            value = world(abilities=[ability(14711)], effects=[effect("stun", active)])
            self.assertEqual(self.manager().step(value), ())

    def test_virus_requires_a_validated_unique_cleanse_target(self):
        manager = self.manager()
        value = world(effects=[effect("anima_virus", True)])
        self.assertEqual(manager.step(value), ())
        self.assertIn("virus_cleanse_unmapped_or_ambiguous", manager.last_report.reasons)
        route = {
            "kind": "virus_cleanse",
            "validated": True,
            "source": "synthetic_fixture",
            "target": {"kind": "object", "instance_id": 500},
            "option": "Cleanse",
        }
        value = replace(value, hazards=Observation((route,), 0, "fixture", complete=False))
        self.assertIsInstance(self.manager().step(value)[0].action, Interact)
        value = replace(value, hazards=Observation((route, route), 0, "fixture", complete=True))
        self.assertEqual(self.manager().step(value), ())

    def test_emergency_exit_needs_a_calibrated_route_and_ready_ability(self):
        value = world(health=1000, abilities=[ability(777)])
        manager = self.manager()
        self.assertEqual(manager.step(value), ())
        self.assertIn("emergency_exit_unmapped_or_unavailable", manager.last_report.reasons)
        configuration = policy()
        configuration["exit"] = {
            "ability_id": 777,
            "validated": True,
            "source": "synthetic exit calibration",
        }
        intent = SurvivalManager(configuration).step(value)[0]
        self.assertEqual(intent.action, ActivateAbility(777))

    def test_unknown_timer_or_stale_health_does_not_authorize_input(self):
        value = world(abilities=[ability(14711, cooldown=None)], effects=[effect("stun", True)])
        self.assertEqual(self.manager().step(value), ())
        stale = WorldState.from_telemetry(row(health=2000, items=[item()]), now_ns=10300 * MS)
        self.assertEqual(self.manager().step(stale), ())

    def test_metadata_refresh_does_not_acknowledge_food_consumption(self):
        manager = self.manager()
        value = world(health=5000, items=[item()])
        manager.note_sent(manager.step(value)[0].intent_id, value.now_ns)
        changed = item()
        changed["name"] = "New display text"
        self.assertEqual(
            manager.step(world(health=5000, items=[changed], sequence=2, time_ms=10010)), ()
        )
        self.assertEqual(manager.last_report.pending_lanes, ("food",))

    def test_partial_inventory_omission_does_not_acknowledge_consumption(self):
        manager = self.manager()
        value = world(health=5000, items=[item()])
        manager.note_sent(manager.step(value)[0].intent_id, value.now_ns)
        newer = world(health=5000, sequence=2, time_ms=10010)
        newer = replace(newer, inventory=Observation((), 0, "fixture", complete=False))
        self.assertEqual(manager.step(newer), ())
        self.assertEqual(manager.last_report.pending_lanes, ("food",))

    def test_ability_cooldown_acknowledges_only_a_newer_sample(self):
        manager = self.manager()
        value = world(abilities=[ability(14711)], effects=[effect("stun", True)])
        manager.note_sent(manager.step(value)[0].intent_id, value.now_ns)
        self.assertEqual(manager.step(value), ())
        newer = world(
            abilities=[ability(14711, cooldown=1000)],
            effects=[effect("stun", False)],
            sequence=2,
            time_ms=10010,
        )
        self.assertEqual(manager.step(newer), ())
        self.assertEqual(manager.last_report.pending_lanes, ())

    def test_unknown_cleanse_identity_cannot_become_an_interaction(self):
        value = world(effects=[effect("anima_virus", True)])
        route = {
            "kind": "virus_cleanse",
            "validated": True,
            "source": "fixture",
            "target": {"kind": "object", "config_id": 999},
            "option": "Cleanse",
        }
        value = replace(value, hazards=Observation((route,), 0, "fixture", complete=True))
        manager = self.manager()
        self.assertEqual(manager.step(value), ())
        self.assertIn("virus_cleanse_target_identity_unknown", manager.last_report.reasons)

    def test_proposal_is_not_input_pending_waits_for_a_new_inventory_observation(self):
        manager = self.manager()
        value = world(health=5000, items=[item()])
        first = manager.step(value)[0]
        self.assertEqual(manager.step(value), (first,))
        manager.note_sent(first.intent_id, value.now_ns)
        self.assertEqual(manager.step(value), ())
        unchanged = world(health=5000, items=[item()], sequence=2, time_ms=10010)
        self.assertEqual(manager.step(unchanged), ())
        changed = world(health=5000, items=[item(item_id=101)], sequence=3, time_ms=10020)
        next_intent = manager.step(changed)[0]
        self.assertEqual(next_intent.action.expected_item_id, 101)
        self.assertIn("inventory_precondition_changed_after_input", manager.last_report.reasons)

    def test_ack_timeout_latches_until_explicit_reset(self):
        manager = self.manager()
        value = world(health=5000, items=[item()])
        intent = manager.step(value)[0]
        manager.note_sent(intent.intent_id, value.now_ns)
        self.assertEqual(
            manager.step(world(health=5000, items=[item()], sequence=2, time_ms=12000)), ()
        )
        self.assertEqual(manager.last_report.fault, "acknowledgement_timeout")
        manager.reset()
        self.assertTrue(manager.step(world(health=5000, items=[item()], sequence=3, time_ms=12010)))

    def test_scene_change_does_not_reuse_old_pending_actions(self):
        manager = self.manager()
        value = world(health=5000, items=[item()])
        manager.note_sent(manager.step(value)[0].intent_id, value.now_ns)
        record = row(health=5000, items=[item()], sequence=2, time_ms=10010)
        record["combat"]["scene_epoch"] = 4
        self.assertTrue(manager.step(WorldState.from_telemetry(record)))
        self.assertEqual(manager.last_report.pending_lanes, ())

    def test_profile_guide_leads_and_family_zero_stay_explicit(self):
        self.assertEqual(load_profile()["schema_version"], 1)
        configuration = policy()
        self.assertTrue(all(lead["validated"] is False for lead in configuration["guide_leads"]))
        configuration["item_roles"][1]["family_ids"] = [0]
        self.assertTrue(
            SurvivalManager(configuration).step(world(health=3000, items=[item(family=0, doses=1)]))
        )
        configuration["thresholds"]["critical_health_fraction"] = 0.9
        with self.assertRaises(ValueError):
            validate_profile(configuration)

    def test_replay_is_deterministic_and_proposes_multiple_lanes_without_input(self):
        capture = json.dumps(row(health=2000, items=[item(), item(1, family=19, doses=4)])) + "\n"
        logs = []
        for _ in range(2):
            output = io.StringIO()
            replay(io.StringIO(capture), output, self.manager())
            logs.append(output.getvalue())
        self.assertEqual(logs[0], logs[1])
        result = json.loads(logs[0])
        self.assertFalse(result["input_sent"])
        self.assertEqual([intent["lane"] for intent in result["intents"]], ["food", "potion"])


if __name__ == "__main__":
    unittest.main()
