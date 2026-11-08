"""Local desktop display for the read-only RS3 memory reader (no dependencies)."""
from datetime import datetime
from collections import deque
from contextlib import closing
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from telemetry import Telemetry

BG, PANEL, INK, MUTED = "#0c1220", "#151f30", "#eef4ff", "#93a4bd"
GREEN, RED, BLUE, GOLD = "#55dfad", "#ff7385", "#69b4ff", "#f2c568"

def fmt(value):
    if value is None:
        return "—"
    return str(int(value)) if value == int(value) else ("%.1f" % value)

class Meter:
    def __init__(self, parent, title, colour):
        self.colour = colour
        self.frame = tk.Frame(parent, bg=PANEL, padx=22, pady=16)
        tk.Label(self.frame, text=title, fg=MUTED, bg=PANEL,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.value = tk.Label(self.frame, text="—", fg=INK, bg=PANEL,
                              font=("Segoe UI", 29, "bold"))
        self.value.pack(anchor="w", pady=(4, 3))
        self.bar = tk.Canvas(self.frame, height=7, bg=PANEL, highlightthickness=0)
        self.bar.pack(fill="x", pady=(0, 8))
        self.detail = tk.Label(self.frame, text="Waiting for a sample …", fg=MUTED,
                               bg=PANEL, font=("Segoe UI", 9), anchor="w")
        self.detail.pack(anchor="w")
        self.fraction = 0
        self.bar.bind("<Configure>", lambda _: self.paint())

    def paint(self):
        w = max(1, self.bar.winfo_width())
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, w, 7, fill="#28374d", outline="")
        if self.fraction:
            self.bar.create_rectangle(0, 0, w*self.fraction, 7, fill=self.colour, outline="")

    def set(self, text, fraction, detail):
        self.value.config(text=text)
        self.detail.config(text=detail)
        self.fraction = max(0, min(1, fraction or 0))
        self.paint()

def event_key(event):
    # Native sequence numbers restart with a new context; raw hit identity also
    # works when importing history from a previous collector in the same client.
    return tuple(event.get(k) for k in ("pid", "process_creation_time", "runtime_id", "instance_id", "config_id", "slot", "type_id", "amount",
                                        "end_client_tick", "secondary_type_id", "secondary_amount"))

class HitHistory:
    def __init__(self, capacity=150):
        self.events = deque(maxlen=capacity)
        self.keys = set()

    def observe(self, record):
        for hit in ((record.get("hit_splats") or {}).get("newly_observed") or []):
            event = dict(hit)
            event.setdefault("observed_at_ms", record["timestamp_ms"])
            event.setdefault("pid", record.get("pid"))
            key = event_key(event)
            if key not in self.keys:
                if len(self.events) == self.events.maxlen:
                    self.keys.discard(event_key(self.events[0]))
                self.events.append(event)
                self.keys.add(key)

def sync_rows(tree, rows):
    old = set(tree.get_children())
    for position, (key, values, tags) in enumerate(rows):
        if key in old:
            old.remove(key)
            if tuple(tree.item(key, "values")) != tuple(str(v) for v in values):
                tree.item(key, values=values, tags=tags)
        else:
            tree.insert("", "end", iid=key, values=values, tags=tags)
        tree.move(key, "", position)
    for key in old:
        tree.delete(key)

class Tracker:
    def __init__(self, root, history_path=None, backend=None, native_hz=100):
        self.root = root
        root.title("RS3 Live Tracker · direct memory reader")
        self.backend, self.native_hz = backend, native_hz
        root.geometry("1240x880")
        root.minsize(1060, 760)
        root.configure(bg=BG)
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=1)
        self.latest = self.received_at = None
        self.sequence = 0
        self.hit_history = HitHistory()
        self.history_path = history_path
        self.filter_text = tk.StringVar()
        self.only_hp = tk.BooleanVar(value=True)
        logs = Path(__file__).resolve().parent / "logs"
        logs.mkdir(exist_ok=True)
        self.log_path = logs / (datetime.now().strftime("capture-%Y%m%d-%H%M%S-")+str(os.getpid())+".jsonl")

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 12))
        tk.Label(header, text="RS3  /  LIVE TRACKER", fg=INK, bg=BG,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        self.status = tk.Label(header, text="CONNECTING", fg=GOLD, bg=PANEL,
                               padx=12, pady=6, font=("Segoe UI", 10, "bold"))
        self.status.pack(side="right")
        self.source = tk.Label(root, text="Verifying the client version …", bg=BG,
                               fg=MUTED, font=("Segoe UI", 10), anchor="w")
        self.source.pack(fill="x", padx=24, pady=(0, 12))

        grid = tk.Frame(root, bg=BG)
        grid.pack(fill="x", padx=18)
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="meters")
        self.meters = {}
        for i, (key, title, colour) in enumerate([
            ("hp", "HEALTH / LIFE POINTS", RED), ("prayer", "PRAYER", BLUE),
            ("adren", "ADRENALINE", GOLD), ("gcd", "GLOBAL COOLDOWN", GREEN)]):
            meter = self.meters[key] = Meter(grid, title, colour)
            meter.value.config(font=("Segoe UI", 23, "bold"))
            meter.detail.config(wraplength=235, height=2, justify="left")
            meter.frame.grid(row=0, column=i, padx=6, pady=6, sticky="nsew")

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Tracker.TNotebook", background=BG, borderwidth=0)
        style.configure("Tracker.TNotebook.Tab", background=PANEL, foreground=MUTED,
                        padding=(16, 9), font=("Segoe UI", 10))
        style.map("Tracker.TNotebook.Tab", background=[("selected", "#26374e")],
                  foreground=[("selected", INK)])
        style.configure("Tracker.Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
                        rowheight=27, font=("Segoe UI", 10), borderwidth=0)
        style.configure("Tracker.Treeview.Heading", background="#26374e", foreground=INK,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Tracker.Treeview", background=[("selected", "#345371")], foreground=[("selected", "white")])
        tabs = ttk.Notebook(root, style="Tracker.TNotebook")
        tabs.pack(fill="both", expand=True, padx=24, pady=(14, 0))
        npc_tab = tk.Frame(tabs, bg=PANEL, padx=12, pady=12)
        proof = tk.Frame(tabs, bg=PANEL, padx=18, pady=12)
        tabs.add(npc_tab, text="NPCs and hit splats")
        tabs.add(proof, text="Raw values and source")
        self.build_npc_panel(npc_tab)
        tk.Label(proof, text="SOURCE AND RAW VALUES", fg=MUTED, bg=PANEL,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.timing = tk.Label(proof, text="—", fg=GREEN, bg=PANEL,
                               font=("Consolas", 10), anchor="w")
        self.timing.pack(fill="x", pady=(6, 6))
        self.raw_text = tk.Text(proof, height=6, bg=PANEL, fg=INK, relief="flat",
                                font=("Consolas", 10), wrap="none", padx=0, pady=0,
                                highlightthickness=0, state="disabled")
        self.raw_text.pack(fill="both", expand=True)
        self.pending = tk.Label(root, text="NPC HP: client statistics · hit splats: observed records, attacker unknown · Telos remains unverified.",
                                bg=BG, fg=GOLD, font=("Segoe UI", 10), anchor="w")
        self.pending.pack(fill="x", padx=24, pady=(10, 4))
        footer = tk.Frame(root, bg=BG)
        footer.pack(fill="x", padx=24, pady=(6, 18))
        for text, command in [("Open log folder", lambda: os.startfile(str(logs))),
                              ("Copy current JSON", self.copy_json)]:
            tk.Button(footer, text=text, command=command, bg="#26374e", fg=INK,
                      activebackground="#344b68", activeforeground=INK, relief="flat",
                      padx=12, pady=8, font=("Segoe UI", 9)).pack(side="left", padx=(0, 10))
        tk.Label(footer, text="20 Hz display · read only", fg=MUTED, bg=BG,
                 font=("Segoe UI", 9)).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = threading.Thread(target=self.collect, daemon=True)
        self.worker.start()
        root.after(50, self.refresh)

    def collect(self):
        telemetry = None
        try:
            telemetry = Telemetry(backend=self.backend, worker_hz=self.native_hz)
            if self.history_path:
                with Path(self.history_path).open(encoding="utf-8") as source:
                    for line in source:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if record.get("executable_sha256") == "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45":
                            self.hit_history.observe(record)
            with closing(RotatingFileHandler(self.log_path, maxBytes=25*1024*1024,
                                            backupCount=3, encoding="utf-8")) as log:
                deadline = time.perf_counter()
                while not self.stop.is_set():
                    record = telemetry.sample(True)
                    self.sequence += 1
                    record["sample_sequence"] = self.sequence
                    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    log.emit(logging.makeLogRecord({"msg": line}))
                    self.hit_history.observe(record)
                    self.publish(dict(record, recent_hit_splats=list(self.hit_history.events)))
                    deadline += .05
                    delay = deadline-time.perf_counter()
                    if delay < -.5:
                        deadline = time.perf_counter()
                    self.stop.wait(max(0, delay))
        except Exception as error:
            from native_telemetry import NativeTelemetry
            self.publish(NativeTelemetry.unavailable("Tracker: "+str(error)))
        finally:
            if telemetry is not None:
                telemetry.close()

    def publish(self, record):
        try:
            self.queue.get_nowait()
        except queue.Empty:
            pass
        self.queue.put_nowait((record, time.perf_counter()))

    def refresh(self):
        try:
            record, received = self.queue.get_nowait()
            self.latest, self.received_at = record, received
            self.render(record)
        except queue.Empty:
            pass
        if self.latest:
            age = ((time.perf_counter()-self.received_at)*1000 +
                   self.latest.get("native", {}).get("snapshot_age_ms", 0))
            stamp = datetime.fromtimestamp(self.latest["timestamp_ms"]/1000).strftime("%H:%M:%S.%f")[:-3]
            native = self.latest.get("native", {})
            self.timing.config(text="Sample %s  |  age %.0f ms  |  #%s  |  %.2f ms/read  |  %s" %
                               (stamp, age, native.get("sample_sequence", self.latest.get("sample_sequence", "—")),
                                self.latest.get("read_duration_ms", 0),
                                "C collector %s Hz · %s reads · lost events %s" %
                                (native.get("worker_hz"), native.get("read_calls"), native.get("events_lost"))
                                if native else "Python collector"))
            if age > 1500:
                self.status.config(text="STALE", fg=RED)
                for meter in self.meters.values():
                    meter.set("—", 0, "Samples are not updating")
                sync_rows(self.npc_tree, [])
                self.npc_status.config(text="NPC data is stale; waiting for a fresh sample.", fg=RED)
        self.root.after(50, self.refresh)

    def render(self, s):
        available = s["status"] != "unavailable"
        stalled = s.get("freshness", {}).get("clock_stalled_ms", 0) > 2000
        self.status.config(text="LIVE · MEMORY" if available and not stalled else "NO FRESH DATA",
                           fg=GREEN if available and not stalled else RED)
        digest = s.get("executable_sha256", "")
        self.source.config(text="rs2client.exe  |  PID %s  |  build %s  |  SHA-256 %s…%s" %
                           (s.get("pid", "—"), s.get("client_version", "—"), digest[:8], digest[-6:]) if available
                           else "Waiting for a supported client: "+s.get("reason", "unknown reason"))
        if not available or stalled:
            for meter in self.meters.values():
                meter.set("—", 0, "Unavailable" if not available else "Client clock is not advancing")
            self.set_raw(s.get("reason", "Client clock is not advancing"))
            sync_rows(self.npc_tree, [])
            self.npc_status.config(text="NPC data is unavailable.", fg=RED)
            return
        h, p, a, g = s["health"], s["prayer"], s["adrenaline_percent"]["value"], s["gcd"]
        hp, prayer = h["current"]["value"], p["current"]["value"]
        self.meters["hp"].set(fmt(hp)+" / "+fmt(h["max"]), (hp or 0)/(h["max"] or 1), "VARP 13537 / 13538 · direct values")
        self.meters["prayer"].set(fmt(p["display_current"])+" / "+fmt(p["max"]), (prayer or 0)/(p["max"] or 1),
                                  "Exact %s · max derived from prayer level" % fmt(prayer))
        self.meters["adren"].set(fmt(a)+" %", (a or 0)/100, "VARP 679 ÷ 10 · direct value")
        remaining = g["remaining_ms"]
        self.meters["gcd"].set("—" if remaining is None else ("READY" if remaining == 0 else "%.2f s" % (remaining/1000)),
                                (remaining or 0)/(g["duration_ms"] or 1),
                                "VARC 2091 / 2092 · tick 20 ms" if remaining is not None else g["reason"])
        raw = s["raw_variables"]
        self.set_raw("VARP 13537 HP       = %-8s  VARP 13538 max HP = %s\n"
                     "VARP 679 adrenaline = %-8s  / 10 = %s %%\n"
                     "VARP 3274 prayer    = %-8s  bits 0..14 / 10 = %s\n"
                     "VARC 2091 GCD start = %-8s  VARC 2092 end = %s\n"
                     "Client logic clock = %-8s  GCD remaining = %s ms\n"
                     "Freshness: direct memory sampling; last server update time is unknown." %
                     (raw["health_current"], raw["health_max"], raw["adrenaline"], fmt(a),
                      raw["prayer_packed"], fmt(prayer), raw["gcd_client_start"], raw["gcd_client_end"],
                      g["clock_raw"], fmt(remaining)))
        self.render_npcs(s)

    def build_npc_panel(self, parent):
        controls = tk.Frame(parent, bg=PANEL)
        controls.pack(fill="x", pady=(0, 10))
        tk.Label(controls, text="Filter name or ID:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        tk.Entry(controls, textvariable=self.filter_text, bg="#26374e", fg=INK,
                 insertbackground=INK, relief="flat", font=("Segoe UI", 11), width=22).pack(side="left", padx=8)
        tk.Checkbutton(controls, text="Only NPCs with known HP", variable=self.only_hp,
                       bg=PANEL, fg=INK, activebackground=PANEL, activeforeground=INK,
                       selectcolor=BG, font=("Segoe UI", 9)).pack(side="left", padx=10)
        self.npc_status = tk.Label(parent, text="Waiting for NPCs …", bg=PANEL, fg=MUTED,
                                   font=("Segoe UI", 10), anchor="w")
        self.npc_status.pack(fill="x", pady=(0, 8))
        tables = tk.Frame(parent, bg=PANEL)
        tables.pack(fill="both", expand=True)
        tables.grid_columnconfigure(0, weight=3)
        tables.grid_columnconfigure(1, weight=2)
        tables.grid_rowconfigure(1, weight=1)
        for col, text in [(0, "NPCS IN THE LOADED SCENE"), (1, "RECENTLY OBSERVED HIT SPLATS")]:
            tk.Label(tables, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=col, sticky="w", pady=(0, 7), padx=(0, 12))
        self.npc_tree = self.table(tables, 0, [("name", "Name", 170), ("config", "Type ID", 75),
                                   ("runtime", "Runtime ID", 85), ("health", "HP / max", 120), ("pct", "%", 65)])
        self.npc_tree.tag_configure("wounded", foreground=GOLD)
        self.npc_tree.tag_configure("dead", foreground=RED)
        self.hit_tree = self.table(tables, 1, [("time", "Observed", 100), ("npc", "Recipient", 140),
                                  ("amount", "Amount", 70), ("type", "Type ID", 65)])
        self.hit_tree.tag_configure("hit", foreground=GOLD)
        self.history_note = tk.Label(parent,
            text="History includes an earlier local capture and new live observations." if self.history_path else
                 "History fills with newly observed hits. The table shows observation times.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.history_note.pack(fill="x", pady=(8, 0))

    def table(self, parent, column, columns):
        frame = tk.Frame(parent, bg=PANEL)
        frame.grid(row=1, column=column, sticky="nsew", padx=(0, 12) if column == 0 else (0, 0))
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                            style="Tracker.Treeview", selectmode="browse")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55, stretch=key in ("name", "npc"), anchor="w" if key in ("name", "npc") else "center")
        return tree

    def render_npcs(self, record):
        npcs = record.get("npcs") or {}
        entities = npcs.get("entities")
        if entities is None:
            sync_rows(self.npc_tree, [])
            self.npc_status.config(text="NPC: "+npcs.get("reason", "unavailable"), fg=RED)
        else:
            needle = self.filter_text.get().strip().casefold()
            visible = [e for e in entities if (not self.only_hp.get() or e["health"]["max"] is not None)
                       and (not needle or needle in "%s %s %s" % ((e["name"] or "").casefold(), e["config_id"], e["runtime_id"]))]
            visible.sort(key=lambda e:(e["health"]["percent"] is None, e["health"]["percent"] if e["health"]["percent"] is not None else 100,
                                       (e["name"] or "").casefold(), e["runtime_id"]))
            rows = []
            for e in visible:
                h = e["health"]
                tag = ("dead",) if h["current"] == 0 else (("wounded",) if h["percent"] is not None and h["percent"] < 100 else ())
                rows.append(("npc_%d_%d" % (e["runtime_id"], e["instance_id"]),
                             (e["name"] or "(unnamed)", fmt(e["config_id"]), str(e["runtime_id"]),
                              fmt(h["current"])+" / "+fmt(h["max"]), fmt(h["percent"])), tag))
            sync_rows(self.npc_tree, rows)
            self.npc_status.config(text="Loaded %s · read %s · displayed %d · skipped %s" %
                                   (npcs.get("loaded_count", "—"), npcs.get("read_count", "—"), len(visible), npcs.get("skipped_count", 0)),
                                   fg=GOLD if npcs.get("skipped_count") else GREEN)
        events = record.get("recent_hit_splats", [])
        rows = []
        for i, event in enumerate(reversed(events)):
            stamp = datetime.fromtimestamp(event["observed_at_ms"]/1000).strftime("%H:%M:%S.%f")[:-3]
            rows.append(("hit_"+"_".join(str(v) for v in event_key(event)),
                         (stamp, "%s #%s" % (event.get("name") or "NPC", event["runtime_id"]),
                          fmt(event["amount"]), str(event["type_id"])), ("hit",)))
        sync_rows(self.hit_tree, rows)

    def set_raw(self, value):
        self.raw_text.config(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", value)
        self.raw_text.config(state="disabled")

    def copy_json(self):
        if self.latest:
            self.root.clipboard_clear()
            self.root.clipboard_append(json.dumps(self.latest, ensure_ascii=False, indent=2))

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)
        self.root.destroy()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, help="Import observed hits from an earlier local JSONL capture")
    parser.add_argument("--backend", choices=("c", "python"), help="Default: C collector (RS3_BACKEND overrides)")
    parser.add_argument("--native-hz", type=int, default=100, help="Native collector frequency, 1..1000 Hz")
    args = parser.parse_args()
    if not 1 <= args.native_hz <= 1000:
        parser.error("native-hz must be 1..1000")
    root = tk.Tk()
    Tracker(root, args.history, args.backend, args.native_hz)
    root.mainloop()
