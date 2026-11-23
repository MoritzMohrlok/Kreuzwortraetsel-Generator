"""Oberfläche (tkinter): Datenbank pflegen und Rätsel erzeugen."""
from __future__ import annotations

import json
import os
import queue
import random
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import charts, generator, stats
from . import claude_check as cc
from types import SimpleNamespace

from .einsendungen import check, fetch_text, parse_submissions, to_entries

from .layout import A5_AVAIL, FORMATS, fit_cell, page_ops
from .pdf import export_pdf
from .service import AVOID_TAG, pick_solution, preferred_words, theme_pool, word_pool
from .words import QUELLEN, SHORT_MAX, Entry, all_tags, classify, load_csv, normalize, save_csv, split_tags
import datetime

DEFAULT_SETTINGS = {
    "title": "Schwedenrätsel", "w": 13, "h": 15, "cell": 12.0, "time": 15,
    "topics": [], "filler": True, "sol_mode": "auto", "sol_word": "", "sol_min": 6, "sol_max": 10,
    "show_key": False, "pdf_key": True, "use_auto": True, "max_theme": False, "runs": 100,
    "fixed_ids": [], "highlight": "", "fmt": "a4",
    "import_source": "", "import_tags": "", "imported_keys": [],
    "dash_source": "alle", "dash_target": 100, "skip_bad": True,
}
PRUEF_FILTER = ("alle", "mit Hinweisen", "ungeprüft / geändert", "★1–2")


class App(tk.Tk):
    def __init__(self, csv_path: str):
        super().__init__()
        self.title("Schwedenrätsel-Generator")
        self.geometry("1360x880")
        self.minsize(1000, 650)
        self.app_dir = os.path.dirname(os.path.abspath(csv_path))
        self.settings_path = os.path.join(self.app_dir, "einstellungen.json")
        self.settings = dict(DEFAULT_SETTINGS)
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass

        self.path = csv_path
        self.entries: list[Entry] = []
        self.dirty = False
        self.edit_id = None
        self.puzzle = None
        self.gen_thread = None
        self.stop_event = None
        self.msgs: queue.Queue = queue.Queue()
        self.pending_import_keys: set[str] = set()
        self.review = cc.ReviewStore(os.path.join(self.app_dir, "pruefung.json"))
        self.review_job = None                # laufende Datenbank-Prüfung (SimpleNamespace)

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=22)
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.tab_db = ttk.Frame(self.nb, padding=8)
        self.tab_gen = ttk.Frame(self.nb, padding=8)
        self.tab_dash = tk.Frame(self.nb, background="#f4f3f0", padx=10, pady=10)
        self.nb.add(self.tab_db, text="  Datenbank  ")
        self.nb.add(self.tab_gen, text="  Rätsel erstellen  ")
        self.nb.add(self.tab_dash, text="  Auswertung  ")
        self._build_db_tab()
        self._build_gen_tab()
        self._build_dash_tab()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_dash())

        self.bind_all("<Control-s>", lambda e: self.save())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if os.path.exists(csv_path):
            self.load(csv_path)
        else:
            self.update_status()

    # ================================================================ Datenbank
    def _build_db_tab(self):
        f = self.tab_db
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Öffnen…", command=self.open_dialog).pack(side="left")
        ttk.Button(bar, text="Speichern (Strg+S)", command=self.save).pack(side="left", padx=4)
        ttk.Button(bar, text="Speichern unter…", command=self.save_as).pack(side="left")
        ttk.Button(bar, text="Einsendungen holen…", command=self.import_dialog).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="Mit Claude prüfen…", command=self.db_review_dialog).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="Claude-Zugang…", command=self.key_dialog).pack(side="left", padx=4)
        self.status_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.status_var, foreground="#555").pack(side="right")

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)

        # Eingabemaske
        form = ttk.LabelFrame(body, text="Neuer Begriff", padding=10)
        form.pack(side="left", fill="y", padx=(0, 10))
        self.form = form
        self.v_frage, self.v_antwort, self.v_tags = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(form, text="Frage / Hinweis (kurz halten!)").pack(anchor="w")
        self.e_frage = ttk.Entry(form, textvariable=self.v_frage, width=38)
        self.e_frage.pack(fill="x", pady=(0, 8))
        ttk.Label(form, text="Antwort").pack(anchor="w")
        ttk.Entry(form, textvariable=self.v_antwort, width=38).pack(fill="x")
        self.preview_var = tk.StringVar(value=" ")
        ttk.Label(form, textvariable=self.preview_var, foreground="#555", wraplength=280).pack(anchor="w", pady=(2, 8))
        self.v_antwort.trace_add("write", lambda *a: self.update_preview())
        ttk.Label(form, text="Tags / Themen (durch Komma getrennt)").pack(anchor="w")
        ttk.Entry(form, textvariable=self.v_tags, width=38).pack(fill="x")
        ttk.Label(form, text="Vorhandene Tags (Doppelklick fügt hinzu):", foreground="#555").pack(anchor="w", pady=(8, 2))
        lf = ttk.Frame(form)
        lf.pack(fill="both", expand=True)
        self.tag_list = tk.Listbox(lf, height=12, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(lf, command=self.tag_list.yview)
        self.tag_list.configure(yscrollcommand=sb.set)
        self.tag_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tag_list.bind("<Double-Button-1>", self.add_tag_from_list)
        btns = ttk.Frame(form)
        btns.pack(fill="x", pady=(10, 0))
        self.btn_add = ttk.Button(btns, text="Hinzufügen", style="Accent.TButton", command=self.submit_entry)
        self.btn_add.pack(side="left")
        self.btn_cancel = ttk.Button(btns, text="Abbrechen", command=self.reset_form)
        for child in form.winfo_children():
            if isinstance(child, ttk.Entry):
                child.bind("<Return>", lambda e: self.submit_entry())

        # Tabelle
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(right)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Suchen:").pack(side="left")
        self.v_search = tk.StringVar()
        self.search_entry = ttk.Entry(top, textvariable=self.v_search, width=30)
        self.search_entry.pack(side="left", padx=(4, 10))
        self.v_search.trace_add("write", lambda *a: self.refresh_table())
        ttk.Label(top, text="Tag:").pack(side="left")
        self.v_tagfilter = tk.StringVar(value="Alle Tags")
        self.cb_tag = ttk.Combobox(top, textvariable=self.v_tagfilter, state="readonly", width=24)
        self.cb_tag.pack(side="left", padx=4)
        self.cb_tag.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        ttk.Label(top, text="Quelle:").pack(side="left", padx=(6, 0))
        self.v_quelle = tk.StringVar(value="alle")
        cbq = ttk.Combobox(top, textvariable=self.v_quelle, state="readonly", width=12,
                           values=["alle"] + list(QUELLEN.values()))
        cbq.pack(side="left", padx=4)
        cbq.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        ttk.Label(top, text="Prüfung:").pack(side="left", padx=(6, 0))
        self.v_pruef = tk.StringVar(value="alle")
        cbp = ttk.Combobox(top, textvariable=self.v_pruef, state="readonly", width=18, values=PRUEF_FILTER)
        cbp.pack(side="left", padx=4)
        cbp.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        self.count_var = tk.StringVar()
        ttk.Label(top, textvariable=self.count_var, foreground="#555").pack(side="left", padx=10)
        ttk.Button(top, text="Löschen", command=self.delete_selected).pack(side="right")
        ttk.Button(top, text="Bearbeiten", command=self.edit_selected).pack(side="right", padx=4)

        cols = ("id", "frage", "antwort", "wort", "laenge", "tags", "quelle", "erstellt", "pruefung")
        heads = ("ID", "Frage", "Antwort", "Rätsel-Buchstaben", "Länge", "Tags", "Quelle", "Erstellt",
                 "Claude-Prüfung")
        widths = (50, 280, 140, 140, 50, 220, 90, 85, 200)
        tf = ttk.Frame(right)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=w, anchor="w", stretch=c in ("frage", "tags"))
        ysb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self.edit_selected())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.tag_configure("auto", foreground="#777")       # automatisch erzeugte Begriffe grau
        self.tree.tag_configure("schwach", foreground="#b00020")  # von Claude mit ★1 bewertet oder 'meiden'
        self.sort_col, self.sort_rev = "id", True

    def load(self, path):
        try:
            self.entries = load_csv(path)
        except Exception as e:      # noqa: BLE001 – Fehler dem Nutzer zeigen
            messagebox.showerror("Fehler", f"Datei konnte nicht gelesen werden:\n{e}")
            return
        self.path = path
        self.dirty = False
        self.reset_form()
        self.refresh_db()
        self.update_status()

    def open_dialog(self):
        if not self.confirm_discard():
            return
        p = filedialog.askopenfilename(title="Wortdatenbank öffnen", initialdir=self.app_dir,
                                       filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")])
        if p:
            self.load(p)

    def save(self):
        if not self.path:
            return self.save_as()
        try:
            save_csv(self.path, self.entries)
        except Exception as e:      # noqa: BLE001
            messagebox.showerror("Fehler", f"Speichern fehlgeschlagen:\n{e}")
            return False
        self.dirty = False
        if self.pending_import_keys:          # übernommene Einsendungen erst nach dem Speichern als erledigt merken
            self.settings["imported_keys"] = sorted(set(self.settings.get("imported_keys", []))
                                                    | self.pending_import_keys)
            self.pending_import_keys = set()
            self.store_settings()
        self.update_status()
        return True

    def save_as(self):
        p = filedialog.asksaveasfilename(title="Speichern unter", initialdir=self.app_dir, defaultextension=".csv",
                                         initialfile=os.path.basename(self.path or "woerter.csv"),
                                         filetypes=[("CSV-Dateien", "*.csv")])
        if not p:
            return False
        self.path = p
        return self.save()

    def confirm_discard(self):
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel("Ungespeicherte Änderungen", "Änderungen an der Datenbank speichern?")
        if ans is None:
            return False
        return self.save() if ans else True

    def on_close(self):
        if not self.confirm_discard():
            return
        if self.stop_event:
            self.stop_event.set()
        self.store_settings()
        self.destroy()

    def update_status(self):
        name = os.path.basename(self.path) if self.path else "keine Datei"
        n_auto = sum(1 for e in self.entries if e.quelle == "auto")
        self.status_var.set(f"{name} · {len(self.entries)} Begriffe ({len(self.entries) - n_auto} eigene, {n_auto} automatisch)"
                            + (" · UNGESPEICHERT" if self.dirty else ""))
        self.title(("* " if self.dirty else "") + "Schwedenrätsel-Generator")

    def set_dirty(self):
        self.dirty = True
        self.update_status()

    def refresh_db(self):
        tags = all_tags(self.entries)
        self.cb_tag["values"] = ["Alle Tags", "ohne Tag"] + [f"{t} ({n})" for t, n in tags]
        if self.v_tagfilter.get() not in self.cb_tag["values"]:
            self.v_tagfilter.set("Alle Tags")
        self.tag_list.delete(0, "end")
        for t, n in tags:
            self.tag_list.insert("end", t)
        self.refresh_table()
        self.refresh_topics()
        self.refresh_dash()

    def refresh_table(self):
        q = self.v_search.get().strip().lower()
        tf = self.v_tagfilter.get()
        tag = None if tf == "Alle Tags" else ("" if tf == "ohne Tag" else tf.rsplit(" (", 1)[0])
        qv = self.v_quelle.get()
        pf = self.v_pruef.get()
        rv = self.review

        def pruef_ok(e):
            if pf == "alle":
                return True
            r = rv.get(e)
            if pf == "ungeprüft / geändert":
                return r is None
            if r is None:
                return False
            if pf == "★1–2":
                return r["bewertung"] <= 2
            return not r.get("erledigt") and (bool(r.get("probleme")) or cc.has_suggestion(r))

        rows = [e for e in self.entries
                if (not q or q in e.frage.lower() or q in e.antwort.lower())
                and (tag is None or (not e.tags if tag == "" else tag in e.tags))
                and (qv == "alle" or e.quelle_text == qv) and pruef_ok(e)]

        def pruef_key(e):
            r = rv.get(e)
            return r["bewertung"] if r else 0

        key = {"id": lambda e: e.id, "frage": lambda e: e.frage.lower(), "antwort": lambda e: e.antwort.lower(),
               "wort": lambda e: e.wort, "laenge": lambda e: len(e.wort), "tags": lambda e: ",".join(e.tags),
               "quelle": lambda e: e.quelle, "erstellt": lambda e: e.erstellt, "pruefung": pruef_key}[self.sort_col]
        rows.sort(key=key, reverse=self.sort_rev)
        self.tree.delete(*self.tree.get_children())
        for e in rows:
            bad = rv.is_bad(e) or AVOID_TAG in e.tags
            self.tree.insert("", "end", iid=str(e.id), tags=(e.quelle,) + (("schwach",) if bad else ()),
                             values=(e.id, e.frage, e.antwort, e.wort, len(e.wort), ", ".join(e.tags), e.quelle_text,
                                     e.erstellt, rv.status(e)))
        self.count_var.set(f"{len(rows)} von {len(self.entries)}" if len(rows) != len(self.entries) else f"{len(rows)} Einträge")

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        self.refresh_table()

    def update_preview(self):
        w = normalize(self.v_antwort.get())
        if not w:
            self.preview_var.set(" ")
            return
        dup = [e for e in self.entries if e.wort == w and e.id != self.edit_id]
        txt = f"Im Rätsel: {' '.join(w)}  ({len(w)} Buchstaben)"
        if len(w) <= SHORT_MAX:
            txt += " – wird automatisch als Kurzwort markiert"
        if dup:
            txt += "\nSchon vorhanden: " + " / ".join(e.frage for e in dup[:3])
        self.preview_var.set(txt)

    def add_tag_from_list(self, _event=None):
        sel = self.tag_list.curselection()
        if not sel:
            return
        tag = self.tag_list.get(sel[0])
        cur = split_tags(self.v_tags.get())
        if tag not in cur:
            cur.append(tag)
        self.v_tags.set(", ".join(cur))

    def selected_entries(self):
        ids = {int(i) for i in self.tree.selection()}
        return [e for e in self.entries if e.id in ids]

    def edit_selected(self):
        sel = self.selected_entries()
        if not sel:
            return
        e = sel[0]
        self.edit_id = e.id
        self.v_frage.set(e.frage)
        self.v_antwort.set(e.antwort)
        self.v_tags.set(", ".join(e.tags))
        self.form.configure(text=f"Begriff #{e.id} bearbeiten ({e.quelle_text})"
                            + (" – wird danach als „manuell“ geführt" if e.quelle == "auto" else ""))
        self.btn_add.configure(text="Änderung übernehmen")
        self.btn_cancel.pack(side="left", padx=6)
        self.e_frage.focus_set()

    def reset_form(self):
        self.edit_id = None
        self.v_frage.set("")
        self.v_antwort.set("")
        self.form.configure(text="Neuer Begriff")
        self.btn_add.configure(text="Hinzufügen")
        self.btn_cancel.pack_forget()
        self.update_preview()

    def submit_entry(self):
        frage, antwort = self.v_frage.get().strip(), self.v_antwort.get().strip()
        if not frage or not antwort:
            messagebox.showinfo("Hinweis", "Bitte Frage und Antwort eingeben.")
            return
        if len(normalize(antwort)) < 2:
            messagebox.showinfo("Hinweis", "Die Antwort braucht mindestens 2 Buchstaben.")
            return
        tags = classify(split_tags(self.v_tags.get()), antwort)
        if self.edit_id is not None:
            e = next(x for x in self.entries if x.id == self.edit_id)
            changed = (e.frage, e.antwort, e.tags) != (frage, antwort, tags)
            e.frage, e.antwort, e.tags = frage, antwort, tags
            if changed and e.quelle == "auto":
                e.quelle = "manuell"            # von Hand geprüft/korrigiert
            new_id = e.id
        else:
            new_id = max((x.id for x in self.entries), default=0) + 1
            self.entries.append(Entry(new_id, frage, antwort, tags, "manuell", datetime.date.today().isoformat()))
        keep_tags = self.v_tags.get() if self.edit_id is None else ""
        self.reset_form()
        self.v_tags.set(keep_tags)            # Tags für die nächste Eingabe stehen lassen
        self.set_dirty()
        self.refresh_db()
        if self.tree.exists(str(new_id)):
            self.tree.see(str(new_id))
            self.tree.selection_set(str(new_id))
        self.e_frage.focus_set()

    def delete_selected(self):
        sel = self.selected_entries()
        if not sel:
            return
        names = ", ".join(e.antwort for e in sel[:5]) + (" …" if len(sel) > 5 else "")
        if not messagebox.askyesno("Löschen", f"{len(sel)} Begriff(e) löschen?\n{names}"):
            return
        ids = {e.id for e in sel}
        self.entries = [e for e in self.entries if e.id not in ids]
        if self.edit_id in ids:
            self.reset_form()
        self.set_dirty()
        self.refresh_db()

    # ================================================================ Rätsel
    # ---------------------------------------------------------------- Einsendungen (Google-Formular / CSV)
    def import_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Einsendungen holen")
        dlg.geometry("1100x620")
        dlg.transient(self)
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Google-Sheets-Link oder CSV-Datei:").grid(row=0, column=0, sticky="w")
        v_src = tk.StringVar(value=self.settings.get("import_source", ""))
        ttk.Entry(frm, textvariable=v_src, width=95).grid(row=0, column=1, sticky="ew", padx=4)

        def pick_file():
            p = filedialog.askopenfilename(parent=dlg, title="CSV-Datei mit Einsendungen", initialdir=self.app_dir,
                                           filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")])
            if p:
                v_src.set(p)
                load()

        ttk.Button(frm, text="Datei…", command=pick_file).grid(row=0, column=2)
        ttk.Button(frm, text="Laden", style="Accent.TButton", command=lambda: load()).grid(row=0, column=3, padx=(4, 0))
        ttk.Label(frm, text="Zusätzliche Tags für alle:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        v_tags = tk.StringVar(value=self.settings.get("import_tags", ""))
        ttk.Entry(frm, textvariable=v_tags, width=30).grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(frm, foreground="#555", wraplength=1050, justify="left", text=(
            "Erkannt werden die Spalten Frage, Antwort, Tags/Thema, Name und Zeitstempel (Reihenfolge egal). "
            "Neue Einsendungen sind vorausgewählt. Mit Strg+Klick änderst du die Auswahl. Übernommene Begriffe "
            "bekommen die Quelle „Einsendung“ und lassen sich danach wie alle anderen bearbeiten.")).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=6)

        cols = ("status", "frage", "antwort", "wort", "tags", "name", "datum", "hinweis")
        heads = ("Status", "Frage", "Antwort", "Rätsel-Buchstaben", "Tags", "Name", "Datum", "Hinweis")
        widths = (90, 230, 120, 120, 130, 90, 85, 260)
        tf = ttk.Frame(frm)
        tf.grid(row=3, column=0, columnspan=4, sticky="nsew")
        tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w", stretch=c in ("frage", "hinweis"))
        sb = ttk.Scrollbar(tf, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("grau", foreground="#999")
        tree.tag_configure("warn", foreground="#a05a00")
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(3, weight=1)

        bottom = ttk.Frame(frm)
        bottom.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        info = tk.StringVar(value="Link einfügen oder Datei wählen, dann „Laden“.")
        ttk.Label(bottom, textvariable=info).pack(side="left")
        btn_ok = ttk.Button(bottom, text="Ausgewählte übernehmen", style="Accent.TButton", state="disabled",
                            command=lambda: take())
        ttk.Button(bottom, text="Schließen", command=dlg.destroy).pack(side="right")
        btn_ok.pack(side="right", padx=6)
        subs: list = []
        labels = {"neu": "neu", "importiert": "übernommen", "vorhanden": "doppelt", "ungueltig": "ungültig"}

        def load():
            src = v_src.get().strip()
            if not src:
                return
            dlg.configure(cursor="watch")
            dlg.update()
            try:
                found = parse_submissions(fetch_text(src))
            except Exception as e:      # noqa: BLE001 – Fehler dem Nutzer zeigen
                messagebox.showerror("Einsendungen holen", f"Konnte nicht geladen werden:\n{e}", parent=dlg)
                return
            finally:
                dlg.configure(cursor="")
            keys = set(self.settings.get("imported_keys", [])) | self.pending_import_keys
            check(found, self.entries, keys)
            subs[:] = found
            tree.delete(*tree.get_children())
            sel = []
            for k, s in enumerate(subs):
                tag = "grau" if s.status != "neu" else ("warn" if s.hinweis else "")
                tree.insert("", "end", iid=str(k), tags=(tag,), values=(
                    labels[s.status], s.frage, s.antwort, s.wort, ", ".join(s.tags), s.name, s.datum, s.hinweis))
                if s.status == "neu":
                    sel.append(str(k))
            tree.selection_set(sel)
            n = {st: sum(1 for s in subs if s.status == st) for st in labels}
            info.set(f"{len(subs)} Einsendungen: {n['neu']} neu, {n['importiert']} schon übernommen, "
                     f"{n['vorhanden']} doppelt, {n['ungueltig']} ungültig")
            btn_ok.configure(state="normal" if subs else "disabled")
            self.settings["import_source"] = src
            self.settings["import_tags"] = v_tags.get()

        def take():
            chosen = [subs[int(i)] for i in tree.selection()]
            chosen = [s for s in chosen if s.status in ("neu", "vorhanden")]
            if not chosen:
                messagebox.showinfo("Einsendungen holen", "Keine neue Einsendung ausgewählt.", parent=dlg)
                return
            start = max((e.id for e in self.entries), default=0) + 1
            new = to_entries(chosen, start, split_tags(v_tags.get()))
            self.entries.extend(new)
            self.pending_import_keys |= {s.key for s in chosen}
            self.settings["import_tags"] = v_tags.get()
            self.set_dirty()
            self.refresh_db()
            self.v_quelle.set(QUELLEN["einsendung"])
            self.refresh_table()
            dlg.destroy()
            messagebox.showinfo("Einsendungen holen",
                                f"{len(new)} Begriffe übernommen (Quelle „Einsendung“). Die Tabelle zeigt jetzt nur "
                                "Einsendungen. Mit Strg+S speichern.", parent=self)

        if v_src.get():
            dlg.after(100, load)

    # ---------------------------------------------------------------- Claude: Zugang
    def key_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Claude-Zugang")
        dlg.transient(self)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, wraplength=460, justify="left", text=(
            "Für die Prüfung durch Claude braucht das Programm einen API-Schlüssel von Anthropic. Du legst ihn in "
            "der Anthropic Console an (platform.claude.com > API Keys). Die Prüfung kostet Guthaben, die Kosten "
            "zeigt das Programm vorher an.\n\nDer Schlüssel wird nur auf diesem Rechner gespeichert, nicht im "
            "Projektordner:\n" + cc.key_path())).pack(anchor="w")
        ttk.Label(frm, text=cc.access_info(), foreground="#555").pack(anchor="w", pady=(8, 4))
        v_key = tk.StringVar()
        ent = ttk.Entry(frm, textvariable=v_key, show="•", width=60)
        ent.pack(fill="x")
        ent.focus_set()
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(10, 0))

        def save():
            key = v_key.get().strip()
            if not key:
                messagebox.showinfo("Claude-Zugang", "Bitte einen Schlüssel einfügen.", parent=dlg)
                return
            cc.store_key(key)
            dlg.destroy()
            messagebox.showinfo("Claude-Zugang", "Schlüssel gespeichert.", parent=self)

        def remove():
            if messagebox.askyesno("Claude-Zugang", "Gespeicherten Schlüssel löschen?", parent=dlg):
                cc.store_key("")
                dlg.destroy()

        ttk.Button(row, text="Speichern", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(row, text="Gespeicherten Schlüssel löschen", command=remove).pack(side="left", padx=6)
        ttk.Button(row, text="Abbrechen", command=dlg.destroy).pack(side="right")
        ent.bind("<Return>", lambda e: save())

    # ---------------------------------------------------------------- Claude: Datenbank prüfen
    def db_review_dialog(self):
        if getattr(self, "_db_review_dlg", None) and self._db_review_dlg.winfo_exists():
            self._db_review_dlg.lift()
            return
        dlg = tk.Toplevel(self)
        self._db_review_dlg = dlg
        dlg.title("Datenbank mit Claude prüfen")
        dlg.geometry("1250x700")
        dlg.transient(self)
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, wraplength=1200, justify="left", foreground="#555", text=(
            "Claude bewertet jeden Begriff mit 1 bis 5 Sternen und meldet Probleme (falsche oder zu lange Fragen, "
            "unbekannte Begriffe, fehlende Tags). Geprüfte Begriffe merkt sich das Programm in „pruefung.json“. "
            "Ändert sich ein Begriff, wird er beim nächsten Durchlauf wieder geprüft. Mit ★1 bewertete Begriffe "
            "lässt der Generator weg, solange das Häkchen im Reiter „Rätsel erstellen“ gesetzt ist.")).pack(anchor="w")
        top = ttk.Frame(frm)
        top.pack(fill="x", pady=8)
        v_mode = tk.StringVar(value="neu")
        rb1 = ttk.Radiobutton(top, value="neu", variable=v_mode)
        rb1.pack(side="left")
        rb2 = ttk.Radiobutton(top, value="alle", variable=v_mode)
        rb2.pack(side="left", padx=(12, 0))
        btn_run = ttk.Button(top, text="Prüfung starten", style="Accent.TButton")
        btn_run.pack(side="left", padx=(16, 0))
        prog = ttk.Progressbar(top, mode="determinate", maximum=100, length=200)
        prog.pack(side="left", padx=10)
        info = tk.StringVar()
        ttk.Label(frm, textvariable=info, wraplength=1200, justify="left").pack(anchor="w")

        cols = ("id", "antwort", "frage", "bewertung", "probleme", "hinweis", "vorschlag", "tags")
        heads = ("ID", "Antwort", "Frage", "Bewertung", "Probleme", "Hinweis", "Vorschlag Frage", "Vorschlag Tags")
        widths = (45, 110, 220, 75, 170, 280, 200, 130)
        tf = ttk.Frame(frm)
        tf.pack(fill="both", expand=True, pady=(8, 0))
        tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w", stretch=c in ("frage", "hinweis", "vorschlag"))
        sb = ttk.Scrollbar(tf, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("s1", foreground="#b00020")
        tree.tag_configure("s2", foreground="#a05a00")

        bottom = ttk.Frame(frm)
        bottom.pack(fill="x", pady=(8, 0))
        list_info = tk.StringVar()
        ttk.Label(bottom, textvariable=list_info).pack(side="left")
        ttk.Button(bottom, text="Schließen", command=lambda: close()).pack(side="right")
        ttk.Button(bottom, text="Bearbeiten", command=lambda: edit()).pack(side="right", padx=4)
        ttk.Button(bottom, text=f"Begriff meiden (Tag „{AVOID_TAG}“)", command=lambda: act("meiden")).pack(side="right")
        ttk.Button(bottom, text="Abhaken", command=lambda: act("ok")).pack(side="right", padx=4)
        ttk.Button(bottom, text="Vorschlag übernehmen", style="Accent.TButton",
                   command=lambda: act("vorschlag")).pack(side="right")

        by_iid: dict[str, Entry] = {}

        def update_info():
            n_pend = len(self.review.pending(self.entries))
            n_all = len(self.entries)
            rb1.configure(text=f"Nur neue und geänderte Begriffe ({n_pend})")
            rb2.configure(text=f"Alle Begriffe neu prüfen ({n_all})")
            job = self.review_job
            if job and job.running:
                return
            n = n_pend if v_mode.get() == "neu" else n_all
            info.set(f"{n_all - n_pend} von {n_all} Begriffen sind aktuell geprüft. "
                     f"Bisherige Kosten insgesamt ca. {self.review.total_cost:.2f} $. "
                     + (f"Diese Prüfung: {n} Begriffe, grob geschätzt {cc.estimate_cost(n):.2f} $ "
                        f"(Modell {cc.MODEL})." if n else "Nichts zu prüfen.") + "  " + cc.access_info())

        def fill():
            tree.delete(*tree.get_children())
            by_iid.clear()
            findings = self.review.open_findings(self.entries)
            for e, r in findings:
                iid = str(e.id)
                by_iid[iid] = e
                tags_s = " ".join([f"+{t}" for t in r.get("tags_dazu", [])] + [f"−{t}" for t in r.get("tags_weg", [])])
                tree.insert("", "end", iid=iid, tags=(f"s{r['bewertung']}",), values=(
                    e.id, e.antwort, e.frage, "★" * r["bewertung"],
                    ", ".join(cc.PROBLEME_TEXT.get(p, p) for p in r.get("probleme", [])),
                    r.get("hinweis", ""), r.get("frage_vorschlag", ""), tags_s))
            list_info.set(f"{len(findings)} offene Hinweise (★1–3, Probleme oder Vorschläge). Strg+Klick wählt mehrere.")

        def selected():
            return [by_iid[i] for i in tree.selection() if i in by_iid]

        def act(kind):
            sel = selected()
            if not sel:
                return
            for e in sel:
                r = self.review.get(e)
                if not r:
                    continue
                if kind == "vorschlag":
                    self.apply_review_suggestion(e, r)
                elif kind == "meiden" and AVOID_TAG not in e.tags:
                    e.tags = e.tags + [AVOID_TAG]
                self.review.mark_done(e)
            self.review.save()
            if kind != "ok":
                self.set_dirty()
                self.refresh_db()
            else:
                self.refresh_table()
            fill()
            update_info()

        def edit():
            sel = selected()
            if not sel:
                return
            self.nb.select(self.tab_db)
            self.v_search.set("")
            self.v_tagfilter.set("Alle Tags")
            self.v_quelle.set("alle")
            self.v_pruef.set("alle")
            self.refresh_table()
            iid = str(sel[0].id)
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.edit_selected()
            self.lift()

        def start():
            job = self.review_job
            if job and job.running:
                job.stop.set()
                btn_run.configure(text="Wird beendet …", state="disabled")
                return
            todo = self.review.pending(self.entries) if v_mode.get() == "neu" else list(self.entries)
            if not todo:
                messagebox.showinfo("Prüfung", "Alle Begriffe sind schon geprüft.", parent=dlg)
                return
            est = cc.estimate_cost(len(todo))
            if not messagebox.askyesno("Prüfung starten", f"{len(todo)} Begriffe an Claude schicken?\n\n"
                                       f"Grobe Schätzung der Kosten: {est:.2f} $ (wird vom Guthaben des "
                                       "API-Schlüssels abgebucht).", parent=dlg):
                return
            self.run_db_review(todo)
            btn_run.configure(text="Stopp")
            poll()

        def poll():
            job = self.review_job
            if not dlg.winfo_exists():
                return
            if job:
                prog["value"] = 100 * job.done / max(1, job.total)
                info.set(f"Prüfung läuft … {job.done} von {job.total} Paketen fertig, Kosten bisher ca. "
                         f"{job.cost:.2f} $" + (f", {len(job.errors)} Pakete mit Fehler" if job.errors else ""))
                if job.changed:
                    job.changed = False
                    fill()
                if not job.running:
                    btn_run.configure(text="Prüfung starten", state="normal")
                    fill()
                    update_info()
                    if job.errors:
                        msg = "\n".join(sorted(set(job.errors))[:5])
                        messagebox.showwarning("Prüfung", f"{len(job.errors)} Pakete konnten nicht geprüft werden:\n\n"
                                               f"{msg}\n\nSie bleiben ungeprüft und kommen beim nächsten Durchlauf "
                                               "wieder dran.", parent=dlg)
                    elif job.stop.is_set():
                        info.set(info.get() + "  Abgebrochen. Der Rest bleibt für den nächsten Durchlauf.")
                    return
            dlg.after(300, poll)

        def close():
            dlg.destroy()

        btn_run.configure(command=start)
        v_mode.trace_add("write", lambda *a: update_info())
        fill()
        update_info()
        if self.review_job and self.review_job.running:
            btn_run.configure(text="Stopp")
            poll()

    def run_db_review(self, todo: list[Entry]):
        """Startet die Prüfung im Hintergrund. Ergebnisse landen über self.after im Hauptthread im Speicher."""
        job = SimpleNamespace(running=True, done=0, total=-(-len(todo) // cc.BATCH_SIZE), cost=0.0, errors=[],
                              stop=threading.Event(), changed=False, q=queue.Queue())
        self.review_job = job
        snapshot = [Entry(e.id, e.frage, e.antwort, list(e.tags), e.quelle, e.erstellt) for e in todo]
        everything = list(self.entries)

        def work():
            try:
                cc.review_db(snapshot, everything, lambda *a: job.q.put(("batch",) + a), job.stop,
                             hints=cc.load_tag_hints(self.app_dir))
            except cc.ClaudeError as e:
                job.q.put(("fatal", str(e)))
            except Exception:       # noqa: BLE001
                job.q.put(("fatal", traceback.format_exc()))
            job.q.put(("end",))

        def drain():
            try:
                while True:
                    msg = job.q.get_nowait()
                    if msg[0] == "batch":
                        res, fps, cost, err = msg[1:]
                        job.done += 1
                        job.cost += cost
                        self.review.total_cost += cost
                        if err:
                            job.errors.append(err)
                        for r in res:
                            self.review.put(fps[r["id"]], r)
                        self.review.save()
                        job.changed = True
                    elif msg[0] == "fatal":
                        job.errors.append(msg[1])
                    elif msg[0] == "end":
                        job.running = False
                        self.refresh_table()
                        self.update_pool_hint()
                        return
            except queue.Empty:
                pass
            self.after(300, drain)

        threading.Thread(target=work, daemon=True).start()
        drain()

    def apply_review_suggestion(self, e: Entry, r: dict):
        """Übernimmt Claudes Vorschlag (Frage und/oder Tags). Die Quelle bleibt, wie sie ist."""
        if r.get("frage_vorschlag"):
            e.frage = r["frage_vorschlag"].strip()
        weg = set(r.get("tags_weg", []))
        tags = [t for t in e.tags if t not in weg]
        tags += [t for t in (x.strip().lower() for x in r.get("tags_dazu", [])) if t and t not in tags]
        e.tags = classify(tags, e.antwort)

    # ---------------------------------------------------------------- Claude: Rätsel prüfen
    def puzzle_review_dialog(self):
        p = self.puzzle
        if not p:
            return
        topics = set(getattr(self, "puzzle_topics", set()))
        facts = cc.puzzle_facts(p, topics, self.review)
        dlg = tk.Toplevel(self)
        dlg.title("Rätsel prüfen")
        dlg.geometry("1150x720")
        dlg.transient(self)
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        txt = tk.Text(frm, height=14, wrap="word", font=("Segoe UI", 10), relief="flat",
                      background=self.cget("background"))
        txt.pack(fill="x")
        txt.tag_configure("h", font=("Segoe UI", 10, "bold"))

        def show(parts):
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            for s, tag in parts:
                txt.insert("end", s, tag)
            txt.configure(state="disabled")

        base = [("Zahlen\n", "h"), (cc.facts_text(facts) + "\n\n", "")]
        show(base + [("Claude liest das Rätsel … (meist unter einer Minute)", "")])

        cols = ("id", "antwort", "frage", "problem", "hinweis", "vorschlag")
        heads = ("ID", "Antwort", "Frage", "Problem", "Hinweis", "Vorschlag Frage")
        widths = (45, 110, 220, 150, 330, 220)
        tf = ttk.Frame(frm)
        tf.pack(fill="both", expand=True, pady=(8, 0))
        tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w", stretch=c in ("frage", "hinweis", "vorschlag"))
        sb = ttk.Scrollbar(tf, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom = ttk.Frame(frm)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, foreground="#555", wraplength=520, justify="left", text=(
            "Eine neue Frage ändert das Rätsel sofort. Ein Wort selbst lässt sich nicht tauschen, ohne das Gitter "
            "neu zu bauen: „Begriff meiden“ und dann neu erzeugen.")).pack(side="left")
        ttk.Button(bottom, text="Schließen", command=dlg.destroy).pack(side="right")
        ttk.Button(bottom, text=f"Begriff meiden (Tag „{AVOID_TAG}“)", command=lambda: act("meiden")).pack(
            side="right", padx=4)
        ttk.Button(bottom, text="Frage übernehmen", style="Accent.TButton", command=lambda: act("frage")).pack(
            side="right")

        by_id = {e.id: e for e in self.entries}
        items: dict[str, dict] = {}
        q: queue.Queue = queue.Queue()
        title = self.v_title.get()

        def work():
            try:
                q.put(("ok",) + cc.review_puzzle(p, topics, title, facts, cc.load_tag_hints(self.app_dir)))
            except cc.ClaudeError as e:
                q.put(("err", str(e)))
            except Exception:       # noqa: BLE001
                q.put(("err", traceback.format_exc()))

        def poll():
            if not dlg.winfo_exists():
                return
            try:
                msg = q.get_nowait()
            except queue.Empty:
                dlg.after(300, poll)
                return
            if msg[0] == "err":
                show(base + [("Prüfung fehlgeschlagen\n", "h"), (msg[1], "")])
                return
            data, cost = msg[1], msg[2]
            self.review.total_cost += cost
            self.review.save()
            parts = base + [(f"Claudes Urteil: {data['note']} von 10\n", "h"), (data["fazit"] + "\n\n", ""),
                            ("Themenbezug\n", "h"), (data["themenbezug"] + "\n\n", "")]
            if data["staerken"]:
                parts += [("Stärken\n", "h"), ("".join(f"• {s}\n" for s in data["staerken"]) + "\n", "")]
            if data["verbesserungen"]:
                parts += [("Verbesserungen\n", "h"), ("".join(f"• {s}\n" for s in data["verbesserungen"]) + "\n", "")]
            parts.append((f"(Kosten dieser Prüfung ca. {cost:.2f} $)", ""))
            show(parts)
            for k, w in enumerate(data["woerter"]):
                e = by_id.get(w["id"])
                if not e:
                    continue
                iid = str(k)
                items[iid] = w
                tree.insert("", "end", iid=iid, values=(e.id, e.antwort, e.frage,
                                                        cc.PROBLEME_TEXT.get(w["problem"], w["problem"]),
                                                        w["hinweis"], w["frage_vorschlag"]))

        def act(kind):
            changed = False
            for iid in tree.selection():
                w = items.get(iid)
                e = by_id.get(w["id"]) if w else None
                if not e:
                    continue
                if kind == "frage" and w["frage_vorschlag"]:
                    e.frage = w["frage_vorschlag"].strip()
                    tree.set(iid, "frage", e.frage)
                    changed = True
                elif kind == "meiden" and AVOID_TAG not in e.tags:
                    e.tags = e.tags + [AVOID_TAG]
                    changed = True
            if changed:
                self.set_dirty()
                self.refresh_db()
                self.fill_used_words()
                self.redraw()

        threading.Thread(target=work, daemon=True).start()
        poll()

    def _scroll_column(self, parent, width=345):
        """Linke Spalte mit senkrechter Bildlaufleiste (für kleine Bildschirme). Liefert den Innenrahmen."""
        outer = ttk.Frame(parent)
        outer.pack(side="left", fill="y", padx=(0, 10))
        bg = ttk.Style(self).lookup("TFrame", "background") or self.cget("background")
        cv = tk.Canvas(outer, width=width, highlightthickness=0, background=bg)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="y")
        inner = ttk.Frame(cv)
        win = cv.create_window(0, 0, window=inner, anchor="nw", width=width)

        def update(_e=None):
            cv.configure(scrollregion=(0, 0, width, inner.winfo_reqheight()))
            need = inner.winfo_reqheight() > cv.winfo_height()
            if need and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y")
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()
                cv.yview_moveto(0)

        inner.bind("<Configure>", update)
        cv.bind("<Configure>", update)

        def wheel(e):
            if e.widget.winfo_class() in ("Listbox", "TCombobox", "TSpinbox", "Text"):
                return                     # diese Elemente scrollen selbst
            if sb.winfo_ismapped():
                cv.yview_scroll(int(-e.delta / 120), "units")

        outer.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", wheel))
        outer.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))
        cv.itemconfigure(win, width=width)
        return inner

    def _build_gen_tab(self):
        f = self.tab_gen
        s = self.settings
        left = self._scroll_column(f)

        box = ttk.LabelFrame(left, text="Rätsel", padding=8)
        box.pack(fill="x")
        self.v_title = tk.StringVar(value=s["title"])
        self.v_w, self.v_h = tk.IntVar(value=s["w"]), tk.IntVar(value=s["h"])
        self.v_cell, self.v_time = tk.DoubleVar(value=s["cell"]), tk.IntVar(value=s["time"])
        ttk.Label(box, text="Überschrift").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.v_title, width=26).grid(row=0, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(box, text="Spalten × Zeilen").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(box, from_=6, to=25, textvariable=self.v_w, width=5).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(box, text="×").grid(row=1, column=2)
        ttk.Spinbox(box, from_=6, to=30, textvariable=self.v_h, width=5).grid(row=1, column=3, sticky="w")
        ttk.Label(box, text="Kästchen (mm)").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(box, from_=7, to=20, increment=0.5, textvariable=self.v_cell, width=5,
                    command=self.redraw).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(box, text="Rechenzeit (s)").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(box, from_=3, to=300, textvariable=self.v_time, width=5).grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(box, text="Format").grid(row=4, column=0, sticky="w")
        self.v_fmt = tk.StringVar(value=FORMATS.get(s.get("fmt", "a4"), FORMATS["a4"]))
        cb = ttk.Combobox(box, textvariable=self.v_fmt, values=list(FORMATS.values()), state="readonly", width=30)
        cb.grid(row=4, column=1, columnspan=3, sticky="ew", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: (self.update_size_hint(), self.redraw()))
        self.size_var = tk.StringVar()
        ttk.Label(box, textvariable=self.size_var, foreground="#555", wraplength=300).grid(
            row=5, column=0, columnspan=4, sticky="w")
        for v in (self.v_w, self.v_h, self.v_cell):
            v.trace_add("write", lambda *a: self.update_size_hint())

        tb = ttk.LabelFrame(left, text="Themen (keins gewählt = alle Begriffe)", padding=8)
        tb.pack(fill="both", expand=True, pady=8)
        lf = ttk.Frame(tb)
        lf.pack(fill="both", expand=True)
        self.topic_list = tk.Listbox(lf, selectmode="multiple", height=7, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(lf, command=self.topic_list.yview)
        self.topic_list.configure(yscrollcommand=sb.set)
        self.topic_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.topic_list.bind("<<ListboxSelect>>", lambda e: self.update_pool_hint())
        ttk.Button(tb, text="Auswahl aufheben", command=self.clear_topics).pack(anchor="w", pady=(4, 0))
        self.v_filler = tk.BooleanVar(value=s["filler"])
        ttk.Checkbutton(tb, text="Kurze Füllwörter aus allen Themen erlauben", variable=self.v_filler,
                        command=self.update_pool_hint).pack(anchor="w", pady=(4, 0))
        self.v_use_auto = tk.BooleanVar(value=s["use_auto"])
        ttk.Checkbutton(tb, text="Automatisch erzeugte Begriffe verwenden", variable=self.v_use_auto,
                        command=self.update_pool_hint).pack(anchor="w")
        self.v_skip_bad = tk.BooleanVar(value=s["skip_bad"])
        ttk.Checkbutton(tb, text="Von Claude mit ★1 bewertete Begriffe meiden", variable=self.v_skip_bad,
                        command=self.update_pool_hint).pack(anchor="w")
        self.pool_var = tk.StringVar()
        ttk.Label(tb, textvariable=self.pool_var, foreground="#555", wraplength=300).pack(anchor="w")
        mx = ttk.Frame(tb)
        mx.pack(anchor="w", pady=(6, 0))
        self.v_max_theme = tk.BooleanVar(value=s["max_theme"])
        self.v_runs = tk.IntVar(value=s["runs"])
        ttk.Checkbutton(mx, text="Themen-Modus: beste von", variable=self.v_max_theme).pack(side="left")
        ttk.Spinbox(mx, from_=5, to=1000, increment=5, textvariable=self.v_runs, width=5).pack(side="left", padx=3)
        ttk.Label(mx, text="Rätseln").pack(side="left")
        ttk.Label(tb, text="meiste Themenwörter gewinnt; Rechenzeit gilt dann nicht.",
                  foreground="#555", wraplength=300).pack(anchor="w")

        fx = ttk.LabelFrame(left, text="Feste Begriffe (kommen sicher ins Rätsel)", padding=8)
        fx.pack(fill="x", pady=(0, 8))
        self.fixed_ids = [int(x) for x in s.get("fixed_ids", [])]
        self.v_fix_tag, self.v_fix_entry = tk.StringVar(), tk.StringVar()
        r1 = ttk.Frame(fx)
        r1.pack(fill="x")
        ttk.Label(r1, text="Thema").pack(side="left")
        self.cb_fix_tag = ttk.Combobox(r1, textvariable=self.v_fix_tag, state="readonly", width=14)
        self.cb_fix_tag.pack(side="left", padx=(4, 0))
        self.cb_fix_tag.bind("<<ComboboxSelected>>", lambda e: self.update_fix_choices())
        r2 = ttk.Frame(fx)
        r2.pack(fill="x", pady=(4, 0))
        self.cb_fix_entry = ttk.Combobox(r2, textvariable=self.v_fix_entry, state="readonly", width=22)
        self.cb_fix_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Hinzufügen", command=self.add_fixed).pack(side="left", padx=(4, 0))
        self.fix_list = tk.Listbox(fx, height=4, activestyle="none", exportselection=False)
        self.fix_list.pack(fill="x", pady=(4, 0))
        self.fix_list.bind("<Delete>", lambda e: self.remove_fixed())
        r3 = ttk.Frame(fx)
        r3.pack(fill="x", pady=(4, 0))
        ttk.Button(r3, text="Entfernen", command=self.remove_fixed).pack(side="left")
        ttk.Button(r3, text="Alle entfernen", command=self.clear_fixed).pack(side="left", padx=4)
        self.fix_hint = tk.StringVar()
        ttk.Label(fx, textvariable=self.fix_hint, foreground="#555", wraplength=300).pack(anchor="w")
        hl = ttk.Frame(fx)
        hl.pack(fill="x", pady=(8, 0))
        ttk.Label(hl, text="Fragefelder hervorheben:").pack(side="left")
        self.v_highlight = tk.StringVar(value=s.get("highlight") or "(keine)")
        self.cb_highlight = ttk.Combobox(hl, textvariable=self.v_highlight, state="readonly", width=14)
        self.cb_highlight.pack(side="left", padx=(4, 0))
        self.cb_highlight.bind("<<ComboboxSelected>>", lambda e: (self.redraw(), self.update_highlight_hint()))
        self.highlight_hint = tk.StringVar()
        ttk.Label(fx, textvariable=self.highlight_hint, foreground="#555", wraplength=300).pack(anchor="w")

        sbx = ttk.LabelFrame(left, text="Lösungswort", padding=8)
        sbx.pack(fill="x")
        self.v_sol_mode = tk.StringVar(value=s["sol_mode"])
        self.v_sol_word = tk.StringVar(value=s["sol_word"])
        self.v_sol_min, self.v_sol_max = tk.IntVar(value=s["sol_min"]), tk.IntVar(value=s["sol_max"])
        ttk.Radiobutton(sbx, text="automatisch, Länge", value="auto", variable=self.v_sol_mode).grid(row=0, column=0, sticky="w")
        lenf = ttk.Frame(sbx)
        lenf.grid(row=0, column=1, sticky="w")
        ttk.Spinbox(lenf, from_=3, to=15, textvariable=self.v_sol_min, width=3).pack(side="left")
        ttk.Label(lenf, text=" bis ").pack(side="left")
        ttk.Spinbox(lenf, from_=3, to=20, textvariable=self.v_sol_max, width=3).pack(side="left")
        ttk.Radiobutton(sbx, text="eigenes:", value="manual", variable=self.v_sol_mode).grid(row=1, column=0, sticky="w")
        e = ttk.Entry(sbx, textvariable=self.v_sol_word, width=16)
        e.grid(row=1, column=1, sticky="w", pady=2)
        e.bind("<FocusIn>", lambda ev: self.v_sol_mode.set("manual"))
        ttk.Radiobutton(sbx, text="ohne", value="none", variable=self.v_sol_mode).grid(row=2, column=0, sticky="w")

        act = ttk.Frame(left)
        act.pack(fill="x", pady=8)
        self.btn_gen = ttk.Button(act, text="Rätsel erzeugen", style="Accent.TButton", command=self.toggle_generate)
        self.btn_gen.pack(fill="x")
        self.progress = ttk.Progressbar(act, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=4)
        self.gen_status = tk.StringVar(value="Bereit.")
        ttk.Label(act, textvariable=self.gen_status, wraplength=320, justify="left").pack(anchor="w")
        row = ttk.Frame(act)
        row.pack(fill="x", pady=(6, 0))
        self.btn_sol = ttk.Button(row, text="Anderes Lösungswort", command=self.new_solution, state="disabled")
        self.btn_sol.pack(side="left")
        self.btn_pdf = ttk.Button(row, text="PDF speichern…", command=self.save_pdf, state="disabled")
        self.btn_pdf.pack(side="left", padx=6)
        self.btn_review = ttk.Button(act, text="Rätsel prüfen (Claude)…", command=self.puzzle_review_dialog,
                                     state="disabled")
        self.btn_review.pack(fill="x", pady=(6, 0))
        self.v_show_key = tk.BooleanVar(value=s["show_key"])
        self.v_pdf_key = tk.BooleanVar(value=s["pdf_key"])
        ttk.Checkbutton(act, text="Lösung in der Vorschau zeigen", variable=self.v_show_key, command=self.redraw).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(act, text="Lösungsblatt als Seite 2 ins PDF", variable=self.v_pdf_key).pack(anchor="w")

        # Vorschau + Wortliste
        right = ttk.PanedWindow(f, orient="vertical")
        right.pack(side="left", fill="both", expand=True)
        cf = ttk.Frame(right)
        self.canvas = tk.Canvas(cf, background="#e9e7e2", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        right.add(cf, weight=4)
        wf = ttk.Frame(right)
        cols = ("id", "frage", "antwort", "richtung", "quelle", "tags", "pruefung")
        self.used_tree = ttk.Treeview(wf, columns=cols, show="headings", height=6)
        for c, h, w in zip(cols, ("ID", "Frage", "Antwort", "Richtung", "Quelle", "Tags", "Claude-Prüfung"),
                           (50, 300, 140, 90, 90, 240, 180)):
            self.used_tree.heading(c, text=h)
            self.used_tree.column(c, width=w, stretch=c in ("frage", "tags"))
        usb = ttk.Scrollbar(wf, command=self.used_tree.yview)
        self.used_tree.configure(yscrollcommand=usb.set)
        self.used_tree.pack(side="left", fill="both", expand=True)
        usb.pack(side="right", fill="y")
        right.add(wf, weight=1)
        self.update_size_hint()

    # ---------------------------------------------------------------- Themen
    def refresh_topics(self):
        wanted = set(self.selected_topics()) if self.topic_list.size() else set(self.settings.get("topics", []))
        self.topic_tags = [t for t, _ in all_tags(self.entries)]
        counts = dict(all_tags(self.entries))
        self.topic_list.delete(0, "end")
        for k, t in enumerate(self.topic_tags):
            self.topic_list.insert("end", f"{t}  ({counts[t]})")
            if t in wanted:
                self.topic_list.selection_set(k)
        self.update_pool_hint()
        # Auswahllisten für feste Begriffe und Hervorhebung
        self.cb_fix_tag.configure(values=self.topic_tags)
        if self.v_fix_tag.get() not in self.topic_tags:
            self.v_fix_tag.set(next((t for t in self.selected_topics()), self.topic_tags[0] if self.topic_tags else ""))
        self.update_fix_choices()
        self.cb_highlight.configure(values=["(keine)"] + self.topic_tags)
        if self.v_highlight.get() not in self.topic_tags:
            self.v_highlight.set("(keine)")
        ids = {e.id for e in self.entries}
        self.fixed_ids = [i for i in self.fixed_ids if i in ids]
        self.refresh_fixed()

    # ---------------------------------------------------------------- Feste Begriffe
    def update_fix_choices(self):
        tag = self.v_fix_tag.get()
        self._fix_choices = sorted((e for e in self.entries if tag in e.tags), key=lambda e: e.wort)
        self.cb_fix_entry.configure(values=[f"{e.antwort} – {e.frage}" for e in self._fix_choices])
        self.v_fix_entry.set("")

    def add_fixed(self):
        k = self.cb_fix_entry.current()
        if k < 0:
            return
        e = self._fix_choices[k]
        if any(x.wort == e.wort for x in self.fixed_entries()):
            messagebox.showinfo("Hinweis", f"„{e.wort}“ ist schon als fester Begriff gewählt.")
            return
        self.fixed_ids.append(e.id)
        self.refresh_fixed()

    def remove_fixed(self):
        sel = self.fix_list.curselection()
        if sel:
            del self.fixed_ids[sel[0]]
            self.refresh_fixed()

    def clear_fixed(self):
        self.fixed_ids = []
        self.refresh_fixed()

    def fixed_entries(self) -> list[Entry]:
        by_id = {e.id: e for e in self.entries}
        return [by_id[i] for i in self.fixed_ids if i in by_id]

    def refresh_fixed(self):
        self.fix_list.delete(0, "end")
        fixed = self.fixed_entries()
        for e in fixed:
            self.fix_list.insert("end", f"{e.wort} ({len(e.wort)}) – {e.frage}")
        n_long = sum(1 for e in fixed if len(e.wort) > 5)
        if not fixed:
            self.fix_hint.set("Keine. Wähle ein Thema und einen Begriff und klicke „Hinzufügen“.")
        elif n_long > 2:
            self.fix_hint.set(f"{n_long} lange Begriffe (über 5 Buchstaben) machen die Suche schwer – "
                              "mehr Rechenzeit, ein kleineres Raster oder höchstens 2 lange Begriffe.")
        else:
            self.fix_hint.set(f"{len(fixed)} fest. Kurze Begriffe gehen leicht, je langem Begriff "
                              "dauert die Suche länger.")

    def highlight_tags(self):
        t = self.v_highlight.get()
        return {t} if t and t != "(keine)" else None

    def update_highlight_hint(self):
        hl = self.highlight_tags()
        if not (hl and self.puzzle):
            self.highlight_hint.set("")
            return
        n = sum(1 for wd in self.puzzle.words if wd.entry and hl & set(wd.entry.tags))
        self.highlight_hint.set(f"{n} Fragefelder im Rätsel sind hervorgehoben.")

    def selected_topics(self):
        return [self.topic_tags[i] for i in self.topic_list.curselection()] if hasattr(self, "topic_tags") else []

    def clear_topics(self):
        self.topic_list.selection_clear(0, "end")
        self.update_pool_hint()

    def skip_fn(self):
        """Filter für den Generator: von Claude mit ★1 bewertete Begriffe (wenn das Häkchen gesetzt ist)."""
        return self.review.is_bad if self.v_skip_bad.get() else None

    def update_pool_hint(self):
        if not hasattr(self, "v_skip_bad"):
            return
        topics = set(self.selected_topics())
        ua = self.v_use_auto.get()
        pool = word_pool(self.entries, topics, self.v_filler.get(), ua, self.skip_fn())
        th = theme_pool(self.entries, topics, ua, self.skip_fn())
        if topics and len(pool) != len(th):
            txt = f"{len(th)} Themenbegriffe + {len(pool) - len(th)} Füllwörter"
        else:
            txt = f"{len(pool)} Begriffe verfügbar"
        own = sum(1 for e in pool if e.quelle != "auto")
        txt += f", davon {own} eigene"
        if len(pool) < 300:
            txt += " – für dichte Rätsel eher wenig."
        n_skip = sum(1 for e in self.entries if AVOID_TAG in e.tags or (self.v_skip_bad.get() and self.review.is_bad(e)))
        if n_skip:
            txt += f" ({n_skip} Begriffe mit ★1 oder Tag „{AVOID_TAG}“ ausgelassen)"
        self.pool_var.set(txt)

    def fmt(self) -> str:
        return next((k for k, v in FORMATS.items() if v == self.v_fmt.get()), "a4")

    def update_size_hint(self):
        try:
            w, h, c = self.v_w.get() * self.v_cell.get(), self.v_h.get() * self.v_cell.get(), self.v_cell.get()
        except (tk.TclError, ValueError):
            return
        if self.fmt() == "a5":
            dummy = SimpleNamespace(w=self.v_w.get(), h=self.v_h.get(), solution=("X" * 8, ()))
            k = fit_cell(dummy, self.v_title.get(), *A5_AVAIL, top=16.0)
            self.size_var.set(f"Rechte Hälfte von A4 quer: Kästchen ca. {k:.1f} mm (Kästchengröße oben gilt "
                              "nicht)" + ("" if k >= 9 else " – klein. Ein hohes Raster wie 12 × 16 passt besser."))
            return
        fits = w <= 186 and h <= 245
        self.size_var.set(f"Raster {w:.0f} × {h:.0f} mm" + ("" if fits else " – wird für A4 verkleinert") + (
            "" if c >= 10 else " – kleine Kästchen: Fragen schwer lesbar"))

    # ---------------------------------------------------------------- Erzeugen
    def toggle_generate(self):
        if self.gen_thread and self.gen_thread.is_alive():
            self.stop_event.set()
            self.gen_status.set("Wird beendet – bestes bisheriges Ergebnis wird übernommen …")
            return
        try:
            W, H, secs = int(self.v_w.get()), int(self.v_h.get()), int(self.v_time.get())
        except (tk.TclError, ValueError):
            messagebox.showinfo("Hinweis", "Bitte gültige Zahlen für Größe und Rechenzeit eingeben.")
            return
        W, H, secs = max(6, min(25, W)), max(6, min(30, H)), max(3, secs)
        topics = set(self.selected_topics())
        ua = self.v_use_auto.get()
        skip = self.skip_fn()
        pool = word_pool(self.entries, topics, self.v_filler.get(), ua, skip)
        if len(pool) < 30:
            messagebox.showinfo("Hinweis", "Zu wenige Begriffe – bitte Datenbank laden, Themen erweitern "
                                "oder automatisch erzeugte Begriffe zulassen.")
            return
        theme = preferred_words(self.entries, topics, ua, skip)
        prefer = (lambda e: any(t in topics for t in e.tags)) if topics else None
        runs = None
        if self.v_max_theme.get():
            if not topics:
                messagebox.showinfo("Hinweis", "Für den Themen-Modus bitte mindestens ein Thema auswählen.")
                return
            try:
                runs = max(1, int(self.v_runs.get()))
            except (tk.TclError, ValueError):
                runs = 100
            secs = 3600                      # Obergrenze; Ende nach 'runs' Rätseln oder mit Stopp
        fixed = self.fixed_entries()
        too_long = [e.wort for e in fixed if not generator.fits(W, H, len(e.wort))]
        if too_long:
            messagebox.showinfo("Hinweis", "Diese festen Begriffe sind zu lang für das Raster "
                                f"(Länge + 1 Fragefeld > {max(W, H)}): {', '.join(too_long)}")
            return
        self.stop_event = threading.Event()
        self.btn_gen.configure(text="Stopp")
        self.progress["value"] = 0
        self.gen_status.set("Suche startet …")
        self.store_settings()

        def work():
            try:
                p, info = generator.generate(pool, W, H, secs, theme_words=theme, prefer=prefer,
                                             progress=lambda d: self.msgs.put(("progress", d)),
                                             stop_event=self.stop_event, runs=runs, fixed=fixed)
                self.msgs.put(("done", p, info, topics))
            except Exception:       # noqa: BLE001
                self.msgs.put(("error", traceback.format_exc()))

        self.gen_thread = threading.Thread(target=work, daemon=True)
        self.gen_thread.start()
        self.after(150, self.poll)

    def poll(self):
        try:
            while True:
                msg = self.msgs.get_nowait()
                if msg[0] == "progress":
                    d = msg[1]
                    if d.get("runs"):
                        self.progress["value"] = min(100, 100 * d["found"] / d["runs"])
                        m, s_ = divmod(int(d["elapsed"]), 60)
                        self.gen_status.set(
                            f"Themen-Modus … {min(d['found'], d['runs'])} von {d['runs']} Rätseln ({m}:{s_:02d} min)"
                            + (f", bisher bestes: {d['best_theme']} Themenwörter" if d["has_result"] else ""))
                    else:
                        self.progress["value"] = min(100, 100 * d["elapsed"] / max(1, d["seconds"]))
                        self.gen_status.set(f"Suche läuft … {d['patterns']} Muster, {d['found']} gefüllte Rätsel"
                                            + ("" if d["has_result"] else " (noch keins)"))
                elif msg[0] == "done":
                    self.finish(*msg[1:])
                    return
                elif msg[0] == "error":
                    self.btn_gen.configure(text="Rätsel erzeugen")
                    self.gen_status.set("Fehler bei der Erzeugung.")
                    messagebox.showerror("Fehler", msg[1])
                    return
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def finish(self, p, info, topics):
        self.btn_gen.configure(text="Rätsel erzeugen")
        self.progress["value"] = 100
        if p is None:
            self.gen_status.set("Kein Rätsel gefunden. Mehr Rechenzeit, ein kleineres Raster oder mehr "
                                "(kurze) Begriffe helfen."
                                + (" Auch weniger lange feste Begriffe machen es leichter." if self.fixed_ids else ""))
            return
        self.puzzle = p
        self.puzzle_topics = topics
        msg = self.apply_solution()
        st = p.stats()
        theme_note = ""
        if topics:
            n_th = sum(1 for w in p.words if w.entry and any(t in topics for t in w.entry.tags))
            n_all = len({e.wort for e in theme_pool(self.entries, topics, self.v_use_auto.get(), self.skip_fn())})
            theme_note = f"{n_th} Themenwörter (von {n_all} vorhandenen) · "
        if self.fixed_ids:
            ids = {wd.entry.id for wd in p.words if wd.entry}
            n_fx = sum(1 for i in self.fixed_ids if i in ids)
            theme_note += f"{n_fx} von {len(self.fixed_ids)} festen Begriffen · "
        self.gen_status.set(
            theme_note +
            f"{st['words']} Wörter · {st['gray']} graue Felder · Ø {st['avg_len']:.1f} Buchstaben · "
            f"{st['two_letter']} Zweibuchstaben-Wörter · {st['bent']} Knickpfeile "
            f"(beste von {info['found']} gefüllten Varianten) · "
            f"{sum(1 for w in p.words if w.entry and w.entry.quelle != 'auto')} eigene Fragen"
            + (f"\nLösungswort: {p.solution[0]}" if p.solution else "") + (f"\n⚠ {msg}" if msg else ""))
        self.btn_sol.configure(state="normal")
        self.btn_pdf.configure(state="normal")
        self.btn_review.configure(state="normal")
        self.fill_used_words()
        self.update_highlight_hint()
        self.redraw()

    def apply_solution(self, avoid=None):
        p = self.puzzle
        cands = theme_pool(self.entries, getattr(self, "puzzle_topics", set()), self.v_use_auto.get(), self.skip_fn())
        try:
            mn, mx = int(self.v_sol_min.get()), int(self.v_sol_max.get())
        except (tk.TclError, ValueError):
            mn, mx = 6, 10
        return pick_solution(p, self.v_sol_mode.get(), self.v_sol_word.get(), mn, mx, cands, avoid=avoid)

    def new_solution(self):
        if not self.puzzle:
            return
        msg = self.apply_solution(avoid=self.puzzle.solution[0] if self.puzzle.solution else None)
        if msg:
            messagebox.showinfo("Lösungswort", msg)
        self.gen_status.set(self.gen_status.get().split("\nLösungswort")[0].split("\n⚠")[0]
                            + (f"\nLösungswort: {self.puzzle.solution[0]}" if self.puzzle.solution else ""))
        self.redraw()

    def fill_used_words(self):
        self.used_tree.delete(*self.used_tree.get_children())
        for wd in sorted(self.puzzle.words, key=lambda w: w.wort):
            e = wd.entry
            self.used_tree.insert("", "end", values=(e.id if e else "", e.frage if e else "", wd.wort,
                                                     "waagerecht" if wd.dir == "A" else "senkrecht",
                                                     e.quelle_text if e else "", ", ".join(e.tags) if e else "",
                                                     self.review.status(e) if e else ""))

    # ---------------------------------------------------------------- Vorschau
    def redraw(self):
        cv = self.canvas
        cv.delete("all")
        if not self.puzzle:
            cv.create_text(cv.winfo_width() / 2, cv.winfo_height() / 2, fill="#777", font=("Segoe UI", 12),
                           text="Noch kein Rätsel – links Einstellungen wählen und „Rätsel erzeugen“ klicken.")
            return
        try:
            cell = float(self.v_cell.get())
        except (tk.TclError, ValueError):
            cell = 12.0
        fmt = self.fmt()
        ops, w, h, _ = page_ops(self.puzzle, self.v_title.get(), cell, answers=self.v_show_key.get(),
                                highlight=self.highlight_tags(), fmt=fmt)
        pad = 30
        cw, ch = max(100, cv.winfo_width()), max(100, cv.winfo_height())
        k = min((cw - 2 * pad) / w, (ch - 2 * pad) / h)
        ox, oy = (cw - w * k) / 2, (ch - h * k) / 2
        m = 0 if fmt == "a5" else 16          # A5: Rahmen = halbe Seite
        cv.create_rectangle(ox - m, oy - m, ox + w * k + m, oy + h * k + m, fill="white", outline="#ccc")

        def P(x, y):
            return ox + x * k, oy + y * k

        for op in ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, ww, hh, fill, lw = op
                col = "" if fill is None else "#%02x%02x%02x" % ((int(fill * 255),) * 3)
                cv.create_rectangle(*P(x, y), *P(x + ww, y + hh), fill=col, outline="black", width=max(1, lw * k))
            elif kind in ("text", "textl"):
                _, x, y, text, font, size = op
                px = max(5, round(size * k))
                cv.create_text(*P(x, y), text=text, anchor="center" if kind == "text" else "w",
                               font=("Helvetica", -px, "bold" if "Bold" in font else "normal"))
            elif kind == "poly":
                cv.create_polygon(*[c for pt in op[1] for c in P(*pt)], fill="black", outline="")
            elif kind == "line":
                cv.create_line(*[c for pt in op[1] for c in P(*pt)], width=max(1, op[2] * k))
            elif kind == "circle":
                _, x, y, r, lw = op
                cv.create_oval(*P(x - r, y - r), *P(x + r, y + r), width=max(1, lw * k))

    # ---------------------------------------------------------------- PDF
    def save_pdf(self):
        if not self.puzzle:
            return
        title = self.v_title.get().strip() or "Schwedenrätsel"
        safe = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip() or "raetsel"
        p = filedialog.asksaveasfilename(title="PDF speichern", initialdir=self.app_dir, defaultextension=".pdf",
                                         initialfile=f"{safe}.pdf", filetypes=[("PDF", "*.pdf")])
        if not p:
            return
        try:
            scale, cell = export_pdf(p, self.puzzle, title, float(self.v_cell.get()), self.v_pdf_key.get(),
                                     highlight=self.highlight_tags(), fmt=self.fmt())
        except PermissionError:
            messagebox.showerror("Fehler", "Die PDF-Datei ist noch geöffnet (z. B. im PDF-Viewer). Bitte schließen und erneut versuchen.")
            return
        if self.fmt() == "a5":
            note = f"\n(A4 quer, Rätsel auf der rechten Hälfte, Kästchen {cell:.1f} mm.)"
        else:
            note = f"\n(Das Rätsel wurde auf {scale:.0%} verkleinert, damit es auf A4 passt.)" if scale < 0.999 else ""
        if messagebox.askyesno("PDF gespeichert", f"Gespeichert:\n{p}{note}\n\nJetzt öffnen?"):
            try:
                os.startfile(p)     # type: ignore[attr-defined]  (nur Windows)
            except (AttributeError, OSError):
                pass

    # ================================================================ Auswertung
    def _build_dash_tab(self):
        f = self.tab_dash
        bg = f.cget("background")
        bar = tk.Frame(f, background=bg)
        bar.pack(fill="x", pady=(0, 8))
        tk.Label(bar, text="Begriffe:", bg=bg, font=(charts.FONT, 9)).pack(side="left")
        self.v_dash_source = tk.StringVar(value=self.settings["dash_source"])
        cb = ttk.Combobox(bar, textvariable=self.v_dash_source, state="readonly", width=12,
                          values=["alle", "eigene", "automatisch"])
        cb.pack(side="left", padx=(4, 16))
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_dash())
        tk.Label(bar, text="Ziel je Thema:", bg=bg, font=(charts.FONT, 9)).pack(side="left")
        self.v_dash_target = tk.IntVar(value=self.settings["dash_target"])
        ttk.Spinbox(bar, from_=10, to=5000, increment=10, textvariable=self.v_dash_target, width=6,
                    command=self.refresh_dash).pack(side="left", padx=4)
        tk.Label(bar, text="Begriffe", bg=bg, font=(charts.FONT, 9)).pack(side="left")
        self.v_dash_target.trace_add("write", lambda *a: self.after_idle(self.refresh_dash))

        tiles = tk.Frame(f, background=bg)
        tiles.pack(fill="x", pady=(0, 10))
        self.tile_vars = {}
        for key, label in (("begriffe", "Begriffe"), ("eigene", "Eigene"), ("automatisch", "Automatisch"),
                           ("antworten", "Verschiedene Antworten"), ("themen", "Themen"),
                           ("avg_len", "Ø Antwortlänge"), ("mehrfach", "Antworten mit mehreren Fragen"),
                           ("ohne_tag", "Ohne Tag")):
            card = tk.Frame(tiles, background=charts.SURFACE, highlightbackground=charts.GRID, highlightthickness=1)
            card.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(card, text=label, font=(charts.FONT, 9), fg=charts.MUTED, bg=charts.SURFACE).pack(anchor="w", padx=12, pady=(8, 0))
            v = tk.StringVar(value="–")
            tk.Label(card, textvariable=v, font=(charts.FONT, 18, "bold"), fg=charts.INK, bg=charts.SURFACE).pack(anchor="w", padx=12, pady=(0, 8))
            self.tile_vars[key] = v

        grid = tk.Frame(f, background=bg)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=3, uniform="c")
        grid.columnconfigure(1, weight=2, uniform="c")
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)
        self.ch_len = charts.CompareColumns(
            grid, "Wortlängen", "Anteil verschiedener Antworten je Buchstabenzahl, verglichen mit dem Bedarf "
            "des Generators (Anteil der Lücken dieser Länge in seinen Mustern)",
            "Länge", "Bedarf im Rätsel", " Buchstaben")
        self.ch_len.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        self.ch_let = charts.CompareColumns(
            grid, "Buchstabenverteilung", "Anteil jedes Buchstabens in allen Antworten (Ä = AE, ß = SS), "
            "verglichen mit der Häufigkeit im deutschen Text",
            "Buchstabe", "Deutsch (Text)")
        self.ch_let.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.ch_tag = charts.StackedBars(grid, "Begriffe je Thema", "Ein Begriff kann mehrere Themen haben "
                                         "und zählt dann in jedem mit")
        self.ch_tag.grid(row=0, column=1, rowspan=2, sticky="nsew")

    def refresh_dash(self):
        if not hasattr(self, "ch_len") or self.nb.index("current") != 2:
            return
        src = self.v_dash_source.get()
        try:
            target = max(1, int(self.v_dash_target.get()))
        except (tk.TclError, ValueError):
            target = 100
        es = stats.filter_source(self.entries, src)
        ov = stats.overview(es)
        for key, v in self.tile_vars.items():
            val = ov[key]
            v.set(charts.de(val) if isinstance(val, float) else f"{val:,}".replace(",", "."))

        rows = stats.length_distribution(es)
        gaps = stats.biggest_gaps(rows)
        txt = ("Zu wenig im Vergleich zum Bedarf: " + ", ".join(
            f"{g['label']} Buchstaben ({charts.de(g['share'], 0)} % statt {charts.de(g['expected'], 0)} %)" for g in gaps)
            if gaps else "Die Wortlängen decken den Bedarf des Generators.")
        self.ch_len.set_rows(rows, txt)

        rows = stats.letter_distribution(es)
        diff = sorted(rows, key=lambda r: r["share"] - r["expected"])
        lo = [r["label"] for r in diff[:4] if r["share"] < r["expected"] - 0.5]
        hi = [r["label"] for r in diff[::-1][:4] if r["share"] > r["expected"] + 0.5]
        txt = (("Seltener als im Deutschen: " + ", ".join(lo) if lo else "")
               + (" · häufiger: " + ", ".join(hi) if hi else "")) or "Entspricht etwa der deutschen Buchstabenverteilung."
        self.ch_let.set_rows(rows, txt.lstrip(" ·"))

        rows = stats.tag_distribution(es)
        done = sum(1 for r in rows if r["count"] >= target)
        weakest = [r["label"] for r in sorted(rows, key=lambda r: r["count"])[:3]]
        txt = f"{done} von {len(rows)} Themen haben das Ziel von {target} erreicht." + (
            f" Am kleinsten: {', '.join(weakest)}" if rows else "")
        self.ch_tag.set_rows(rows, target, txt)

    # ---------------------------------------------------------------- Einstellungen
    def store_settings(self):
        try:
            self.settings.update(
                title=self.v_title.get(), w=int(self.v_w.get()), h=int(self.v_h.get()), cell=float(self.v_cell.get()),
                time=int(self.v_time.get()), fmt=self.fmt(), topics=self.selected_topics(), filler=self.v_filler.get(),
                sol_mode=self.v_sol_mode.get(), sol_word=self.v_sol_word.get(), sol_min=int(self.v_sol_min.get()),
                sol_max=int(self.v_sol_max.get()), show_key=self.v_show_key.get(), pdf_key=self.v_pdf_key.get(),
                use_auto=self.v_use_auto.get(), max_theme=self.v_max_theme.get(), runs=int(self.v_runs.get()),
                skip_bad=self.v_skip_bad.get(),
                fixed_ids=list(self.fixed_ids),
                highlight="" if self.v_highlight.get() == "(keine)" else self.v_highlight.get(),
                dash_source=self.v_dash_source.get(),
                dash_target=int(self.v_dash_target.get()))
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except (tk.TclError, ValueError, OSError):
            pass


def run(csv_path: str):
    try:                                    # scharfe Darstellung auf hochauflösenden Bildschirmen
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                       # noqa: BLE001
        pass
    random.seed()
    App(csv_path).mainloop()
