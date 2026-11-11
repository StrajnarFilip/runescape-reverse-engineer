"""Notebook tabs of the tracker window. Each panel builds its widgets and renders records."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..hit_history import hit_event_key
from . import theme
from .widgets import (
    MISSING_VALUE,
    create_checkbox,
    create_filter_entry,
    create_note,
    create_section_label,
    create_table,
    format_clock_time,
    format_number,
    replace_text,
    sync_tree_rows,
)

BACKPACK_SLOT_COUNT = 28
INVENTORY_GRID_COLUMNS = 4
RECENT_INVENTORY_CHANGES_SHOWN = 30
EQUIPMENT_SLOT_NAMES = {
    0: "Head",
    1: "Cape",
    2: "Neck",
    3: "Main hand",
    4: "Body",
    5: "Off hand",
    7: "Legs",
    9: "Hands",
    10: "Feet",
    12: "Ring",
    13: "Ammo",
}
REQUIREMENT_LABELS = (
    ("dual_wield", "Dual wield"),
    ("two_handed", "Two-handed"),
    ("shield", "Shield"),
    ("weapon", "Weapon"),
)


def _panel_frame(parent, **options):
    return tk.Frame(parent, bg=theme.PANEL, **options)


def _status_label(parent, text):
    label = tk.Label(
        parent, text=text, bg=theme.PANEL, fg=theme.MUTED_TEXT, font=theme.BODY_FONT, anchor="w"
    )
    label.pack(fill="x", pady=(0, 8))
    return label


def _format_seconds(milliseconds, decimals):
    return f"{milliseconds / 1000:.{decimals}f} s"


class NPCPanel:
    """Loaded NPCs and the recently observed hit marks."""

    def __init__(self, parent, has_imported_history):
        self.name_filter = tk.StringVar()
        self.only_known_health = tk.BooleanVar(value=True)

        controls = _panel_frame(parent)
        controls.pack(fill="x", pady=(0, 10))
        create_filter_entry(controls, "Filter name or ID:", self.name_filter, 22)
        create_checkbox(
            controls, "Only NPCs with known HP", self.only_known_health, font=theme.SMALL_FONT
        )
        self.status = tk.Label(
            parent,
            text="Waiting for NPCs …",
            bg=theme.PANEL,
            fg=theme.MUTED_TEXT,
            font=theme.BODY_FONT,
            anchor="w",
        )
        self.status.pack(fill="x", pady=(0, 8))

        tables = _panel_frame(parent)
        tables.pack(fill="both", expand=True)
        tables.grid_columnconfigure(0, weight=3)
        tables.grid_columnconfigure(1, weight=2)
        tables.grid_rowconfigure(1, weight=1)
        table_headings = ((0, "NPCS IN THE LOADED SCENE"), (1, "RECENTLY OBSERVED HIT SPLATS"))
        for grid_column, heading in table_headings:
            tk.Label(
                tables, text=heading, bg=theme.PANEL, fg=theme.MUTED_TEXT, font=theme.SECTION_FONT
            ).grid(row=0, column=grid_column, sticky="w", pady=(0, 7), padx=(0, 12))
        self.npc_tree = create_table(
            tables,
            0,
            [
                ("name", "Name", 135),
                ("config", "Type ID", 65),
                ("runtime", "Runtime ID", 75),
                ("health", "HP / max", 105),
                ("pct", "%", 55),
                ("sequence", "Action seq", 85),
            ],
        )
        self.npc_tree.tag_configure("wounded", foreground=theme.GOLD)
        self.npc_tree.tag_configure("dead", foreground=theme.RED)
        self.hit_tree = create_table(
            tables,
            1,
            [
                ("time", "Observed", 100),
                ("npc", "Recipient", 140),
                ("amount", "Amount", 70),
                ("type", "Type ID", 65),
            ],
        )
        self.hit_tree.tag_configure("hit", foreground=theme.GOLD)
        history_note = (
            "History includes an earlier local capture and new live observations."
            if has_imported_history
            else "History fills with newly observed hits. The table shows observation times."
        )
        tk.Label(
            parent,
            text=history_note,
            bg=theme.PANEL,
            fg=theme.MUTED_TEXT,
            font=theme.SMALL_FONT,
            anchor="w",
        ).pack(fill="x", pady=(8, 0))

    def show_unavailable(self, message):
        sync_tree_rows(self.npc_tree, [])
        self.status.config(text=message, fg=theme.RED)

    def _visible_entities(self, entities):
        needle = self.name_filter.get().strip().casefold()
        only_known_health = self.only_known_health.get()

        def matches(entity):
            if only_known_health and entity["health"]["max"] is None:
                return False
            name = (entity["name"] or "").casefold()
            searchable = f"{name} {entity['config_id']} {entity['runtime_id']}"
            return not needle or needle in searchable

        visible = [entity for entity in entities if matches(entity)]
        # Most wounded first; NPCs without known health last.
        visible.sort(
            key=lambda entity: (
                entity["health"]["percent"] is None,
                entity["health"]["percent"] if entity["health"]["percent"] is not None else 100,
                (entity["name"] or "").casefold(),
                entity["runtime_id"],
            )
        )
        return visible

    @staticmethod
    def _npc_row(entity):
        health = entity["health"]
        if health["current"] == 0:
            tags = ("dead",)
        elif health["percent"] is not None and health["percent"] < 100:
            tags = ("wounded",)
        else:
            tags = ()
        action_sequence = (entity.get("animation") or {}).get("action_sequence")
        values = (
            entity["name"] or "(unnamed)",
            format_number(entity["config_id"]),
            str(entity["runtime_id"]),
            format_number(health["current"]) + " / " + format_number(health["max"]),
            format_number(health["percent"]),
            format_number(action_sequence),
        )
        return (f"npc_{entity['runtime_id']}_{entity['instance_id']}", values, tags)

    @staticmethod
    def _hit_row(event):
        values = (
            format_clock_time(event["observed_at_ms"], with_milliseconds=True),
            f"{event.get('name') or 'NPC'} #{event['runtime_id']}",
            format_number(event["amount"]),
            str(event["type_id"]),
        )
        row_id = "hit_" + "_".join(str(value) for value in hit_event_key(event))
        return (row_id, values, ("hit",))

    def render(self, record):
        npcs = record.get("npcs") or {}
        entities = npcs.get("entities")
        if entities is None:
            self.show_unavailable("NPC: " + (npcs.get("reason") or "unavailable"))
        else:
            visible = self._visible_entities(entities)
            sync_tree_rows(self.npc_tree, [self._npc_row(entity) for entity in visible])
            skipped_count = npcs.get("skipped_count", 0)
            self.status.config(
                text=(
                    f"Loaded {npcs.get('loaded_count', MISSING_VALUE)} · "
                    f"read {npcs.get('read_count', MISSING_VALUE)} · "
                    f"displayed {len(visible)} · skipped {skipped_count}"
                ),
                fg=theme.GOLD if skipped_count else theme.GREEN,
            )
        recent_hits = record.get("recent_hit_splats", [])
        sync_tree_rows(self.hit_tree, [self._hit_row(event) for event in reversed(recent_hits)])


class InventoryPanel:
    """Backpack grid, equipped items and recent inventory changes."""

    def __init__(self, parent):
        self.status = _status_label(parent, "Waiting for inventory …")
        body = _panel_frame(parent)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)
        self.backpack_cells = self._build_backpack_grid(body)

        side = _panel_frame(body)
        side.grid(row=0, column=1, sticky="nsew")
        self.loadout_status = tk.Label(
            side,
            text="Equipment unknown",
            bg=theme.PANEL,
            fg=theme.GOLD,
            font=theme.HEADING_FONT,
            anchor="w",
        )
        self.loadout_status.pack(fill="x", pady=(0, 6))
        equipment_frame = _panel_frame(side)
        equipment_frame.pack(fill="x")
        equipment_frame.grid_columnconfigure(0, weight=1)
        equipment_frame.grid_rowconfigure(1, weight=1)
        self.equipment_tree = create_table(
            equipment_frame, 0, [("slot", "Slot", 100), ("item", "Equipped item / ID", 260)]
        )
        self.equipment_tree.configure(height=7)
        create_section_label(side, "RECENT INVENTORY CHANGES", pady=(10, 4))
        self.history_text = tk.Text(
            side,
            bg=theme.CELL,
            fg=theme.TEXT,
            relief="flat",
            font=theme.SMALL_CODE_FONT,
            height=8,
            wrap="word",
            state="disabled",
        )
        self.history_text.pack(fill="both", expand=True)
        create_note(
            parent,
            "Slots follow native inventory order (1–28). The grid uses four columns; "
            "it does not measure game pixels.\n"
            "Tracking identity is inferred; identical duplicates can be ambiguous. "
            "History reports observed changes.",
        )

    @staticmethod
    def _build_backpack_grid(parent):
        """Create a scrolling four-column grid with one label per backpack slot."""
        container = _panel_frame(parent)
        container.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        canvas = tk.Canvas(container, bg=theme.PANEL, highlightthickness=0, width=1, height=1)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        grid = _panel_frame(canvas)
        window = canvas.create_window(0, 0, anchor="nw", window=grid)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        grid.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        for column in range(INVENTORY_GRID_COLUMNS):
            grid.grid_columnconfigure(column, weight=1, uniform="inventory")
        cells = []
        for slot in range(BACKPACK_SLOT_COUNT):
            cell = tk.Label(
                grid,
                text=f"{slot + 1:02d}  {MISSING_VALUE}",
                bg=theme.CELL,
                fg=theme.MUTED_TEXT,
                font=theme.SMALL_FONT,
                justify="left",
                anchor="nw",
                padx=8,
                pady=6,
                height=3,
                wraplength=135,
            )
            cell.grid(
                row=slot // INVENTORY_GRID_COLUMNS,
                column=slot % INVENTORY_GRID_COLUMNS,
                sticky="nsew",
                padx=3,
                pady=3,
            )
            cells.append(cell)
        return cells

    @staticmethod
    def _cell_content(slot_number, item):
        """Return (text, colour); a missing observation differs from a confirmed empty slot."""
        if item is None:
            return f"{slot_number:02d}  Unavailable", theme.MUTED_TEXT
        if item["empty"]:
            return f"{slot_number:02d}  Empty", theme.MUTED_TEXT
        doses = f" · {item['doses']} doses" if item["doses"] is not None else ""
        text = (
            f"{slot_number:02d}  {item['name'] or 'Unknown item'}\n"
            f"ID {item['item_id']} · x{item['quantity']}{doses}"
        )
        return text, theme.GREEN if item["effects"]["heals"] else theme.TEXT

    def _render_backpack(self, inventory, changes):
        slots = inventory.get("slots")
        for index, cell in enumerate(self.backpack_cells):
            item = slots[index] if slots and index < len(slots) else None
            text, colour = self._cell_content(index + 1, item)
            if cell.cget("text") != text:
                cell.config(text=text, fg=colour)
        if slots:
            occupied = sum(not item["empty"] for item in slots)
            catalog_status = inventory.get("catalog_status")
            metadata = f"metadata {catalog_status}"
            if catalog_status != "available" and inventory.get("reason"):
                metadata += f" ({inventory['reason']})"
            self.status.config(
                text=(
                    f"Backpack: {occupied} / {BACKPACK_SLOT_COUNT} occupied · {metadata} · "
                    f"lost changes {changes.get('events_lost', 0)}"
                ),
                fg=theme.GREEN if catalog_status == "available" else theme.GOLD,
            )
        else:
            reason = inventory.get("reason") or "waiting for a fresh C sample"
            self.status.config(text=f"Inventory unavailable: {reason}", fg=theme.RED)

    def _render_equipment(self, equipment):
        configuration = equipment.get("configuration", "unknown").replace("_", " ")
        bone_shield_tier = equipment.get("bone_shield_tier", "unknown")
        self.loadout_status.config(text=f"{configuration} · Bone Shield: {bone_shield_tier}")
        rows = [
            (
                f"equip_{item['slot']}",
                (
                    EQUIPMENT_SLOT_NAMES.get(item["slot"], str(item["slot"])),
                    f"{item['name'] or 'Unknown'} / {item['item_id']}",
                ),
                (),
            )
            for item in equipment.get("slots") or []
            if not item["empty"]
        ]
        sync_tree_rows(self.equipment_tree, rows)

    @staticmethod
    def _describe_item(item):
        if not item:
            return "empty"
        location = f"[{item['container_id']}:{item['slot_number']}]"
        return f"{item.get('name') or 'Item'} #{item['item_id']} {location}"

    def _render_history(self, recent_changes):
        lines = []
        for change in reversed(recent_changes[-RECENT_INVENTORY_CHANGES_SHOWN:]):
            observed = format_clock_time(change["observed_at_ms"])
            kind = change["kind"].replace("_", " ")
            before = self._describe_item(change.get("before"))
            after = self._describe_item(change.get("after"))
            lines.append(f"{observed} {kind}\n{before} -> {after}\n")
        text = "\n".join(lines) or "New moves, dose changes and equipment swaps will appear here."
        if self.history_text.get("1.0", "end-1c") != text:
            replace_text(self.history_text, text)

    def render(self, record):
        self._render_backpack(record.get("inventory") or {}, record.get("inventory_changes") or {})
        self._render_equipment(record.get("equipment") or {})
        self._render_history(record.get("recent_inventory_changes", []))


class AbilityPanel:
    """Ability definitions with live cooldowns and weapon requirement checks."""

    def __init__(self, parent):
        self.name_filter = tk.StringVar()
        self.ability_book_only = tk.BooleanVar(value=True)
        controls = _panel_frame(parent)
        controls.pack(fill="x", pady=(0, 8))
        create_filter_entry(controls, "Filter ability:", self.name_filter, 25)
        create_checkbox(controls, "Ability book entries", self.ability_book_only)
        self.status = _status_label(parent, "Waiting for abilities …")
        frame = _panel_frame(parent)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        self.tree = create_table(
            frame,
            0,
            [
                ("name", "Ability", 210),
                ("id", "Struct ID", 75),
                ("remaining", "Cooldown left", 110),
                ("base", "Base cooldown", 110),
                ("requires", "Weapon requirements", 220),
                ("match", "Equipment match", 145),
            ],
        )
        self.tree.tag_configure("running", foreground=theme.GOLD)
        self.tree.tag_configure("blocked", foreground=theme.RED)
        create_note(
            parent,
            "Equipment match checks weapon requirements, not every cast condition. "
            "Levels, unlocks, adrenaline,\n"
            "targets, runes and ability-specific overrides may still prevent casting. "
            "Unknown timers are not shown as ready.",
        )

    def _is_shown(self, ability, needle):
        if self.ability_book_only.get() and not ability["in_ability_book"]:
            return False
        return not needle or needle in ability["name"].casefold()

    @staticmethod
    def _ability_row(ability):
        cooldown, requirements = ability["cooldown"], ability["requirements"]
        remaining = cooldown["remaining_ms"]
        # This label reflects the cooldown only; the equipment match has its own column.
        if remaining is None:
            cooldown_text = "Unknown"
        elif remaining == 0:
            cooldown_text = "Ready"
        else:
            cooldown_text = _format_seconds(remaining, 2)
        required = [label for key, label in REQUIREMENT_LABELS if requirements[key]]
        if requirements["style"]:
            required.insert(0, requirements["style"].title())
        if remaining:
            tags = ("running",)
        elif ability["equipment_match"] == "blocked":
            tags = ("blocked",)
        else:
            tags = ()
        base = cooldown["base_duration_ms"]
        values = (
            ability["name"].replace("<nbsp>", " "),
            ability["struct_id"],
            cooldown_text,
            "Unknown" if base is None else _format_seconds(base, 1),
            " · ".join(required) or "No specific weapon setup",
            ability["equipment_match"].title(),
        )
        return (f"ability_{ability['struct_id']}", values, tags)

    def render(self, record):
        abilities = record.get("abilities") or {}
        entries = abilities.get("entries") or []
        needle = self.name_filter.get().strip().casefold()
        rows = [
            self._ability_row(ability) for ability in entries if self._is_shown(ability, needle)
        ]
        sync_tree_rows(self.tree, rows)
        if entries:
            live_timers = sum(
                ability["cooldown"]["remaining_ms"] is not None for ability in entries
            )
            self.status.config(
                text=(
                    f"{len(entries)} definitions · {len(rows)} displayed · "
                    f"{live_timers} live timers known · cooldown and equipment are separate checks"
                ),
                fg=theme.GREEN,
            )
        else:
            message = abilities.get("message") or abilities.get("reason") or "waiting for a sample"
            self.status.config(text=f"Ability metadata unavailable: {message}", fg=theme.RED)


class SourcePanel:
    """Collector timing and the raw values behind the meters."""

    def __init__(self, parent):
        tk.Label(
            parent,
            text="SOURCE AND RAW VALUES",
            fg=theme.MUTED_TEXT,
            bg=theme.PANEL,
            font=theme.HEADING_FONT,
        ).pack(anchor="w")
        self.timing = tk.Label(
            parent,
            text=MISSING_VALUE,
            fg=theme.GREEN,
            bg=theme.PANEL,
            font=theme.CODE_FONT,
            anchor="w",
        )
        self.timing.pack(fill="x", pady=(6, 6))
        self.raw_text = tk.Text(
            parent,
            height=6,
            bg=theme.PANEL,
            fg=theme.TEXT,
            relief="flat",
            font=theme.CODE_FONT,
            wrap="none",
            padx=0,
            pady=0,
            highlightthickness=0,
            state="disabled",
        )
        self.raw_text.pack(fill="both", expand=True)

    def set_raw_text(self, value):
        replace_text(self.raw_text, value)

    def set_timing(self, record, age_ms):
        native = record.get("native", {})
        if native:
            collector = (
                f"C collector {native.get('worker_hz')} Hz · {native.get('read_calls')} reads · "
                f"lost events {native.get('events_lost')}"
            )
        else:
            collector = "Waiting for collector"
        sample_number = native.get("sample_sequence", record.get("sample_sequence", MISSING_VALUE))
        self.timing.config(
            text=(
                f"Sample {format_clock_time(record['timestamp_ms'], with_milliseconds=True)}  |  "
                f"age {age_ms:.0f} ms  |  #{sample_number}  |  "
                f"{record.get('read_duration_ms', 0):.2f} ms/read  |  {collector}"
            )
        )

    def render_player_values(self, record, prayer_points, adrenaline, remaining_ms):
        raw = record["raw_variables"]
        gcd = record["gcd"]
        self.set_raw_text(
            f"VARP 13537 HP       = {raw['health_current']!s:<8}  "
            f"VARP 13538 max HP = {raw['health_max']}\n"
            f"VARP 679 adrenaline = {raw['adrenaline']!s:<8}  "
            f"/ 10 = {format_number(adrenaline)} %\n"
            f"VARP 3274 prayer    = {raw['prayer_packed']!s:<8}  "
            f"bits 0..14 / 10 = {format_number(prayer_points)}\n"
            f"VARC 2091 GCD start = {raw['gcd_client_start']!s:<8}  "
            f"VARC 2092 end = {raw['gcd_client_end']}\n"
            f"Client logic clock = {gcd['clock_raw']!s:<8}  "
            f"GCD remaining = {format_number(remaining_ms)} ms\n"
            "Freshness: direct memory sampling; last server update time is unknown."
        )
