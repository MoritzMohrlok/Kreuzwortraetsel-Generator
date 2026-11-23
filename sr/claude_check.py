"""Prüfung durch Claude: Wortdatenbank bewerten und fertige Rätsel begutachten.

Die Ergebnisse der Datenbank-Prüfung stehen in 'pruefung.json' neben der CSV. Jeder Eintrag merkt sich einen
Fingerabdruck aus Frage, Antwort und Tags. Ändert sich ein Begriff, gilt er wieder als ungeprüft, und die nächste
Prüfung schickt nur neue und geänderte Begriffe an Claude.

Zugang: Umgebungsvariable ANTHROPIC_API_KEY, ein über 'ant auth login' angemeldetes Profil oder der im
Programm eingegebene Schlüssel (gespeichert in %APPDATA%\\Schwedenraetsel\\anthropic_key.txt, nicht im Projektordner).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .service import AVOID_TAG  # noqa: F401 – Begriffe mit diesem Tag nimmt der Generator nicht
from .words import Entry

MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.0, 20.0          # US-Dollar je 1 Mio. Tokens (Claude Opus 5.5)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
BATCH_SIZE = 60                          # Begriffe je Anfrage
WORKERS = 4                              # gleichzeitige Anfragen

PROBLEME = {
    "sachlich_falsch": "Frage falsch oder passt nicht zur Antwort",
    "mehrdeutig": "Frage zu ungenau",
    "zu_lang": "Frage zu lang fürs Kästchen",
    "verraet_antwort": "Frage verrät die Antwort",
    "schreibfehler": "Schreibfehler",
    "unbekannt": "Begriff kaum bekannt",
    "antwortform": "Antwort ungünstig (Abkürzung, Beugung …)",
    "tags_unpassend": "Tags fehlen oder passen nicht",
    "anstoessig": "heikel für ein Magazin",
}
RAETSEL_PROBLEME = dict(PROBLEME, themenfremd="passt nicht zum Thema", zu_leicht="zu leicht",
                        zu_schwer="zu schwer", wiederholung="Wiederholung im Rätsel")
PROBLEME_TEXT = dict(RAETSEL_PROBLEME)


class ClaudeError(Exception):
    """Fehler, den die Oberfläche als Meldung zeigt."""


# ---------------------------------------------------------------- Zugang

def key_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Schwedenraetsel", "anthropic_key.txt")


def stored_key() -> str:
    try:
        with open(key_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def store_key(key: str) -> None:
    path = key_path()
    if not key:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key.strip())


def access_info() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "Zugang über die Umgebungsvariable ANTHROPIC_API_KEY."
    if stored_key():
        return "Zugang über den gespeicherten API-Schlüssel."
    return "Kein API-Schlüssel hinterlegt (ein Profil aus „ant auth login“ wird versucht)."


def make_client():
    try:
        import anthropic
    except ImportError as e:
        raise ClaudeError("Das Paket „anthropic“ fehlt. Bitte start.bat einmal neu starten, "
                          "dann wird es installiert.") from e
    key = os.environ.get("ANTHROPIC_API_KEY") or stored_key()
    try:
        return anthropic.Anthropic(api_key=key, max_retries=3) if key else anthropic.Anthropic(max_retries=3)
    except Exception as e:      # noqa: BLE001 – z. B. keine Anmeldedaten gefunden
        raise ClaudeError("Kein Zugang zu Claude. Bitte unter „Claude-Zugang…“ einen API-Schlüssel eintragen.\n\n"
                          f"({e})") from e


def _ask(client, system: str, user: str, schema: dict, effort: str, max_tokens: int = 32000):
    """Eine Anfrage mit fest vorgegebenem JSON-Format. Liefert (Daten, Kosten in US-Dollar)."""
    import anthropic
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",         # lehnt das Modell ab, springt serverseitig ein anderes ein
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ClaudeError("Der API-Schlüssel wurde abgelehnt. Bitte unter „Claude-Zugang…“ prüfen.") from e
    except anthropic.PermissionDeniedError as e:
        raise ClaudeError(f"Keine Berechtigung für {MODEL}: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("Zu viele Anfragen oder Guthaben aufgebraucht. Später erneut versuchen.") from e
    except anthropic.BadRequestError as e:
        raise ClaudeError(f"Anfrage abgelehnt: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Fehler beim Claude-Dienst ({e.status_code}). Später erneut versuchen.") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError("Keine Verbindung zu Claude. Internetverbindung prüfen.") from e
    u = msg.usage
    cost = ((u.input_tokens + (u.cache_creation_input_tokens or 0) * 1.25 + (u.cache_read_input_tokens or 0) * 0.1)
            * PRICE_IN + u.output_tokens * PRICE_OUT) / 1e6
    if msg.stop_reason == "refusal":
        raise ClaudeError("Claude hat die Anfrage abgelehnt.")
    if msg.stop_reason == "max_tokens":
        raise ClaudeError("Die Antwort war zu lang und wurde abgeschnitten.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text), cost
    except ValueError as e:
        raise ClaudeError("Claude hat keine lesbare Antwort geliefert.") from e


# ---------------------------------------------------------------- Ergebnisspeicher

def fingerprint(e: Entry) -> str:
    raw = "\x1f".join((e.frage, e.antwort, ",".join(sorted(e.tags))))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class ReviewStore:
    """Ergebnisse der Datenbank-Prüfung je Begriff-ID (Datei pruefung.json)."""

    def __init__(self, path: str):
        self.path = path
        self.items: dict[int, dict] = {}
        self.total_cost = 0.0
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.items = {int(k): v for k, v in data.get("eintraege", {}).items()}
            self.total_cost = float(data.get("kosten_gesamt", 0.0))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        folder = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".pruefung-", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "modell": MODEL, "kosten_gesamt": round(self.total_cost, 4),
                       "eintraege": {str(k): v for k, v in sorted(self.items.items())}},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, e: Entry) -> dict | None:
        """Gültiges Ergebnis (None, wenn ungeprüft oder seitdem geändert)."""
        r = self.items.get(e.id)
        return r if r and r.get("fp") == fingerprint(e) else None

    def status(self, e: Entry) -> str:
        if e.id not in self.items:
            return "ungeprüft"
        r = self.get(e)
        if r is None:
            return "geändert"
        txt = "★" * r["bewertung"] + "☆" * (5 - r["bewertung"])
        if r.get("probleme") and not r.get("erledigt"):
            txt += " · " + ", ".join(PROBLEME_TEXT.get(p, p) for p in r["probleme"])
        elif r.get("erledigt"):
            txt += " · erledigt"
        return txt

    def pending(self, entries: list[Entry]) -> list[Entry]:
        return [e for e in entries if self.get(e) is None]

    def is_bad(self, e: Entry) -> bool:
        r = self.get(e)
        return bool(r) and r["bewertung"] <= 1 and not r.get("erledigt")

    def open_findings(self, entries: list[Entry]) -> list[tuple[Entry, dict]]:
        out = []
        for e in entries:
            r = self.get(e)
            if r and not r.get("erledigt") and (r["bewertung"] <= 3 or r.get("probleme") or has_suggestion(r)):
                out.append((e, r))
        out.sort(key=lambda er: (er[1]["bewertung"], er[0].id))
        return out

    def put(self, fp: str, r: dict) -> None:
        r = dict(r)
        eid = int(r.pop("id"))
        r["fp"] = fp
        r["datum"] = datetime.date.today().isoformat()
        r["erledigt"] = False
        self.items[eid] = r

    def mark_done(self, e: Entry) -> None:
        """Hinweis abgehakt (auch nach Übernahme eines Vorschlags: neuer Fingerabdruck, keine neue Prüfung)."""
        r = self.items.get(e.id)
        if r:
            r["fp"] = fingerprint(e)
            r["erledigt"] = True

    def prune(self, entries: list[Entry]) -> None:
        ids = {e.id for e in entries}
        self.items = {k: v for k, v in self.items.items() if k in ids}


def has_suggestion(r: dict) -> bool:
    return bool(r.get("frage_vorschlag") or r.get("tags_dazu") or r.get("tags_weg"))


# ---------------------------------------------------------------- Datenbank-Prüfung

DB_SYSTEM = """Du prüfst die Wortdatenbank eines Generators für Schwedenrätsel. Die Rätsel werden in einem \
gedruckten Magazin veröffentlicht und von Hand gelöst. Jede Frage steht in einem eigenen kleinen Kästchen \
(etwa 3 bis 4 kurze Zeilen, höchstens rund 35 Zeichen). Die Antwort wird in Großbuchstaben ohne Umlaute \
eingetragen (Ä = AE, ß = SS); das Feld "buchstaben" zeigt die Schreibweise im Rätsel.

Bewerte jeden Begriff mit 1 bis 5:
5 = sehr guter Rätselbegriff: bekannt, Frage eindeutig, treffend und knapp
4 = gut
3 = brauchbar, z. B. typisches kurzes Füllwort oder etwas blasse Frage
2 = schwach: Frage ungenau, zu lang oder Begriff wenig bekannt; sollte überarbeitet werden
1 = unbrauchbar: falsch, unverständlich, anstößig oder für Rätselfreunde nicht lösbar

Kurze Füllwörter (2 bis 3 Buchstaben, Abkürzungen, chemische Zeichen, Kfz-Kennzeichen) sind in \
Schwedenrätseln üblich und nötig. Bewerte sie nicht schlecht, nur weil sie kurz sind; prüfe aber, ob die \
Frage stimmt und eindeutig ist. Fragen dürfen den typischen knappen Rätselstil haben ("Fluss in Italien", \
"engl.: eins"). Ein "|" in der Frage ist eine gewollte Trennstelle.

Für jeden Begriff:
- "probleme": nur zutreffende Codes, sonst leer.
- "hinweis": ein kurzer Satz, warum, nur bei Bewertung 1 bis 3 oder Problemen, sonst "".
- "frage_vorschlag": eine bessere Frage (höchstens 35 Zeichen) nur wenn die jetzige klar verbesserungswürdig \
ist, sonst "". Die Antwort bleibt dieselbe.
- "tags_dazu" / "tags_weg": nur wenn ein Tag eindeutig fehlt oder eindeutig falsch ist, sonst leer. Nimm \
vorhandene Tags aus der Liste unten, erfinde nur in Ausnahmefällen neue (klein geschrieben). Entferne nie den \
Tag "kurzwort", Tags mit "-fill" und keine persönlichen Tags (Namen, Orte, Anlässe).

Gib für jeden übergebenen Begriff genau ein Ergebnis mit seiner id zurück.

Vorhandene Tags (Anzahl Begriffe):
{tags}

Bekannte Bedeutungen: kurzwort = automatisch für Antworten bis 3 Buchstaben.{hints}"""

TAG_HINTS_FILE = "tag_hinweise.txt"


def load_tag_hints(folder: str) -> str:
    """Eigene Erklärungen zu Tags (optional, eine pro Zeile, z. B. 'urlaub = Fotos vom Sommer 2025').

    Die Datei liegt neben der Datenbank und gehört wie diese nicht ins öffentliche Repository."""
    try:
        with open(os.path.join(folder, TAG_HINTS_FILE), encoding="utf-8-sig") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return ""
    return ("\n" + "\n".join(lines)) if lines else ""


def _db_schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "bewertung": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "probleme": {"type": "array", "items": {"type": "string", "enum": list(PROBLEME)}},
            "hinweis": {"type": "string"},
            "frage_vorschlag": {"type": "string"},
            "tags_dazu": {"type": "array", "items": {"type": "string"}},
            "tags_weg": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "bewertung", "probleme", "hinweis", "frage_vorschlag", "tags_dazu", "tags_weg"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"ergebnisse": {"type": "array", "items": item}},
            "required": ["ergebnisse"], "additionalProperties": False}


def _entry_json(e: Entry) -> str:
    return json.dumps({"id": e.id, "frage": e.frage, "antwort": e.antwort, "buchstaben": e.wort,
                       "tags": e.tags, "quelle": e.quelle}, ensure_ascii=False)


def estimate_cost(n: int) -> float:
    """Grobe Schätzung in US-Dollar (Eingabe, Ausgabe und Denkzeit je Begriff)."""
    batches = -(-n // BATCH_SIZE)
    return (n * (50 * PRICE_IN + 150 * PRICE_OUT) + batches * 1500 * PRICE_IN) / 1e6


def review_db(entries: list[Entry], all_entries: list[Entry], on_batch, stop: threading.Event,
              hints: str = "") -> None:
    """Prüft 'entries' in Paketen. Ruft on_batch(ergebnisse, fingerabdrücke, kosten, fehler) je Paket auf
    (aus einem Hintergrund-Thread). Läuft blockierend; für die Oberfläche in einem Thread starten.
    hints – eigene Tag-Erklärungen aus load_tag_hints()."""
    client = make_client()
    tags = Counter(t for e in all_entries for t in e.tags)
    system = DB_SYSTEM.format(tags=", ".join(f"{t} ({n})" for t, n in sorted(tags.items())), hints=hints)
    schema = _db_schema()
    batches = [entries[k:k + BATCH_SIZE] for k in range(0, len(entries), BATCH_SIZE)]

    def run(batch: list[Entry]):
        if stop.is_set():
            return None
        fps = {e.id: fingerprint(e) for e in batch}
        user = "Prüfe diese Begriffe (ein JSON-Objekt je Zeile):\n" + "\n".join(_entry_json(e) for e in batch)
        data, cost = _ask(client, system, user, schema, effort="medium")
        res = [r for r in data.get("ergebnisse", []) if r.get("id") in fps]
        return res, fps, cost

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(run, b) for b in batches]
        for fut in as_completed(futures):
            try:
                out = fut.result()
            except ClaudeError as e:
                on_batch([], {}, 0.0, str(e))
                if "Schlüssel" in str(e) or "Zugang" in str(e):
                    stop.set()               # alle weiteren Pakete würden genauso scheitern
                continue
            except Exception as e:      # noqa: BLE001 – unerwartet, trotzdem weiterprüfen
                on_batch([], {}, 0.0, f"Unerwarteter Fehler: {e}")
                continue
            if out is not None:
                on_batch(*out, None)


# ---------------------------------------------------------------- Rätsel-Prüfung

PUZZLE_SYSTEM = """Du bist Redakteur für Rätsel in einem gedruckten Magazin und begutachtest ein fertig \
erzeugtes Schwedenrätsel. Das Gitter stammt von einem Algorithmus und ist korrekt verkreuzt; ein Wort lässt \
sich deshalb nicht einzeln austauschen, wohl aber seine Frage (die Antwort bleibt gleich). Jede Frage steht \
in einem eigenen kleinen Kästchen (höchstens rund 35 Zeichen).

Beurteile:
- Themenbezug: Passen die Wörter zum gewählten Thema? Gibt es Füllwörter, die stören (z. B. ein Thema \
"Weihnachten" mit vielen Begriffen aus der Chemie)? Kurze Füllwörter sind nötig und normal.
- Qualität der Fragen: richtig, eindeutig, abwechslungsreich, passend zum Schwierigkeitsgrad.
- Wiederholungen: gleicher Wortstamm, sehr ähnliche Fragen oder zu viele Fragen desselben Musters \
(z. B. lauter "Chem. Zeichen für …").
- Schwierigkeit: Ist das Rätsel ausgewogen?

"woerter": nur Wörter mit einem echten Problem, höchstens 25, die wichtigsten zuerst. "frage_vorschlag" ist \
eine bessere Frage mit derselben Antwort, die auch zum Thema passt, wenn möglich, sonst "". \
"verbesserungen": konkrete Hinweise, was man am Rätsel oder an der Datenbank ändern sollte (z. B. welche \
Wortarten fürs Thema fehlen). Antworte auf Deutsch, knapp und konkret."""


def _puzzle_schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "problem": {"type": "string", "enum": list(RAETSEL_PROBLEME)},
            "hinweis": {"type": "string"},
            "frage_vorschlag": {"type": "string"},
        },
        "required": ["id", "problem", "hinweis", "frage_vorschlag"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "note": {"type": "integer", "enum": list(range(1, 11))},
            "fazit": {"type": "string"},
            "themenbezug": {"type": "string"},
            "staerken": {"type": "array", "items": {"type": "string"}},
            "verbesserungen": {"type": "array", "items": {"type": "string"}},
            "woerter": {"type": "array", "items": item},
        },
        "required": ["note", "fazit", "themenbezug", "staerken", "verbesserungen", "woerter"],
        "additionalProperties": False,
    }


def puzzle_facts(p, topics: set[str], store: ReviewStore | None = None) -> dict:
    """Zahlen, die ohne Claude feststehen: Themenwörter, Füllwörter je Tag, Quellen, Bewertungen."""
    words = [wd for wd in p.words if wd.entry]
    theme = [wd for wd in words if topics and any(t in topics for t in wd.entry.tags)]
    fill = [wd for wd in words if wd not in theme] if topics else []
    fill_tags = Counter(t for wd in fill for t in (wd.entry.tags or ["ohne Tag"]) if t != "kurzwort")
    quellen = Counter(wd.entry.quelle_text for wd in words)
    stars = Counter()
    if store:
        for wd in words:
            r = store.get(wd.entry)
            stars[r["bewertung"] if r else 0] += 1
    return {"woerter": len(words), "themen": sorted(topics), "themenwoerter": len(theme),
            "fuellwoerter": len(fill), "fuell_tags": fill_tags.most_common(), "quellen": quellen.most_common(),
            "bewertungen": dict(stars), "theme_ids": {wd.entry.id for wd in theme}}


def facts_text(f: dict) -> str:
    lines = [f"{f['woerter']} Wörter im Rätsel."]
    if f["themen"]:
        share = 100 * f["themenwoerter"] / max(1, f["woerter"])
        lines.append(f"Themen {', '.join(f['themen'])}: {f['themenwoerter']} Themenwörter ({share:.0f} %), "
                     f"{f['fuellwoerter']} Füllwörter.")
        if f["fuell_tags"]:
            lines.append("Füllwörter nach Tag: " + ", ".join(f"{t} {n}" for t, n in f["fuell_tags"][:10]))
    else:
        lines.append("Kein Thema gewählt: alle Wörter zählen gleich.")
    lines.append("Quellen: " + ", ".join(f"{q} {n}" for q, n in f["quellen"]))
    b = f["bewertungen"]
    if b:
        parts = [f"★{k}: {b[k]}" for k in (5, 4, 3, 2, 1) if b.get(k)]
        if b.get(0):
            parts.append(f"ungeprüft: {b[0]}")
        lines.append("Datenbank-Bewertung der verwendeten Begriffe: " + ", ".join(parts))
    return "\n".join(lines)


def review_puzzle(p, topics: set[str], title: str, facts: dict, hints: str = "") -> tuple[dict, float]:
    client = make_client()
    rows = []
    for wd in sorted(p.words, key=lambda w: (w.dir, w.r, w.c)):
        e = wd.entry
        if not e:
            continue
        rows.append(json.dumps({"id": e.id, "antwort": e.antwort, "buchstaben": wd.wort, "frage": e.frage,
                                "tags": e.tags, "richtung": "waagerecht" if wd.dir == "A" else "senkrecht",
                                "themenwort": e.id in facts["theme_ids"]}, ensure_ascii=False))
    sol = p.solution[0] if p.solution else "(keins)"
    user = (f"Überschrift: {title}\nGewählte Themen: {', '.join(sorted(topics)) or '(keine, alle Begriffe)'}\n"
            f"Raster: {p.w} × {p.h}, Lösungswort: {sol}\n"
            + (f"Bedeutung einzelner Tags:{hints}\n" if hints else "")
            + f"\nFeststehende Zahlen:\n{facts_text(facts)}\n\n"
            "Wörter im Rätsel (ein JSON-Objekt je Zeile):\n" + "\n".join(rows))
    return _ask(client, PUZZLE_SYSTEM, user, _puzzle_schema(), effort="high")
