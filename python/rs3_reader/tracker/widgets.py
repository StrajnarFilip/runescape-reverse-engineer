"""Reusable tracker widgets and display formatting."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import theme

MISSING_VALUE = "—"
METER_BAR_HEIGHT = 7
# Columns that stretch and align left; every other column is centred.
STRETCHING_COLUMNS = ("name", "npc")
MIN_COLUMN_WIDTH = 55


def format_number(value):
    """Format an integral value without decimals and others with one decimal."""
    if value is None:
        return MISSING_VALUE
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def format_clock_time(timestamp_ms, with_milliseconds=False):
    """Format a Unix timestamp in milliseconds as local HH:MM:SS[.mmm]."""
    moment = datetime.fromtimestamp(timestamp_ms / 1000)
    if with_milliseconds:
        return moment.strftime("%H:%M:%S.%f")[:-3]
    return moment.strftime("%H:%M:%S")


def replace_text(text_widget, value):
    """Replace the contents of a read-only Text widget."""
    text_widget.config(state="normal")
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value)
    text_widget.config(state="disabled")


class Meter:
    """A titled value with a fill bar and a detail line."""

    def __init__(self, parent, title, colour):
        self.colour = colour
        self.fraction = 0
        self.frame = tk.Frame(parent, bg=theme.PANEL, padx=22, pady=16)
        tk.Label(
            self.frame, text=title, fg=theme.MUTED_TEXT, bg=theme.PANEL, font=theme.HEADING_FONT
        ).pack(anchor="w")
        self.value = tk.Label(
            self.frame,
            text=MISSING_VALUE,
            fg=theme.TEXT,
            bg=theme.PANEL,
            font=theme.METER_VALUE_FONT,
        )
        self.value.pack(anchor="w", pady=(4, 3))
        self.bar = tk.Canvas(
            self.frame, height=METER_BAR_HEIGHT, bg=theme.PANEL, highlightthickness=0
        )
        self.bar.pack(fill="x", pady=(0, 8))
        self.detail = tk.Label(
            self.frame,
            text="Waiting for a sample …",
            fg=theme.MUTED_TEXT,
            bg=theme.PANEL,
            font=theme.SMALL_FONT,
            anchor="w",
            wraplength=235,
            height=2,
            justify="left",
        )
        self.detail.pack(anchor="w")
        self.bar.bind("<Configure>", lambda _event: self.paint())

    def paint(self):
        width = max(1, self.bar.winfo_width())
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, width, METER_BAR_HEIGHT, fill=theme.METER_TRACK, outline="")
        if self.fraction:
            self.bar.create_rectangle(
                0, 0, width * self.fraction, METER_BAR_HEIGHT, fill=self.colour, outline=""
            )

    def set(self, text, fraction, detail):
        self.value.config(text=text)
        self.detail.config(text=detail)
        self.fraction = max(0, min(1, fraction or 0))
        self.paint()

    def clear(self, detail):
        self.set(MISSING_VALUE, 0, detail)


def sync_tree_rows(tree, rows):
    """Update a tree view from (row ID, values, tags) tuples.

    Rows are reused by their stable ID so a refresh keeps the selection on
    rows that survive.
    """
    stale_rows = set(tree.get_children())
    for position, (row_id, values, tags) in enumerate(rows):
        if row_id in stale_rows:
            stale_rows.remove(row_id)
            if tuple(tree.item(row_id, "values")) != tuple(str(value) for value in values):
                tree.item(row_id, values=values, tags=tags)
        else:
            tree.insert("", "end", iid=row_id, values=values, tags=tags)
        tree.move(row_id, "", position)
    for row_id in stale_rows:
        tree.delete(row_id)


def create_table(parent, grid_column, columns):
    """Create a scrolling tree view in row 1 of parent's grid.

    columns is a sequence of (key, heading, width) tuples.
    """
    frame = tk.Frame(parent, bg=theme.PANEL)
    horizontal_padding = (0, 12) if grid_column == 0 else (0, 0)
    frame.grid(row=1, column=grid_column, sticky="nsew", padx=horizontal_padding)
    tree = ttk.Treeview(
        frame,
        columns=[key for key, _, _ in columns],
        show="headings",
        style=theme.TREEVIEW_STYLE,
        selectmode="browse",
    )
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)
    for key, heading, width in columns:
        stretches = key in STRETCHING_COLUMNS
        tree.heading(key, text=heading)
        tree.column(
            key,
            width=width,
            minwidth=MIN_COLUMN_WIDTH,
            stretch=stretches,
            anchor="w" if stretches else "center",
        )
    return tree


def create_section_label(parent, text, **pack_options):
    label = tk.Label(
        parent, text=text, bg=theme.PANEL, fg=theme.MUTED_TEXT, font=theme.SECTION_FONT
    )
    label.pack(anchor="w", **pack_options)
    return label


def create_note(parent, text):
    """Create the explanatory footnote of a panel."""
    tk.Label(
        parent,
        text=text,
        bg=theme.PANEL,
        fg=theme.MUTED_TEXT,
        justify="left",
        anchor="w",
        font=theme.SMALL_FONT,
    ).pack(fill="x", pady=(8, 0))


def create_filter_entry(parent, label_text, variable, width):
    tk.Label(
        parent, text=label_text, bg=theme.PANEL, fg=theme.MUTED_TEXT, font=theme.BODY_FONT
    ).pack(side="left")
    tk.Entry(
        parent,
        textvariable=variable,
        bg=theme.CONTROL,
        fg=theme.TEXT,
        insertbackground=theme.TEXT,
        relief="flat",
        font=theme.ENTRY_FONT,
        width=width,
    ).pack(side="left", padx=8)


def create_checkbox(parent, text, variable, **options):
    tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=theme.PANEL,
        fg=theme.TEXT,
        activebackground=theme.PANEL,
        activeforeground=theme.TEXT,
        selectcolor=theme.BACKGROUND,
        **options,
    ).pack(side="left", padx=10)
