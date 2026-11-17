"""Dose-family selection and observations use owned C data without input or extra sampling."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from rs3_reader.action_bar_keys import SlotBinding
from rs3_reader.bot.consumables import PotionUseMonitor, resolve_prayer_potion
from rs3_reader.native import Item, ItemChange, ItemEvent


class NativePotionTests(unittest.TestCase):
    def setUp(self):
        self.now = 2000
        self.points = 400
        self.definition = Item(
            item_id=1006,
            flags=1,
            family_id=177,
            doses=6,
            next_dose_item_id=1005,
            effect_flags=2,
            name=b"Spiritual prayer potion (6)",
        )
        self.item = Item(
            token=88,
            container_id=93,
            slot=3,
            item_id=1005,
            quantity=1,
            flags=1,
            family_id=177,
            doses=5,
            next_dose_item_id=1004,
            effect_flags=2,
        )
        self.reader = SimpleNamespace(
            info=SimpleNamespace(pid=50, process_creation_time=70, sequence=1, monotonic_ns=1000),
            combat=SimpleNamespace(scene_epoch=4),
            inventory_info=SimpleNamespace(flags=9, backpack_count=1, change_sequence=5),
            items=[self.item],
            monotonic_ns=lambda: self.now,
            describe_item=Mock(return_value=self.definition),
            player=SimpleNamespace(variable=lambda var: self.points),
            sample=Mock(),
            drain_item_events=Mock(),
        )
        self.binding = SlotBinding(
            "Spiritual prayer potion",
            "C",
            2,
            6,
            5,
            None,
            None,
            source="native_ui_label",
            virtual_key=ord("C"),
            modifiers=0,
            pid=50,
            process_creation_time=70,
            sample_sequence=1,
            observed_ns=1000,
            item_id=1006,
            sprite_id=42,
        )
        self.bindings = {self.binding.name: self.binding}

    def event(self, *, sequence=6):
        event = ItemEvent(
            sequence=sequence,
            observed_at_ns=1_800_000_000_000_000_000,
            pid=50,
            process_creation_time=70,
            kind=ItemChange.DOSES_CHANGED,
            flags=1,
        )
        event.before = Item.from_buffer_copy(bytes(self.item))
        event.after = Item.from_buffer_copy(bytes(self.item))
        event.after.item_id = 1004
        event.after.doses = 4
        event.after.next_dose_item_id = 1003
        event.after.slot = 19
        return event

    def advance(self):
        self.reader.info.sequence = 2
        self.reader.info.monotonic_ns = self.now = 3000

    def test_six_dose_bar_seed_follows_a_five_dose_carried_item_and_its_move(self):
        potion = resolve_prayer_potion(self.reader, self.bindings)
        self.assertEqual(potion.binding.item_id, 1006)
        self.assertEqual(potion.family_id, 177)
        self.assertEqual((potion.candidates[0].item_id, potion.candidates[0].doses), (1005, 5))
        self.item.slot = 19
        moved = resolve_prayer_potion(self.reader, self.bindings)
        self.assertEqual((moved.candidates[0].slot, moved.candidates[0].token), (19, 88))
        self.reader.sample.assert_not_called()
        self.reader.drain_item_events.assert_not_called()

    def test_unknown_ambiguous_missing_effect_and_other_account_data_are_ineligible(self):
        for field, value in (
            ("flags", 0),
            ("flags", 3),
            ("token", 0),
            ("quantity", 0),
            ("family_id", 0),
            ("effect_flags", 0),
            ("doses", 0),
            ("container_id", 94),
        ):
            original = getattr(self.item, field)
            setattr(self.item, field, value)
            with self.subTest(field=field, value=value):
                self.assertIsNone(resolve_prayer_potion(self.reader, self.bindings))
            setattr(self.item, field, original)
        self.reader.info.process_creation_time = 71
        self.assertIsNone(resolve_prayer_potion(self.reader, self.bindings))

    def test_positive_flag_alone_does_not_authorize_a_different_potion_policy(self):
        self.definition.name = b"Super prayer renewal (6)"
        self.assertIsNone(resolve_prayer_potion(self.reader, self.bindings))
        self.definition.name = b"Spiritual prayer potion (6)"
        self.reader.inventory_info.flags = 8
        self.assertIsNone(resolve_prayer_potion(self.reader, self.bindings))

    def test_inventory_event_resolves_exact_item_and_slot_without_mixing_clocks(self):
        monitor = PotionUseMonitor()
        potion = resolve_prayer_potion(self.reader, self.bindings)
        sent = monitor.sent(potion, 2100, self.points)
        self.assertFalse(sent["consumption_confirmed"])
        self.assertEqual(sent["candidate_items"][0]["token"], 88)
        with self.assertRaisesRegex(RuntimeError, "awaiting observation"):
            monitor.sent(potion, 2200, self.points)
        self.advance()
        self.points = 3600
        outcome = monitor.observe(self.reader, [self.event()], 0)
        self.assertEqual(outcome["outcome"], "dose_transition_observed")
        self.assertEqual((outcome["slot_before"], outcome["slot_after"]), (3, 19))
        self.assertEqual((outcome["doses_before"], outcome["doses_after"]), (5, 4))
        self.assertEqual(outcome["event_timestamp_ns"], 1_800_000_000_000_000_000)
        self.assertTrue(outcome["prayer_increased_after_use"])
        self.assertIsNone(monitor.pending)

    def test_duplicate_potions_remain_candidates_until_the_native_event_identifies_one(self):
        second = Item.from_buffer_copy(bytes(self.item))
        second.slot, second.token = 7, 99
        self.reader.items.append(second)
        self.reader.inventory_info.backpack_count = 2
        potion = resolve_prayer_potion(self.reader, self.bindings)
        self.assertEqual(len(potion.candidates), 2)
        monitor = PotionUseMonitor()
        monitor.sent(potion, 2100, self.points)
        event = self.event()
        event.before.token = event.after.token = 99
        event.before.slot = 7
        self.advance()
        outcome = monitor.observe(self.reader, [event], 0)
        self.assertEqual((outcome["item_token"], outcome["slot_before"]), (99, 7))

    def test_old_wrong_process_and_replacement_events_do_not_confirm_consumption(self):
        potion = resolve_prayer_potion(self.reader, self.bindings)
        monitor = PotionUseMonitor(timeout_ns=2000)
        monitor.sent(potion, 2100, self.points)
        self.advance()
        old = self.event(sequence=5)
        wrong = self.event()
        wrong.pid = 51
        replaced = self.event()
        replaced.kind = ItemChange.CHANGED
        replaced.after.item_id = 9999
        self.assertIsNone(monitor.observe(self.reader, [old, wrong, replaced], 0))
        self.now = 5000
        self.assertEqual(monitor.observe(self.reader, [], 0)["outcome"], "consumption_not_observed")

    def test_loss_and_session_changes_preserve_inconclusive_outcomes(self):
        potion = resolve_prayer_potion(self.reader, self.bindings)
        monitor = PotionUseMonitor()
        monitor.sent(potion, 2100, self.points)
        self.advance()
        self.assertEqual(
            monitor.observe(self.reader, [self.event()], 1)["outcome"],
            "inconclusive_inventory_queue_loss",
        )
        monitor.sent(potion, 3100, self.points)
        self.reader.combat.scene_epoch = 5
        self.assertEqual(
            monitor.observe(self.reader, [], 0)["outcome"], "inconclusive_session_change"
        )

    def test_last_dose_removal_and_known_empty_container_are_observed(self):
        for kind, next_item_id in ((ItemChange.REMOVED, -1), (ItemChange.CHANGED, 229)):
            with self.subTest(kind=kind):
                self.reader.info.sequence = 1
                self.reader.info.monotonic_ns = self.now = 2000
                self.item.doses = 1
                self.item.next_dose_item_id = next_item_id
                potion = resolve_prayer_potion(self.reader, self.bindings)
                monitor = PotionUseMonitor()
                monitor.sent(potion, 2100, self.points)
                event = self.event()
                event.kind = kind
                event.after = Item(item_id=next_item_id, doses=-1)
                self.advance()
                outcome = monitor.observe(self.reader, [event], 0)
                self.assertEqual(outcome["outcome"], "dose_transition_observed")
                self.assertEqual(outcome["doses_before"], 1)
                self.assertEqual(outcome["item_id_after"], next_item_id)


if __name__ == "__main__":
    unittest.main()
