"""Shared native evidence and ordered P1 combat integration.

No memory acquisition occurs here. The original bounded pilot and the combined
practice controller reuse these exact role, readiness and outcome checks.
"""

from __future__ import annotations

from .rotation import OrderedP1Rotation
from .world_state import Observation, WorldState

MAXIMUM_AGE_NS = 150_000_000
OPTIONAL_ITEM_TIMEOUT_NS = 2_500_000_000
EVENT_STREAMS = ("npc_actions", "npc_hits", "player_hits", "inventory_changes")
ROTATION_FIELDS = (
    "schema_version",
    "source",
    "status",
    "pid",
    "process_creation_time",
    "executable_sha256",
    "timestamp_ms",
    "observed_ns",
    "monotonic_ms",
    "native",
    "combat",
    "health",
    "prayer",
    "adrenaline_percent",
    "gcd",
    "position",
    "player",
    "freshness",
    "encounter",
    "telos",
    "game_tick",
    "inventory",
    "equipment",
    "npc_action_events",
    "npc_action_events_lost",
    "player_hit_splats",
    "hit_splats",
    "inventory_changes",
)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


class P1ActionEvidence:
    """Resolve P1 roles and retain action-specific, later native acknowledgements."""

    @staticmethod
    def _text(value):
        return value.decode("utf-8", "strict") if isinstance(value, bytes) else value

    def _matches(self, action, label):
        name = self._text(label.name)
        if action["kind"] == "equip":
            return label.item_id == action["item_id"]
        if action.get("role") == "adrenaline_renewal":
            return name == "Adrenaline renewal"
        if action.get("role") == "vulnerability_bomb":
            return label.item_id == action["item_id"] and name == "Vulnerability bomb"
        return name == action.get("name") and label.item_id == -1

    def discover_bindings(self):
        """Find unique current action roles; no input and no raw memory reads in Python."""
        actions = [
            action
            for step in self.template.document["steps"]
            for action in step["actions"]
            if action["kind"] != "target_cycle"
        ]
        found = {action["id"]: [] for action in actions}
        for bar in range(1, 6):
            for slot in range(1, 15):
                try:
                    label = self.read_label(bar, slot)
                except RuntimeError:
                    continue
                if label.flags == 15 and label.name in (b"Deflect Melee", b"Soul Split"):
                    role = "melee" if label.name == b"Deflect Melee" else "soul_split"
                    if role in self.prayer_locations:
                        raise ValueError("p1_prayer_binding_ambiguous:" + role)
                    self.prayer_locations[role] = bar, slot
                for action in actions:
                    if label.flags == 15 and self._matches(action, label):
                        found[action["id"]].append((bar, slot, self._text(label.key_label)))
        missing = [name for name, matches in found.items() if len(matches) != 1]
        if missing:
            raise ValueError("p1_binding_missing_or_ambiguous:" + ",".join(missing))
        self.locations = {name: matches[0] for name, matches in found.items()}
        return dict(self.locations)

    def _fresh(self, observation):
        return observation.is_fresh(150) and observation.reason is None and bool(observation.source)

    def _ability(self, action, world):
        if not self._fresh(world.abilities):
            return None
        entries = [
            entry
            for entry in world.abilities.value
            if entry.get("struct_id") == action.get("ability_id")
        ]
        return entries[0] if len(entries) == 1 else None

    def _item(self, action, world):
        if not self._fresh(world.inventory):
            return None
        matches = []
        for item in world.inventory.value:
            expected = action.get("item_id")
            match = (
                item.get("item_id") == expected
                if expected is not None
                else (
                    isinstance(item.get("name"), str)
                    and item["name"].startswith("Adrenaline renewal potion (")
                )
            )
            if (
                match
                and _integer(item.get("quantity"), 1)
                and _integer(item.get("tracking_token"), 1)
                and item.get("identity_ambiguous") is False
            ):
                matches.append(item)
        return matches[0] if len(matches) == 1 else None

    def _equipped(self, world, slot, item_id):
        return self._fresh(world.equipment) and any(
            item.get("slot") == slot
            and item.get("item_id") == item_id
            and item.get("quantity") == 1
            and item.get("identity_ambiguous") is False
            for item in world.equipment.value.get("slots", ())
        )

    def _ready(self, action, world):
        kind = action["kind"]
        if kind == "target_cycle":
            return True
        if kind in ("item", "equip"):
            return self._item(action, world) is not None
        entry = self._ability(action, world)
        if (
            entry is None
            or entry.get("in_ability_book") is not True
            or entry.get("castable") is False
            or entry.get("equipment_match") != "allowed"
        ):
            return False
        cooldown = entry.get("cooldown") or {}
        remaining, reason = cooldown.get("remaining_ms"), cooldown.get("reason")
        # A missing first-use timer permits an experimental attempt, not a ready claim.
        if kind == "eof_special":
            # EoF has the ordinary GCD, not its own cooldown. Its generic
            # ability timer cannot establish the stored weapon's cast or delay.
            pass
        elif reason == "not_set":
            if remaining is not None:
                return False
        elif kind == "weapon_special" and reason == "timer_mapping_pending":
            pass
        elif reason is not None or not _integer(remaining) or remaining != 0:
            return False
        if not self._fresh(world.player.adrenaline_percent):
            return False
        cost = (entry.get("requirements") or {}).get("adrenaline_raw")
        if not _integer(cost) or world.player.adrenaline_percent.value < cost / 10:
            return False
        if action.get("required_weapon_id") and not self._equipped(
            world, 3, action["required_weapon_id"]
        ):
            return False
        if action.get("required_amulet_id") and not self._equipped(
            world, 2, action["required_amulet_id"]
        ):
            return False
        gcd = world.player.gcd
        return self._fresh(gcd) and (
            (gcd.value.get("reason") is None and gcd.value.get("remaining_ms") == 0)
            or (self.engine.index < 2 and gcd.value.get("reason") == "not_set")
        )

    def prepare(self, action, world):
        """Re-read the C label and exact slot identity immediately before D6 sends."""
        from .actuator_sendinput import NativeKeyAction

        descriptor = NativeKeyAction()
        kind = action["kind"]
        if kind == "target_cycle":
            target = self.engine._encounter_npc(world)
            if target is None:
                raise ValueError("p1_cycle_target_disappeared")
            descriptor.kind, descriptor.virtual_key = 3, ord("A")
            descriptor.expected_instance_id = target.instance_id
            descriptor.expected_config_id = target.config_id
            return descriptor, None
        bar, slot, _ = self.locations[action["id"]]
        label = self.read_label(bar, slot)
        if not self._matches(action, label) or not self._ready(action, world):
            raise ValueError("p1_role_or_readiness_changed:" + action["id"])
        descriptor.virtual_key, descriptor.modifiers = label.virtual_key, label.modifiers
        if kind in ("item", "equip"):
            item = self._item(action, world)
            descriptor.kind, descriptor.slot = 2, item["slot"]
            descriptor.expected_item_id = item["item_id"]
            descriptor.expected_token = item["tracking_token"]
        else:
            descriptor.kind, descriptor.ability_id = 1, action["ability_id"]
        return descriptor, label

    def _acknowledged(self, action, before, world):
        kind = action["kind"]
        if kind in ("weapon_special", "eof_special"):
            # Weapon-specific debuff/buff candidates are now acquired in C.
            # They still need labelled validation before confirming a cast.
            # In particular, an EoF-specific cooldown does not exist, and a
            # generic GCD or special-button timer is never its acknowledgement.
            return False
        if kind == "target_cycle":
            return self._fresh(world.player.target) and (
                world.player.target.value == self.engine._encounter_npc(world)
            )
        if kind == "equip":
            return self._equipped(
                world, {"main_hand": 3, "neck": 2}[action["equipment_slot"]], action["item_id"]
            )
        if kind == "item":
            previous = self._item(action, before)
            if previous is None or not self._fresh(world.inventory):
                return False
            current = [
                item for item in world.inventory.value if item.get("slot") == previous["slot"]
            ]
            if len(current) != 1 or current[0].get("identity_ambiguous") is not False:
                return False
            current = current[0]
            if action["id"] == "vulnerability":
                return (
                    current.get("item_id") == previous["item_id"]
                    and current.get("tracking_token") == previous["tracking_token"]
                    and current.get("quantity") == previous["quantity"] - 1
                )
            # Explicit decoded potion names/IDs are evidence, not inferred family metadata.
            prefix = "Adrenaline renewal potion ("
            old_name, new_name = previous.get("name", ""), current.get("name", "")
            return (
                isinstance(new_name, str)
                and new_name.startswith(prefix)
                and old_name.endswith(")")
                and new_name.endswith(")")
                and old_name[len(prefix) : -1].isdigit()
                and new_name[len(prefix) : -1].isdigit()
                and int(old_name[len(prefix) : -1]) - 1 == int(new_name[len(prefix) : -1])
                and current.get("item_id") != previous["item_id"]
                and current.get("quantity") == previous["quantity"]
            )
        entry, old = self._ability(action, world), self._ability(action, before)
        if entry is None or old is None:
            return False
        cooldown, old_timer = entry.get("cooldown") or {}, old.get("cooldown") or {}
        return (
            cooldown.get("reason") is None
            and _integer(cooldown.get("remaining_ms"), 1)
            and cooldown.get("start_tick") != old_timer.get("start_tick")
        )


class IntegratedP1Rotation(P1ActionEvidence):
    """Ordered opening beside the mechanic controller, using its single input owner.

    ``observe`` runs before urgent responses and preserves the native wake edge.
    ``dispatch`` runs after protection/defensives and may be deferred without
    advancing a step. The supplied D6 writer prepares the entire ordered batch
    before any input. Completing the sequence does not establish a P1 clear.
    """

    def __init__(self, writer, read_label, clock):
        self.writer, self.read_label, self.clock = writer, read_label, clock
        self.template = OrderedP1Rotation()
        self.engine = None
        self.locations, self.prayer_locations = {}, {}
        self.pending = None
        self.optional_items = {}
        self.stopped = False
        self.reason = "waiting_for_native_instance_and_preparation"
        self._reported = None
        self._ability_ids = {
            action["ability_id"]
            for step in self.template.document["steps"]
            for action in step["actions"]
            if "ability_id" in action
        }

    def world(self, record, *, now_ns):
        """Copy a P1-scoped view without freezing unrelated UI/hazard/catalog data.

        Mechanic tracking and logging retain the complete source snapshot. This
        private consumer needs the Telos forms and configured ability rows only;
        omitted WorldState channels remain unknown, not empty or ready.
        """
        scoped = {name: record[name] for name in ROTATION_FIELDS if name in record}
        for name, key, predicate in (
            ("abilities", "entries", lambda row: row.get("struct_id") in self._ability_ids),
            ("npcs", "entities", lambda row: row.get("config_id") in (22891, 22892)),
        ):
            section = record.get(name)
            if isinstance(section, dict):
                scoped[name] = dict(section, scope="telos_ordered_opening", complete=False)
                if isinstance(section.get(key), list):
                    scoped[name][key] = [row for row in section[key] if predicate(row)]
        return WorldState.from_telemetry(scoped, now_ns=now_ns)

    def _guard(self, world):
        now = self.clock()
        if (
            world.status not in ("available", "partial")
            or not world.session.complete
            or world.raw.get("executable_sha256") != self.template.document["client_sha256"]
            or not _integer(world.observed_ns)
            or not 0 <= now - world.observed_ns <= MAXIMUM_AGE_NS
        ):
            return "snapshot_unavailable_or_stale"
        if self.engine is not None and world.session != self.engine.session:
            return "encounter_session_changed"
        if not self.writer.encounter_ready(world):
            return "native_instance_unavailable"
        for name in EVENT_STREAMS:
            stream = getattr(world.events, name)
            if (
                not stream.records.known
                or stream.records.reason is not None
                or stream.lost.value != 0
                or type(stream.lost.value) is not int
                or stream.lost.reason is not None
                or not stream.lost.source
            ):
                return "event_queue_loss_or_unknown:" + name
        return None

    def _report(self):
        state = (
            self.reason,
            self.engine.current_step["id"] if self.engine and not self.engine.complete else None,
            self.stopped,
        )
        if state == self._reported:
            return []
        self._reported = state
        return [dict(event="p1_rotation_state", reason=state[0], step=state[1], stopped=state[2])]

    def finish(self, reason):
        """End only this opening; the mechanic controller retains the shared backend."""
        events = []
        if not self.stopped:
            self.stopped, self.reason = True, reason
            self.writer.close()
            for identifier, (_, _, sent_ns) in self.optional_items.items():
                events.append(
                    dict(
                        event="p1_optional_item_outcome",
                        action=identifier,
                        sent_ns=sent_ns,
                        outcome="unconfirmed_at_rotation_end",
                    )
                )
            self.optional_items.clear()
        return events + self._report()

    def observe(self, world):
        """Consume owned observations without input, before any priority decision."""
        if self.stopped:
            return []
        guard = self._guard(world)
        if guard:
            if self.engine is not None and guard != "snapshot_unavailable_or_stale":
                return self.finish(guard)
            self.reason = guard
            return self._report()
        if self.engine is None:
            actor = self.template._encounter_npc(world)
            ready = (
                actor is not None
                and actor.config_id == 22892
                and self._fresh(world.player.active_prayer)
                and world.player.active_prayer.value == "melee"
                and self._fresh(world.player.adrenaline_percent)
                and world.player.adrenaline_percent.value >= 115
                and self._equipped(world, 3, 52061)
                and self._equipped(world, 5, 52063)
                and self._fresh(world.game_tick)
                and self.writer.focus_checker(world) is True
            )
            if not ready:
                self.reason = "waiting_for_dormant_telos_and_preparation"
                return self._report()
            self.engine = self.template
            self.engine.session = world.session
        if self._fresh(world.player.health.current) and world.player.health.current.value == 0:
            return self.finish("player_hp_zero")
        if self._fresh(world.telos.phase) and world.telos.phase.value != 1:
            return self.finish("phase_one_ended")
        events = []
        for identifier, (action, before, sent_ns) in tuple(self.optional_items.items()):
            if world.sample_sequence > before.sample_sequence and world.observed_ns > sent_ns:
                outcome = (
                    "consumption_confirmed"
                    if self._acknowledged(action, before, world)
                    else (
                        "consumption_not_observed"
                        if world.observed_ns - sent_ns >= OPTIONAL_ITEM_TIMEOUT_NS
                        else None
                    )
                )
                if outcome:
                    events.append(
                        dict(
                            event="p1_optional_item_outcome",
                            action=identifier,
                            sent_ns=sent_ns,
                            outcome=outcome,
                            perk_proc=None,
                        )
                    )
                    del self.optional_items[identifier]
        if self.pending:
            batch, before, sent_ns = self.pending
            for action in batch.actions:
                if self._acknowledged(action, before, world) and self.engine.confirm(
                    action["id"], Observation(True, 0, "native_action_specific_transition"), world
                ):
                    events.append(
                        dict(
                            event="p1_action_confirmed",
                            action=action["id"],
                            sent_ns=sent_ns,
                            observed_native_ns=world.observed_ns,
                        )
                    )
            if self.engine.complete or self.engine.current_step["id"] != batch.step_id:
                self.pending = None
        if self.engine.complete:
            return events + self.finish("ordered_sequence_confirmed")
        # Even when dispatch is deferred, retain the first observed wake and tick.
        self.engine.step(world)
        if self.engine.fault:
            return events + self.finish(self.engine.fault)
        self.reason = ",".join(self.engine.reasons) or "waiting_for_dispatch"
        if self.pending:
            special = next(
                (
                    action
                    for action in self.pending[0].actions
                    if action["kind"] in ("weapon_special", "eof_special")
                ),
                None,
            )
            if special is not None:
                effect_id = (
                    "igneous_showdown_cooldown"
                    if special["kind"] == "weapon_special"
                    else "dragon_scimitar"
                )
                self.reason = "awaiting_validated_native_effect:" + effect_id
        return events + self._report()

    def dispatch(self, world, *, deferred=False):
        """Offer at most one batch after urgent prayer and mechanic responses."""
        if self.stopped or self.engine is None or self.pending or deferred:
            return []
        guard = self._guard(world)
        if guard or self.writer.focus_checker(world) is not True:
            return []
        readiness = {
            action["id"]: Observation(True, 0, "native_state_attempt_permission")
            for action in self.engine.current_step["actions"]
            if self._ready(action, world)
        }
        batch = self.engine.step(world, readiness)
        if self.engine.fault:
            return self.finish(self.engine.fault)
        if batch is None:
            self.reason = ",".join(self.engine.reasons)
            return self._report()
        try:
            accepted, sent_ns = self.writer.send(batch, world, self.prepare)
            self.engine.note_sent(batch, accepted, sent_ns)
        except Exception as error:
            return self.finish("dispatch_fault:" + str(error))
        if self.engine.fault:
            return self.finish(self.engine.fault)
        self.pending = batch, world, sent_ns
        for action in batch.actions:
            if action.get("completion") == "attempt":
                self.optional_items[action["id"]] = action, world, sent_ns
        self.reason = "awaiting_individual_native_confirmations"
        return [
            dict(
                event="p1_batch_sent",
                batch_id=batch.batch_id,
                step=batch.step_id,
                actions=list(accepted),
                sent_ns=sent_ns,
                server_same_tick_confirmed=False,
                confirmations_pending=True,
            )
        ] + self._report()
