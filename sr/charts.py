"""Schlichte Diagramme für tkinter: Säulen mit Soll-Markierung und gestapelte Balken.

Gestaltung: dünne Markierungen, Haarlinien-Raster, Text nie in Datenfarbe,
Tooltip beim Überfahren, jede Grafik auch als Tabelle umschaltbar.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES1 = "#2a78d6"        # Datenbank / eigene Begriffe
DEEMPH = "#cfcdc6"         # automatisch erzeugte Begriffe (zurückgenommen)
FONT = "Segoe UI"


def de(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def nice_max(v: float) -> tuple[float, float]:
    """Runde Obergrenze und Schrittweite für eine Achse."""
    if v <= 0:
        return 1, 0.25
    raw = v / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return step * math.ceil(v / step), step


class ChartCard(tk.Frame):
    """Rahmen mit Titel, Erklärung, Legende, Diagramm und Tabellenansicht."""

    def __init__(self, master, title: str, subtitle: str, table_cols: list[tuple[str, int]]):
        super().__init__(master, background=SURFACE, highlightbackground=GRID, highlightthickness=1)
        head = tk.Frame(self, background=SURFACE)
        head.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(head, text=title, font=(FONT, 11, "bold"), fg=INK, bg=SURFACE).pack(side="left")
        self.btn = ttk.Button(head, text="Als Tabelle", command=self.toggle, width=12)
        self.btn.pack(side="right")
        self.sub = tk.Label(self, text=subtitle, font=(FONT, 9), fg=MUTED, bg=SURFACE, anchor="w", justify="left")
        self.sub.pack(fill="x", padx=14)
        self.insight = tk.Label(self, text="", font=(FONT, 9), fg=INK2, bg=SURFACE, anchor="w", justify="left")
        self.insight.pack(fill="x", padx=14, pady=(4, 0))
        self.bind("<Configure>", lambda e: [lbl.configure(wraplength=max(100, e.width - 30))
                                            for lbl in (self.sub, self.insight)])
        self.legend = tk.Frame(self, background=SURFACE)
        self.legend.pack(fill="x", padx=14, pady=(6, 0))
        self.body = tk.Frame(self, background=SURFACE)
        self.body.pack(fill="both", expand=True, padx=8, pady=(4, 10))
        self.canvas = tk.Canvas(self.body, background=SURFACE, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("tip"))
        tf = tk.Frame(self.body, background=SURFACE)
        self.table = ttk.Treeview(tf, columns=[c for c, _ in table_cols], show="headings")
        for c, w in table_cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=w, anchor="e" if w < 120 else "w")
        sb = ttk.Scrollbar(tf, command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        self.table.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.table_frame = tf
        self.showing_table = False
        self.rows: list[dict] = []
        self.hits: list[tuple[float, float, float, float, list[str]]] = []

    # -- Legende
    def set_legend(self, items: list[tuple[str, str, str]]):
        """items: (art 'box'|'tick', farbe, text)"""
        for w in self.legend.winfo_children():
            w.destroy()
        for kind, color, text in items:
            c = tk.Canvas(self.legend, width=18, height=12, background=SURFACE, highlightthickness=0)
            if kind == "box":
                c.create_rectangle(3, 2, 15, 12, fill=color, outline="")
            else:
                c.create_line(1, 6, 17, 6, fill=color, width=2)
            c.pack(side="left")
            tk.Label(self.legend, text=text, font=(FONT, 9), fg=INK2, bg=SURFACE).pack(side="left", padx=(2, 14))

    # -- Tabelle
    def toggle(self):
        self.showing_table = not self.showing_table
        if self.showing_table:
            self.canvas.pack_forget()
            self.table_frame.pack(fill="both", expand=True)
            self.btn.configure(text="Als Diagramm")
        else:
            self.table_frame.pack_forget()
            self.canvas.pack(fill="both", expand=True)
            self.btn.configure(text="Als Tabelle")
            self.draw()

    def fill_table(self, values: list[tuple]):
        self.table.delete(*self.table.get_children())
        for v in values:
            self.table.insert("", "end", values=v)

    # -- Tooltip
    def on_motion(self, ev):
        cv = self.canvas
        x, y = cv.canvasx(ev.x), cv.canvasy(ev.y)
        cv.delete("tip")
        for x0, y0, x1, y1, lines in self.hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.draw_tip(x, y, lines)
                return

    def draw_tip(self, x, y, lines):
        cv = self.canvas
        t = cv.create_text(0, 0, text="\n".join(lines), anchor="nw", font=(FONT, 9), fill=INK, tags="tip")
        bx0, by0, bx1, by1 = cv.bbox(t)
        w, h = bx1 - bx0 + 16, by1 - by0 + 12
        vis_w = cv.winfo_width()
        top = cv.canvasy(0)
        tx = x + 14 if x + 14 + w < vis_w else x - 14 - w
        ty = max(top + 4, y - h - 10)
        r = cv.create_rectangle(tx, ty, tx + w, ty + h, fill="white", outline=BASELINE, tags="tip")
        cv.coords(t, tx + 8, ty + 6)
        cv.tag_raise(t, r)

    def draw(self):
        raise NotImplementedError


class CompareColumns(ChartCard):
    """Säulen = Ist-Anteil in %, dunkle Markierung = erwarteter Anteil."""

    def __init__(self, master, title, subtitle, cat_name, expected_name, tooltip_unit=""):
        super().__init__(master, title, subtitle,
                         [(cat_name, 110), ("Anzahl", 70), ("Anteil %", 80), ("Erwartet %", 85), ("Abweichung", 85)])
        self.cat_name, self.expected_name, self.unit = cat_name, expected_name, tooltip_unit
        self.set_legend([("box", SERIES1, "Datenbank"), ("tick", INK, expected_name)])

    def set_rows(self, rows: list[dict], insight: str):
        self.rows = rows
        self.insight.configure(text=insight)
        self.fill_table([(r["label"], r["count"], de(r["share"]), de(r["expected"]),
                          ("+" if r["share"] >= r["expected"] else "") + de(r["share"] - r["expected"])) for r in rows])
        self.draw()

    def draw(self):
        cv = self.canvas
        cv.delete("all")
        self.hits = []
        if not self.rows:
            return
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        left, right, top, bottom = 44, 10, 10, 26
        pw, ph = W - left - right, H - top - bottom
        vmax, step = nice_max(max(max(r["share"], r["expected"]) for r in self.rows) * 1.05)
        y = lambda v: top + ph - ph * v / vmax
        v = 0.0
        while v <= vmax + 1e-9:
            cv.create_line(left, y(v), W - right, y(v), fill=GRID if v else BASELINE)
            cv.create_text(left - 6, y(v), text=f"{de(v, 0 if step >= 1 else 1)} %", anchor="e",
                           font=(FONT, 8), fill=MUTED)
            v += step
        n = len(self.rows)
        band = pw / n
        bw = max(4, min(24, band * 0.62))
        for k, r in enumerate(self.rows):
            cx = left + band * (k + 0.5)
            if r["share"] > 0:
                cv.create_rectangle(cx - bw / 2, y(r["share"]), cx + bw / 2, y(0), fill=SERIES1, outline="")
            ey = y(r["expected"])
            cv.create_line(cx - bw / 2 - 4, ey, cx + bw / 2 + 4, ey, fill=INK, width=2)
            cv.create_text(cx, top + ph + 6, text=r["label"], anchor="n", font=(FONT, 9), fill=INK2)
            diff = r["share"] - r["expected"]
            self.hits.append((cx - band / 2, top, cx + band / 2, top + ph + 22, [
                f"{self.cat_name} {r['label']}{self.unit}",
                f"Datenbank: {de(r['share'])} %  ({r['count']})",
                f"{self.expected_name}: {de(r['expected'])} %",
                f"Abweichung: {'+' if diff >= 0 else ''}{de(diff)} Prozentpunkte",
            ]))


class StackedBars(ChartCard):
    """Waagerechte Balken je Thema: eigene (Farbe) + automatisch (grau), senkrechte Ziellinie."""

    ROW = 24
    BAR = 14

    def __init__(self, master, title, subtitle):
        super().__init__(master, title, subtitle,
                         [("Thema", 160), ("Gesamt", 70), ("Eigene", 70), ("Automatisch", 85), ("bis Ziel", 70)])
        self.set_legend([("box", SERIES1, "Eigene"), ("box", DEEMPH, "Automatisch"), ("tick", INK, "Ziel je Thema")])
        sb = ttk.Scrollbar(self.body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack_forget()
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb = sb
        self.target = 100
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

    def toggle(self):
        super().toggle()
        if self.showing_table:
            self.vsb.pack_forget()
        else:
            self.canvas.pack_forget()
            self.vsb.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)

    def set_rows(self, rows: list[dict], target: int, insight: str):
        self.rows, self.target = rows, target
        self.insight.configure(text=insight)
        self.fill_table([(r["label"], r["count"], r["own"], r["auto"], max(0, target - r["count"])) for r in rows])
        self.draw()

    def draw(self):
        cv = self.canvas
        cv.delete("all")
        self.hits = []
        if not self.rows:
            return
        W = cv.winfo_width()
        if W < 80:
            return
        label_w = 130
        top = 22
        right = 46
        pw = W - label_w - right
        vmax, step = nice_max(max(max(r["count"] for r in self.rows), self.target) * 1.05)
        x = lambda v: label_w + pw * v / vmax
        height = top + len(self.rows) * self.ROW + 6
        v = 0.0
        while v <= vmax + 1e-9:
            cv.create_line(x(v), top - 4, x(v), height, fill=GRID if v else BASELINE)
            cv.create_text(x(v), top - 8, text=f"{int(v)}", anchor="s", font=(FONT, 8), fill=MUTED)
            v += step
        for k, r in enumerate(self.rows):
            cy = top + k * self.ROW + self.ROW / 2
            cv.create_text(label_w - 8, cy, text=r["label"], anchor="e", font=(FONT, 9), fill=INK2)
            y0, y1 = cy - self.BAR / 2, cy + self.BAR / 2
            x_own = x(r["own"])
            if r["own"]:
                cv.create_rectangle(x(0), y0, x_own, y1, fill=SERIES1, outline="")
            if r["auto"]:
                gap = 2 if r["own"] else 0          # 2 px Abstand zwischen den Teilen
                cv.create_rectangle(x_own + gap, y0, x(r["count"]), y1, fill=DEEMPH, outline="")
            cv.create_text(x(r["count"]) + 6, cy, text=str(r["count"]), anchor="w", font=(FONT, 8), fill=MUTED)
            rest = max(0, self.target - r["count"])
            self.hits.append((0, cy - self.ROW / 2, W, cy + self.ROW / 2, [
                r["label"],
                f"{r['count']} Begriffe: {r['own']} eigene, {r['auto']} automatisch",
                f"Ziel {self.target}: " + (f"noch {rest} fehlen" if rest else "erreicht"),
            ]))
        xt = x(self.target)
        cv.create_line(xt, top - 4, xt, height, fill=INK, width=2)
        cv.configure(scrollregion=(0, 0, W, height))
