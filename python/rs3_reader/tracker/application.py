"""Tracker main window, refresh loop and command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..native import MAX_PID
from . import theme
from .collector import LOG_DIRECTORY, TelemetryCollector, new_log_path
from .panels import AbilityPanel, InventoryPanel, NPCPanel, SourcePanel
from .widgets import Meter, format_number

REFRESH_INTERVAL_MS = 50
STALE_AFTER_MS = 1500
CLOCK_STALL_LIMIT_MS = 2000
WINDOW_GEOMETRY = "1240x880"
WINDOW_MINIMUM_SIZE = (1060, 760)
MIN_NATIVE_HZ = 1
MAX_NATIVE_HZ = 1000
METERS = (
    ("health", "HEALTH / LIFE POINTS", theme.RED),
    ("prayer", "PRAYER", theme.BLUE),
    ("adrenaline", "ADRENALINE", theme.GOLD),
    ("global_cooldown", "GLOBAL COOLDOWN", theme.GREEN),
)


class TrackerApplication:
    """Builds the window and renders the newest record from the collector."""

    def __init__(self, root, history_path=None, native_hz=100, *, pid=None):
        self.root = root
        self.latest_record = None
        self.latest_received_at = None
        self.collector = TelemetryCollector(pid, native_hz, history_path, new_log_path())

        root.title("RS3 Live Tracker · direct memory reader")
        root.geometry(WINDOW_GEOMETRY)
        root.minsize(*WINDOW_MINIMUM_SIZE)
        root.configure(bg=theme.BACKGROUND)
        theme.configure_styles(root)

        self._build_header()
        self._build_tile_bar()
        self._build_meters()
        self._build_tabs(has_imported_history=bool(history_path))
        self._build_footer()

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.collector.start()
        root.after(REFRESH_INTERVAL_MS, self.refresh)

    # -- Layout ---------------------------------------------------------------

    def _build_header(self):
        header = tk.Frame(self.root, bg=theme.BACKGROUND)
        header.pack(fill="x", padx=24, pady=(20, 12))
        tk.Label(
            header,
            text="RS3  /  LIVE TRACKER",
            fg=theme.TEXT,
            bg=theme.BACKGROUND,
            font=theme.TITLE_FONT,
        ).pack(side="left")
        self.connection_status = tk.Label(
            header,
            text="CONNECTING",
            fg=theme.GOLD,
            bg=theme.PANEL,
            padx=12,
            pady=6,
            font=theme.HEADING_FONT,
        )
        self.connection_status.pack(side="right")
        self.source_line = tk.Label(
            self.root,
            text="Verifying the client version …",
            bg=theme.BACKGROUND,
            fg=theme.MUTED_TEXT,
            font=theme.BODY_FONT,
            anchor="w",
        )
        self.source_line.pack(fill="x", padx=24, pady=(0, 12))

    def _build_tile_bar(self):
        tile_bar = tk.Frame(self.root, bg=theme.PANEL, padx=16, pady=8)
        tile_bar.pack(fill="x", padx=24, pady=(0, 6))
        tk.Label(
            tile_bar,
            text="PLAYER TILE",
            fg=theme.MUTED_TEXT,
            bg=theme.PANEL,
            font=theme.HEADING_FONT,
        ).pack(side="left")
        self.tile_label = tk.Label(
            tile_bar,
            text="Waiting for tile …",
            fg=theme.TEXT,
            bg=theme.PANEL,
            font=theme.TILE_FONT,
        )
        self.tile_label.pack(side="right")

    def _build_meters(self):
        grid = tk.Frame(self.root, bg=theme.BACKGROUND)
        grid.pack(fill="x", padx=18)
        grid.grid_columnconfigure(tuple(range(len(METERS))), weight=1, uniform="meters")
        self.meters = {}
        for column, (key, title, colour) in enumerate(METERS):
            meter = Meter(grid, title, colour)
            meter.frame.grid(row=0, column=column, padx=6, pady=6, sticky="nsew")
            self.meters[key] = meter

    def _build_tabs(self, has_imported_history):
        tabs = ttk.Notebook(self.root, style=theme.NOTEBOOK_STYLE)
        tabs.pack(fill="both", expand=True, padx=24, pady=(14, 0))
        npc_tab = tk.Frame(tabs, bg=theme.PANEL, padx=12, pady=12)
        source_tab = tk.Frame(tabs, bg=theme.PANEL, padx=18, pady=12)
        inventory_tab = tk.Frame(tabs, bg=theme.PANEL, padx=12, pady=12)
        ability_tab = tk.Frame(tabs, bg=theme.PANEL, padx=12, pady=12)
        tabs.add(npc_tab, text="NPCs and hit splats")
        tabs.add(source_tab, text="Raw values and source")
        tabs.add(inventory_tab, text="Inventory and equipment")
        tabs.add(ability_tab, text="Abilities and cooldowns")
        self.npc_panel = NPCPanel(npc_tab, has_imported_history)
        self.source_panel = SourcePanel(source_tab)
        self.inventory_panel = InventoryPanel(inventory_tab)
        self.ability_panel = AbilityPanel(ability_tab)

        self.status_line = tk.Label(
            self.root,
            text=(
                "NPC HP: client statistics · hit splats: observed records, attacker unknown · "
                "Telos remains unverified."
            ),
            bg=theme.BACKGROUND,
            fg=theme.GOLD,
            font=theme.BODY_FONT,
            anchor="w",
        )
        self.status_line.pack(fill="x", padx=24, pady=(10, 4))

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=theme.BACKGROUND)
        footer.pack(fill="x", padx=24, pady=(6, 18))
        buttons = (
            ("Open log folder", lambda: os.startfile(str(LOG_DIRECTORY))),
            ("Copy current JSON", self.copy_latest_record),
        )
        for text, command in buttons:
            tk.Button(
                footer,
                text=text,
                command=command,
                bg=theme.CONTROL,
                fg=theme.TEXT,
                activebackground=theme.CONTROL_ACTIVE,
                activeforeground=theme.TEXT,
                relief="flat",
                padx=12,
                pady=8,
                font=theme.SMALL_FONT,
            ).pack(side="left", padx=(0, 10))
        tk.Label(
            footer,
            text="20 Hz display · read only",
            fg=theme.MUTED_TEXT,
            bg=theme.BACKGROUND,
            font=theme.SMALL_FONT,
        ).pack(side="right")

    # -- Refresh --------------------------------------------------------------

    def refresh(self):
        """Render a new record when one arrived, then update the age and staleness."""
        newest = self.collector.take_latest()
        if newest is not None:
            self.latest_record, self.latest_received_at = newest
            self.render(self.latest_record)
        if self.latest_record:
            # Add the time since publication to the snapshot age measured by the collector.
            queued_ms = (time.perf_counter() - self.latest_received_at) * 1000
            snapshot_age_ms = self.latest_record.get("native", {}).get("snapshot_age_ms", 0)
            age_ms = queued_ms + snapshot_age_ms
            self.source_panel.set_timing(self.latest_record, age_ms)
            if age_ms > STALE_AFTER_MS:
                self._show_stale()
        self.root.after(REFRESH_INTERVAL_MS, self.refresh)

    def _show_stale(self):
        self.connection_status.config(text="STALE", fg=theme.RED)
        self.tile_label.config(text="Waiting for a fresh tile")
        self._clear_meters("Samples are not updating")
        self.npc_panel.show_unavailable("NPC data is stale; waiting for a fresh sample.")
        self.status_line.config(text="Active prayer unavailable; samples are stale.")

    def _clear_meters(self, detail):
        for meter in self.meters.values():
            meter.clear(detail)

    def _render_header(self, record, fresh):
        overhead = (record.get("combat") or {}).get("active_prayer") if fresh else None
        overhead_text = overhead.replace("_", " ").title() if overhead else "unknown"
        self.status_line.config(
            text=(
                f"Active prayer: {overhead_text} · NPC sequences are raw · "
                "Telos attack timing is uncalibrated."
            )
        )
        position = record.get("position") if fresh else None
        self.tile_label.config(
            text=(
                f"{position['x']}, {position['y']} · plane {position['plane']}"
                if position is not None
                else "Tile unavailable"
            )
        )
        self.connection_status.config(
            text="LIVE · MEMORY" if fresh else "NO FRESH DATA",
            fg=theme.GREEN if fresh else theme.RED,
        )

    def _render_source_line(self, record, available):
        if available:
            digest = record.get("executable_sha256", "")
            self.source_line.config(
                text=(
                    f"rs2client.exe  |  PID {record.get('pid', '—')}  |  "
                    f"build {record.get('client_version', '—')}  |  "
                    f"SHA-256 {digest[:8]}…{digest[-6:]}"
                )
            )
        else:
            reason = record.get("reason") or "unknown reason"
            self.source_line.config(text="Waiting for a supported client: " + reason)

    def _render_meters(self, record):
        """Fill the four meters and the raw-value text from a record with player fields."""
        health, prayer, gcd = record["health"], record["prayer"], record["gcd"]
        adrenaline = record["adrenaline_percent"]["value"]
        life_points = health["current"]["value"]
        prayer_points = prayer["current"]["value"]
        self.meters["health"].set(
            format_number(life_points) + " / " + format_number(health["max"]),
            (life_points or 0) / (health["max"] or 1),
            "VARP 13537 / 13538 · direct values",
        )
        self.meters["prayer"].set(
            format_number(prayer["display_current"]) + " / " + format_number(prayer["max"]),
            (prayer_points or 0) / (prayer["max"] or 1),
            f"Exact {format_number(prayer_points)} · max derived from prayer level",
        )
        self.meters["adrenaline"].set(
            format_number(adrenaline) + " %",
            (adrenaline or 0) / 100,
            "VARP 679 ÷ 10 · direct value",
        )
        remaining_ms = gcd["remaining_ms"]
        if remaining_ms is None:
            cooldown_text = "—"
        elif remaining_ms == 0:
            cooldown_text = "READY"
        else:
            cooldown_text = f"{remaining_ms / 1000:.2f} s"
        self.meters["global_cooldown"].set(
            cooldown_text,
            (remaining_ms or 0) / (gcd["duration_ms"] or 1),
            "VARC 2091 / 2092 · tick 20 ms" if remaining_ms is not None else gcd["reason"],
        )
        self.source_panel.render_player_values(record, prayer_points, adrenaline, remaining_ms)

    def _render_panels(self, record):
        self.npc_panel.render(record)
        self.inventory_panel.render(record)
        self.ability_panel.render(record)

    def render(self, record):
        available = record["status"] != "unavailable"
        stalled = record.get("freshness", {}).get("clock_stalled_ms", 0) > CLOCK_STALL_LIMIT_MS
        fresh = available and not stalled
        self._render_header(record, fresh)
        self._render_source_line(record, available)
        if not fresh:
            self.inventory_panel.render({})
            self.ability_panel.render({})
            self._clear_meters("Unavailable" if not available else "Client clock is not advancing")
            self.source_panel.set_raw_text(record.get("reason") or "Client clock is not advancing")
            self.npc_panel.show_unavailable("NPC data is unavailable.")
            return
        if record["health"] is None:
            # The Compatibility Mode tile can be available without player fields.
            self._clear_meters("Unavailable on this build")
            self.source_panel.set_raw_text("Player tile (x, y, plane) · direct memory read")
        else:
            self._render_meters(record)
        self._render_panels(record)

    # -- Actions --------------------------------------------------------------

    def copy_latest_record(self):
        if self.latest_record:
            self.root.clipboard_clear()
            self.root.clipboard_append(json.dumps(self.latest_record, ensure_ascii=False, indent=2))

    def close(self):
        self.collector.stop()
        self.root.destroy()


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Local desktop display for the read-only RS3 memory reader."
    )
    parser.add_argument(
        "--history", type=Path, help="Import observed hits from an earlier local JSONL capture"
    )
    parser.add_argument("--pid", type=int, help="Use a specific rs2client.exe process ID")
    parser.add_argument(
        "--native-hz", type=int, default=100, help="Native collector frequency, 1..1000 Hz"
    )
    arguments = parser.parse_args()
    if arguments.pid is not None and not 0 < arguments.pid <= MAX_PID:
        parser.error("pid must be an unsigned nonzero 32-bit integer")
    if not MIN_NATIVE_HZ <= arguments.native_hz <= MAX_NATIVE_HZ:
        parser.error("native-hz must be 1..1000")
    return arguments


def main():
    arguments = parse_arguments()
    root = tk.Tk()
    TrackerApplication(root, arguments.history, arguments.native_hz, pid=arguments.pid)
    root.mainloop()
