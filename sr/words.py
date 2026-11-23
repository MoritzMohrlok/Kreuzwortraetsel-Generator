"""Wortdatenbank: CSV lesen/schreiben und Antworten in Rätselbuchstaben umwandeln.

CSV-Format (UTF-8 mit BOM, Trennzeichen ';'):
    id;frage;antwort;laenge;tags;quelle;erstellt
Tags sind durch Komma getrennt. 'laenge' wird beim Speichern neu berechnet.
quelle: auto | manuell | einsendung, erstellt: Datum JJJJ-MM-TT.
"""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    """'Öl-Wanne' -> 'OELWANNE' (Umlaute als zwei Buchstaben, wie im Rätsel üblich)."""
    s = (text or "").upper()
    for a, b in (("Ä", "AE"), ("Ö", "OE"), ("Ü", "UE"), ("ẞ", "SS"), ("ß", "SS")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if "A" <= ch <= "Z")


def split_tags(text: str) -> list[str]:
    seen, out = set(), []
    for t in re.split(r"[,|]", text or ""):
        t = t.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


QUELLEN = {"auto": "automatisch", "manuell": "manuell", "einsendung": "Einsendung"}

SHORT_TAG = "kurzwort"
SHORT_MAX = 3            # Antworten bis zu dieser Buchstabenzahl werden automatisch als Kurzwort markiert


def classify(tags: list[str], antwort: str) -> list[str]:
    """Ergänzt automatisch den Tag 'kurzwort' für kurze Antworten."""
    if len(normalize(antwort)) <= SHORT_MAX and SHORT_TAG not in tags:
        return tags + [SHORT_TAG]
    return tags


@dataclass
class Entry:
    id: int
    frage: str
    antwort: str
    tags: list[str] = field(default_factory=list)
    quelle: str = "manuell"     # 'auto' (automatisch erzeugt), 'manuell' (selbst eingegeben), 'einsendung' (später: Web-Maske)
    erstellt: str = ""          # Datum JJJJ-MM-TT

    @property
    def quelle_text(self) -> str:
        return QUELLEN.get(self.quelle, self.quelle)

    @property
    def wort(self) -> str:
        return normalize(self.antwort)


_ALIASES = {
    "id": ("id", "nr"),
    "frage": ("frage", "hinweis", "clue", "question"),
    "antwort": ("antwort", "wort", "loesung", "lösung", "answer"),
    "tags": ("tags", "tag", "themen", "thema"),
    "quelle": ("quelle", "herkunft", "source"),
    "erstellt": ("erstellt", "datum", "created"),
}


def parse_csv(text: str) -> list[Entry]:
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") >= first.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(f.strip() for f in r)]
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    col = {}
    for key, names in _ALIASES.items():
        for i, h in enumerate(head):
            if h in names:
                col[key] = i
                break
    body = rows[1:]
    if "frage" not in col or "antwort" not in col:     # keine Kopfzeile: frage;antwort;tags
        body, col = rows, {"frage": 0, "antwort": 1, "tags": 2}

    def get(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    entries, max_id = [], 0
    for row in body:
        frage, antwort = get(row, "frage"), get(row, "antwort")
        if not frage or not antwort:
            continue
        try:
            eid = int(get(row, "id"))
        except ValueError:
            eid = 0
        max_id = max(max_id, eid)
        # Spalte fehlt = alte Datei (automatisch erzeugt); Spalte leer = z. B. in Excel von Hand ergänzt
        quelle = get(row, "quelle").lower() or ("manuell" if "quelle" in col else "auto")
        tags = classify(split_tags(get(row, "tags")), antwort)
        entries.append(Entry(eid, frage, antwort, tags, quelle, get(row, "erstellt")))
    used = set()
    for e in entries:                                   # fehlende/doppelte IDs vergeben
        if e.id <= 0 or e.id in used:
            max_id += 1
            e.id = max_id
        used.add(e.id)
    return entries


EXAMPLE_CSV = "woerter_beispiel.csv"


def ensure_db(path: str) -> None:
    """Legt die eigene Datenbank beim ersten Start aus der Beispieldatenbank an (die eigene bleibt aus Git heraus)."""
    example = os.path.join(os.path.dirname(os.path.abspath(path)), EXAMPLE_CSV)
    if not os.path.exists(path) and os.path.exists(example):
        import shutil
        shutil.copyfile(example, path)


def load_csv(path: str) -> list[Entry]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return parse_csv(f.read())


def save_csv(path: str, entries: list[Entry]) -> None:
    """Schreibt sicher (erst Temporärdatei, dann ersetzen)."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".woerter-", suffix=".csv", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\r\n")
            w.writerow(["id", "frage", "antwort", "laenge", "tags", "quelle", "erstellt"])
            for e in entries:
                w.writerow([e.id, e.frage, e.antwort, len(e.wort), ",".join(e.tags), e.quelle, e.erstellt])
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def all_tags(entries: list[Entry]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for e in entries:
        for t in e.tags:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[0])
