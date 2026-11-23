"""Einsendungen aus einem Google-Formular (über Google Sheets) oder einer CSV-Datei übernehmen.

Quelle ist entweder
  - ein Google-Sheets-Link: Freigabe-Link ("Jeder mit dem Link: Betrachter"), "Im Web veröffentlichen" als CSV,
    oder ein Export-Link. Er wird automatisch in einen CSV-Export-Link umgewandelt.
  - eine heruntergeladene CSV-Datei (in Sheets: Datei > Herunterladen > CSV).

Die Spalten werden an ihren Überschriften erkannt (Formular-Fragen wie "Frage", "Antwort", "Tags/Themen",
"Name", dazu der automatische "Zeitstempel"). Reihenfolge und weitere Spalten sind egal.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import io
import re
import urllib.request
from dataclasses import dataclass, field

from .words import Entry, classify, normalize, split_tags

# Schlüsselwörter in den Spaltenüberschriften (kleingeschrieben, Teilstring genügt). Reihenfolge = Priorität.
_COLS = {
    "zeit": ("zeitstempel", "timestamp", "datum", "date"),
    "frage": ("frage", "hinweis", "umschreibung", "clue", "question"),
    "antwort": ("antwort", "lösung", "loesung", "lösungswort", "begriff", "answer"),
    "tags": ("tag", "thema", "themen", "kategorie", "topic"),
    "name": ("name", "von wem", "eingereicht", "absender"),
}


def sheet_csv_url(url: str) -> str:
    """Wandelt einen Google-Sheets-Link in einen CSV-Export-Link um. Andere Links bleiben, wie sie sind."""
    url = url.strip()
    if "docs.google.com/spreadsheets" not in url or "output=csv" in url or "format=csv" in url:
        return url
    if "/pub" in url:                                   # "Im Web veröffentlichen", aber als Webseite
        base = url.split("?")[0].replace("/pubhtml", "/pub")
        gid = re.search(r"gid=(\d+)", url)
        return base + "?output=csv" + (f"&gid={gid.group(1)}" if gid else "")
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        return url
    gid = re.search(r"gid=(\d+)", url)
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            + (f"&gid={gid.group(1)}" if gid else ""))


def fetch_text(source: str, timeout: float = 20) -> str:
    """Liest die Quelle (Link oder Dateipfad) als Text."""
    source = source.strip().strip('"')
    if source.lower().startswith(("http://", "https://")):
        req = urllib.request.Request(sheet_csv_url(source), headers={"User-Agent": "Schwedenraetsel-Import"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type", "")
        text = data.decode("utf-8-sig", errors="replace")
        if "text/html" in ctype or text.lstrip().lower().startswith(("<!doctype", "<html")):
            raise ValueError("Der Link liefert eine Webseite statt einer Tabelle. Meist ist das Sheet nicht "
                             "freigegeben: in Google Sheets „Freigeben“ > „Jeder mit dem Link“ (Betrachter) "
                             "einstellen oder „Datei > Freigeben > Im Web veröffentlichen“ als CSV wählen.")
        return text
    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


@dataclass
class Submission:
    frage: str
    antwort: str
    tags: list[str] = field(default_factory=list)
    zeit: str = ""            # wie im Sheet
    datum: str = ""           # JJJJ-MM-TT
    name: str = ""
    key: str = ""             # Kennung der Zeile, um doppelte Importe zu erkennen
    status: str = "neu"       # neu | importiert | vorhanden | ungueltig
    hinweis: str = ""

    @property
    def wort(self) -> str:
        return normalize(self.antwort)


def _date(text: str) -> str:
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return datetime.date.today().isoformat()


def _find_columns(head: list[str]) -> dict[str, int]:
    h = [x.strip().lower() for x in head]
    col: dict[str, int] = {}
    for key in ("zeit", "antwort", "frage", "tags", "name"):        # "antwort" vor "frage": "Frage zur Antwort" o. ä.
        for i, name in enumerate(h):
            if i in col.values():
                continue
            if any(k in name for k in _COLS[key]):
                col[key] = i
                break
    return col


def parse_submissions(text: str) -> list[Submission]:
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ("\t" if first.count("\t") > first.count(",") else ",")
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(f.strip() for f in r)]
    if not rows:
        return []
    col = _find_columns(rows[0])
    if "frage" not in col or "antwort" not in col:
        raise ValueError("In der ersten Zeile fehlen die Spalten „Frage“ und „Antwort“. Gefunden: "
                         + ", ".join(x for x in rows[0] if x.strip()))

    def get(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    out = []
    for row in rows[1:]:
        frage, antwort = get(row, "frage"), get(row, "antwort")
        if not frage and not antwort:
            continue
        zeit = get(row, "zeit")
        key = hashlib.sha1("\x1f".join((zeit, frage, antwort)).encode("utf-8")).hexdigest()[:16]
        s = Submission(frage=frage, antwort=antwort, tags=split_tags(get(row, "tags")), zeit=zeit,
                       datum=_date(zeit) if zeit else datetime.date.today().isoformat(), name=get(row, "name"),
                       key=key)
        out.append(s)
    return out


def check(subs: list[Submission], entries: list[Entry], imported_keys: set[str]) -> None:
    """Setzt status und hinweis jeder Einsendung."""
    by_word: dict[str, list[Entry]] = {}
    for e in entries:
        by_word.setdefault(e.wort, []).append(e)
    seen: set[tuple[str, str]] = set()
    for s in subs:
        w = s.wort
        pair = (s.frage.casefold(), w)
        if s.key in imported_keys:
            s.status, s.hinweis = "importiert", "schon früher übernommen"
        elif not s.frage or len(w) < 2:
            s.status, s.hinweis = "ungueltig", "Frage fehlt" if not s.frage else "Antwort braucht mind. 2 Buchstaben"
        elif any(e.frage.casefold() == pair[0] for e in by_word.get(w, [])) or pair in seen:
            s.status, s.hinweis = "vorhanden", "gleiche Frage und Antwort gibt es schon"
        else:
            s.status = "neu"
            other = by_word.get(w, [])
            notes = []
            if other:
                notes.append("Antwort schon vorhanden: " + " / ".join(e.frage for e in other[:2]))
            if len(w) > 15:
                notes.append(f"Antwort sehr lang ({len(w)} Buchstaben) – passt nur in große Raster")
            if len(s.frage) > 32:
                notes.append("Frage lang – passt evtl. nicht ins Kästchen")
            s.hinweis = "; ".join(notes)
        seen.add(pair)


def to_entries(subs: list[Submission], start_id: int, extra_tags: list[str]) -> list[Entry]:
    out = []
    for k, s in enumerate(subs):
        tags = list(s.tags)
        for t in extra_tags:
            if t not in tags:
                tags.append(t)
        out.append(Entry(start_id + k, s.frage, s.antwort, classify(tags, s.antwort), "einsendung", s.datum))
    return out
