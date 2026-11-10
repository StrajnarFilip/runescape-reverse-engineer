"""Colours, fonts and ttk styles of the tracker window."""

from tkinter import ttk

BACKGROUND = "#0c1220"
PANEL = "#151f30"
TEXT = "#eef4ff"
MUTED_TEXT = "#93a4bd"
GREEN = "#55dfad"
RED = "#ff7385"
BLUE = "#69b4ff"
GOLD = "#f2c568"
METER_TRACK = "#28374d"
CONTROL = "#26374e"
CONTROL_ACTIVE = "#344b68"
SELECTION = "#345371"
CELL = "#202e43"
SELECTED_TEXT = "white"

UI_FONT = "Segoe UI"
MONOSPACE_FONT = "Consolas"
TITLE_FONT = (UI_FONT, 20, "bold")
HEADING_FONT = (UI_FONT, 10, "bold")
SECTION_FONT = (UI_FONT, 9, "bold")
BODY_FONT = (UI_FONT, 10)
SMALL_FONT = (UI_FONT, 9)
ENTRY_FONT = (UI_FONT, 11)
METER_VALUE_FONT = (UI_FONT, 23, "bold")
TILE_FONT = (MONOSPACE_FONT, 13, "bold")
CODE_FONT = (MONOSPACE_FONT, 10)
SMALL_CODE_FONT = (MONOSPACE_FONT, 9)

NOTEBOOK_STYLE = "Tracker.TNotebook"
TREEVIEW_STYLE = "Tracker.Treeview"


def configure_styles(root):
    """Register the tracker's ttk notebook and tree view styles."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(NOTEBOOK_STYLE, background=BACKGROUND, borderwidth=0)
    style.configure(
        NOTEBOOK_STYLE + ".Tab",
        background=PANEL,
        foreground=MUTED_TEXT,
        padding=(16, 9),
        font=BODY_FONT,
    )
    style.map(
        NOTEBOOK_STYLE + ".Tab",
        background=[("selected", CONTROL)],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        TREEVIEW_STYLE,
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        rowheight=27,
        font=BODY_FONT,
        borderwidth=0,
    )
    style.configure(
        TREEVIEW_STYLE + ".Heading",
        background=CONTROL,
        foreground=TEXT,
        font=SECTION_FONT,
        relief="flat",
    )
    style.map(
        TREEVIEW_STYLE,
        background=[("selected", SELECTION)],
        foreground=[("selected", SELECTED_TEXT)],
    )
