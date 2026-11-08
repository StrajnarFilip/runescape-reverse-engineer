"""Local desktop display for the read-only RS3 memory reader (no dependencies)."""
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk

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
        self.detail = tk.Label(self.frame, text="Čakam na branje …", fg=MUTED,
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

class Tracker:
    def __init__(self, root):
        self.root = root
        root.title("RS3 Live Tracker · neposredno branje pomnilnika")
        root.geometry("980x790")
        root.minsize(880, 740)
        root.configure(bg=BG)
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=1)
        self.latest = self.received_at = None
        self.sequence = 0
        logs = Path(__file__).resolve().parent / "logs"
        logs.mkdir(exist_ok=True)
        self.log_path = logs / (datetime.now().strftime("capture-%Y%m%d-%H%M%S-")+str(os.getpid())+".jsonl")

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 12))
        tk.Label(header, text="RS3  /  LIVE TRACKER", fg=INK, bg=BG,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        self.status = tk.Label(header, text="POVEZOVANJE", fg=GOLD, bg=PANEL,
                               padx=12, pady=6, font=("Segoe UI", 10, "bold"))
        self.status.pack(side="right")
        self.source = tk.Label(root, text="Preverjam različico odjemalca …", bg=BG,
                               fg=MUTED, font=("Segoe UI", 10), anchor="w")
        self.source.pack(fill="x", padx=24, pady=(0, 12))

        grid = tk.Frame(root, bg=BG)
        grid.pack(fill="x", padx=18)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="meters")
        self.meters = {}
        for i, (key, title, colour) in enumerate([
            ("hp", "HEALTH / LIFE POINTS", RED), ("prayer", "PRAYER", BLUE),
            ("adren", "ADRENALIN", GOLD), ("gcd", "GLOBAL COOLDOWN", GREEN)]):
            meter = self.meters[key] = Meter(grid, title, colour)
            meter.frame.grid(row=i//2, column=i%2, padx=6, pady=6, sticky="nsew")

        proof = tk.Frame(root, bg=PANEL, padx=18, pady=12)
        proof.pack(fill="both", expand=True, padx=24, pady=(14, 0))
        tk.Label(proof, text="IZVOR IN SUROVE VREDNOSTI", fg=MUTED, bg=PANEL,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.timing = tk.Label(proof, text="—", fg=GREEN, bg=PANEL,
                               font=("Consolas", 10), anchor="w")
        self.timing.pack(fill="x", pady=(6, 6))
        self.raw_text = tk.Text(proof, height=6, bg=PANEL, fg=INK, relief="flat",
                                font=("Consolas", 10), wrap="none", padx=0, pady=0,
                                highlightthickness=0, state="disabled")
        self.raw_text.pack(fill="both", expand=True)
        self.pending = tk.Label(root, text="NPC ID / NPC HP / hit splati / Telos: branje še ni potrjeno.",
                                bg=BG, fg=GOLD, font=("Segoe UI", 10), anchor="w")
        self.pending.pack(fill="x", padx=24, pady=(10, 4))
        footer = tk.Frame(root, bg=BG)
        footer.pack(fill="x", padx=24, pady=(6, 18))
        for text, command in [("Odpri mapo z dnevniki", lambda: os.startfile(str(logs))),
                              ("Kopiraj trenutni JSON", self.copy_json)]:
            tk.Button(footer, text=text, command=command, bg="#26374e", fg=INK,
                      activebackground="#344b68", activeforeground=INK, relief="flat",
                      padx=12, pady=8, font=("Segoe UI", 9)).pack(side="left", padx=(0, 10))
        tk.Label(footer, text="20 Hz · samo branje", fg=MUTED, bg=BG,
                 font=("Segoe UI", 9)).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = threading.Thread(target=self.collect, daemon=True)
        self.worker.start()
        root.after(50, self.refresh)

    def collect(self):
        telemetry = Telemetry()
        try:
            with self.log_path.open("w", encoding="utf-8", buffering=1) as log:
                deadline = time.perf_counter()
                while not self.stop.is_set():
                    record = telemetry.sample(True)
                    self.sequence += 1
                    record["sample_sequence"] = self.sequence
                    log.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n")
                    self.publish(record)
                    deadline += .05
                    delay = deadline-time.perf_counter()
                    if delay < -.5:
                        deadline = time.perf_counter()
                    self.stop.wait(max(0, delay))
        except Exception as error:
            self.publish(telemetry.unavailable("Tracker: "+str(error)))
        finally:
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
            age = (time.perf_counter()-self.received_at)*1000
            stamp = datetime.fromtimestamp(self.latest["timestamp_ms"]/1000).strftime("%H:%M:%S.%f")[:-3]
            self.timing.config(text="Branje %s  |  starost %.0f ms  |  vzorec #%s  |  %.2f ms/bralni cikel" %
                               (stamp, age, self.latest.get("sample_sequence", "—"), self.latest.get("read_duration_ms", 0)))
            if age > 1500:
                self.status.config(text="ZASTARELO", fg=RED)
                for meter in self.meters.values():
                    meter.set("—", 0, "Zajem se ne osvežuje")
        self.root.after(50, self.refresh)

    def render(self, s):
        available = s["status"] != "unavailable"
        stalled = s.get("freshness", {}).get("clock_stalled_ms", 0) > 2000
        self.status.config(text="ŽIVO · POMNILNIK" if available and not stalled else "NI SVEŽIH PODATKOV",
                           fg=GREEN if available and not stalled else RED)
        digest = s.get("executable_sha256", "")
        self.source.config(text="rs2client.exe  |  PID %s  |  build %s  |  SHA-256 %s…%s" %
                           (s.get("pid", "—"), s.get("client_version", "—"), digest[:8], digest[-6:]) if available
                           else "Čakam na podprt odjemalec: "+s.get("reason", "neznan razlog"))
        if not available or stalled:
            for meter in self.meters.values():
                meter.set("—", 0, "Nedostopno" if not available else "Odjemalčev števec se ne premika")
            self.set_raw(s.get("reason", "Odjemalčev števec se ne premika"))
            return
        h, p, a, g = s["health"], s["prayer"], s["adrenaline_percent"]["value"], s["gcd"]
        hp, prayer = h["current"]["value"], p["current"]["value"]
        self.meters["hp"].set(fmt(hp)+" / "+fmt(h["max"]), (hp or 0)/(h["max"] or 1), "VARP 13537 / 13538 · neposredni vrednosti")
        self.meters["prayer"].set(fmt(p["display_current"])+" / "+fmt(p["max"]), (prayer or 0)/(p["max"] or 1),
                                  "Natančno %s · maksimum iz prayer levela" % fmt(prayer))
        self.meters["adren"].set(fmt(a)+" %", (a or 0)/100, "VARP 679 ÷ 10 · neposredna vrednost")
        remaining = g["remaining_ms"]
        self.meters["gcd"].set("—" if remaining is None else ("PRIPRAVLJEN" if remaining == 0 else "%.2f s" % (remaining/1000)),
                                (remaining or 0)/(g["duration_ms"] or 1),
                                "VARC 2091 / 2092 · tick 20 ms" if remaining is not None else g["reason"])
        raw = s["raw_variables"]
        self.set_raw("VARP 13537 HP       = %-8s  VARP 13538 max HP = %s\n"
                     "VARP 679 adrenalin  = %-8s  / 10 = %s %%\n"
                     "VARP 3274 prayer    = %-8s  bits 0..14 / 10 = %s\n"
                     "VARC 2091 GCD start = %-8s  VARC 2092 end = %s\n"
                     "Client logic clock = %-8s  GCD preostanek = %s ms\n"
                     "Svežina: neposredno branje; čas zadnje strežniške posodobitve ni znan." %
                     (raw["health_current"], raw["health_max"], raw["adrenaline"], fmt(a),
                      raw["prayer_packed"], fmt(prayer), raw["gcd_client_start"], raw["gcd_client_end"],
                      g["clock_raw"], fmt(remaining)))

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
    root = tk.Tk()
    Tracker(root)
    root.mainloop()
