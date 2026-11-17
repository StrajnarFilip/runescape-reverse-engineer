"""Resolve prayer potions by C metadata and verify native dose transitions.

No game sampling or event drain occurs here. A bound seed ID can stay at six
doses while the carried item changes to five; their C family IDs establish
the relationship. Accepted input, consumed dose and resource change are logged
separately. This module never attributes an unrelated heal or perk proc.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..action_bar_keys import MAXIMUM_BINDING_AGE_NS, SlotBinding
from ..native import (
    PRAYER_POINTS_MASK,
    Container,
    EffectFlags,
    InventoryFlags,
    ItemChange,
    ItemFlags,
    Variable,
)

POTION_OBSERVATION_TIMEOUT_NS = 2_400_000_000
PRAYER_RESTORATION_ROLE = "Prayer restoration"
# Instant restoratives whose negative effects do not require a separate
# survival policy. The positive prayer effect must also be present in C metadata.
PRAYER_POTION_NAMES = frozenset({"Prayer flask"}) | frozenset(
    name + suffix
    for name in (
        "Spiritual prayer potion",
        "Super restore",
        "Prayer potion",
        "Super prayer",
        "Extreme prayer",
    )
    for suffix in ("", " flask")
)


@dataclass(frozen=True)
class CarriedDose:
    """One owned observation of a possible dose; a key need not select this exact slot."""

    slot: int
    item_id: int
    token: int
    quantity: int
    doses: int
    next_dose_item_id: int


@dataclass(frozen=True)
class BoundPrayerPotion:
    """A fresh native chord, its catalog family and all eligible carried candidates."""

    binding: SlotBinding
    family_id: int
    candidates: tuple
    session: tuple
    sample_sequence: int
    inventory_sequence: int

    def record(self):
        """Return owned JSON data without declaring which candidate the game will use."""
        return {
            "binding": asdict(self.binding),
            "family_id": self.family_id,
            "candidate_items": [asdict(item) for item in self.candidates],
            "source": "native_ui_and_c_catalog_and_inventory",
        }


def resolve_prayer_potion(reader, bindings, *, preferred_family=None):
    """Resolve the current potion across doses and moves; return None for unknown evidence.

    Runtime slots require C metadata, unambiguous nonzero tokens and a readable
    backpack. Definitions are queried in C, including bar seed IDs no longer
    carried. This never assumes the bar's seed ID is the current inventory ID.
    """
    inventory = reader.inventory_info
    info = reader.info
    required = InventoryFlags.BACKPACK_READ | InventoryFlags.CATALOG_LOADED
    now = reader.monotonic_ns()
    if (
        inventory.flags & required != required
        or not info.pid
        or not info.process_creation_time
        or not 0 < info.monotonic_ns <= now
        or now - info.monotonic_ns > MAXIMUM_BINDING_AGE_NS
    ):
        return None
    session = info.pid, info.process_creation_time, reader.combat.scene_epoch
    options = []
    for binding in bindings.values():
        if (
            binding.source != "native_ui_label"
            or binding.item_id is None
            or binding.item_id < 0
            or (binding.pid, binding.process_creation_time, binding.sample_sequence)
            != (info.pid, info.process_creation_time, info.sequence)
            or binding.observed_ns is None
            or not 0 <= now - binding.observed_ns <= MAXIMUM_BINDING_AGE_NS
        ):
            continue
        try:
            definition = reader.describe_item(binding.item_id)
        except RuntimeError:
            continue
        try:
            base_name = re.sub(r" \([1-9][0-9]*\)$", "", definition.name.decode("utf-8", "strict"))
        except UnicodeError:
            continue
        if (
            not definition.flags & ItemFlags.METADATA
            or not definition.family_id
            or definition.doses <= 0
            or not definition.effect_flags & EffectFlags.RESTORES_PRAYER
            or base_name not in PRAYER_POTION_NAMES
        ):
            continue
        candidates = tuple(
            CarriedDose(
                item.slot,
                item.item_id,
                item.token,
                item.quantity,
                item.doses,
                item.next_dose_item_id,
            )
            for item in reader.items[: inventory.backpack_count]
            if item.container_id == Container.BACKPACK
            and item.flags & ItemFlags.METADATA
            and not item.flags & ItemFlags.AMBIGUOUS_IDENTITY
            and item.quantity > 0
            and item.token
            and item.family_id == definition.family_id
            and item.doses > 0
            and item.effect_flags & EffectFlags.RESTORES_PRAYER
        )
        if candidates:
            options.append(
                BoundPrayerPotion(
                    binding,
                    definition.family_id,
                    candidates,
                    session,
                    info.sequence,
                    inventory.change_sequence,
                )
            )
    if not options or reader.monotonic_ns() - info.monotonic_ns > MAXIMUM_BINDING_AGE_NS:
        return None
    return min(
        options,
        key=lambda option: (
            option.family_id != preferred_family,
            option.binding.display_bar,
            option.binding.slot,
        ),
    )


class PotionUseMonitor:
    """Associate one accepted potion use with later native inventory observations.

    ItemEvent.observed_at_ns is Unix wall time, not the native monotonic clock.
    Sequence cursors, session identity and newer snapshots establish ordering;
    the two clock domains are never subtracted from each other.
    """

    def __init__(self, *, timeout_ns=POTION_OBSERVATION_TIMEOUT_NS):
        self.timeout_ns = timeout_ns
        self.pending = None

    def sent(self, potion, sent_ns, prayer_tenths):
        """Record accepted input; neither consumption nor prayer restoration is assumed."""
        if self.pending is not None:
            raise RuntimeError("A potion use is still awaiting observation")
        if (
            not isinstance(potion, BoundPrayerPotion)
            or type(sent_ns) is not int
            or sent_ns < potion.binding.observed_ns
        ):
            raise ValueError("Potion use requires owned binding evidence and native dispatch time")
        self.pending = potion, sent_ns, prayer_tenths
        return dict(
            potion.record(),
            event="potion_use_sent",
            sent_native_ns=sent_ns,
            input_timestamp_basis="native_clock_before_dispatch",
            prayer_tenths_before=prayer_tenths,
            consumption_confirmed=False,
        )

    def observe(self, reader, events, lost):
        """Return one outcome when observed, invalidated or timed out; otherwise None."""
        if self.pending is None:
            return None
        potion, sent_ns, before_points = self.pending
        info = reader.info
        session = info.pid, info.process_creation_time, reader.combat.scene_epoch
        if session != potion.session:
            return self.finish("inconclusive_session_change")
        if type(lost) is not int or lost != 0:
            return self.finish("inconclusive_inventory_queue_loss", lost=lost)
        if info.sequence <= potion.sample_sequence or info.monotonic_ns < sent_ns:
            return None
        candidates = {item.token: item for item in potion.candidates}
        for event in events:
            before, after = event.before, event.after
            candidate = candidates.get(before.token)
            if (
                event.sequence <= potion.inventory_sequence
                or (event.pid, event.process_creation_time) != session[:2]
                or candidate is None
                or before.item_id != candidate.item_id
                or before.family_id != potion.family_id
                or before.doses != candidate.doses
                or not before.flags & ItemFlags.METADATA
                or event.flags & ItemFlags.AMBIGUOUS_IDENTITY
            ):
                continue
            dose_change = (
                event.kind == ItemChange.DOSES_CHANGED
                and before.doses > 1
                and after.token == before.token
                and after.family_id == before.family_id
                and after.flags & ItemFlags.METADATA
                and after.doses == before.doses - 1
                and after.item_id == before.next_dose_item_id
                and after.quantity == before.quantity
            )
            last_dose = before.doses == 1 and (
                (event.kind == ItemChange.REMOVED and after.item_id < 0)
                or (event.kind == ItemChange.CHANGED and after.item_id == before.next_dose_item_id)
                or (
                    event.kind == ItemChange.QUANTITY_CHANGED
                    and after.item_id == before.item_id
                    and after.quantity == before.quantity - 1
                )
            )
            if dose_change or last_dose:
                packed = reader.player.variable(Variable.PRAYER_PACKED)
                points = None if packed is None else packed & PRAYER_POINTS_MASK
                return self.finish(
                    "dose_transition_observed",
                    inventory_sequence=event.sequence,
                    event_timestamp_ns=event.observed_at_ns,
                    item_token=before.token,
                    slot_before=before.slot,
                    slot_after=after.slot if after.item_id >= 0 else None,
                    item_id_before=before.item_id,
                    item_id_after=after.item_id,
                    doses_before=before.doses,
                    doses_after=after.doses,
                    prayer_tenths_after=points,
                    prayer_increased_after_use=(
                        None if points is None or before_points is None else points > before_points
                    ),
                )
        if reader.monotonic_ns() - sent_ns >= self.timeout_ns:
            return self.finish("consumption_not_observed")
        return None

    def finish(self, outcome, **fields):
        """Close the observation without issuing or retrying an input."""
        potion, sent_ns, _ = self.pending
        self.pending = None
        return dict(
            event="potion_use_outcome",
            outcome=outcome,
            sent_native_ns=sent_ns,
            family_id=potion.family_id,
            **fields,
        )
