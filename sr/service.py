"""Gemeinsame Abläufe für Oberfläche und Kommandozeile: Wortauswahl nach Themen, Lösungswort."""
from __future__ import annotations

import random

from .puzzle import Puzzle, assign_solution, missing_letters
from .words import Entry, normalize


def is_filler(e: Entry) -> bool:
    """Kurze Füllwörter (Tag 'kurzwort' oder bis 3 Buchstaben) – nötig für dichte Raster."""
    return "kurzwort" in e.tags or len(e.wort) <= 3


AVOID_TAG = "meiden"     # Begriffe mit diesem Tag kommen nie ins Rätsel (z. B. nach der Prüfung durch Claude)


def by_source(entries: list[Entry], use_auto: bool = True, skip=None) -> list[Entry]:
    """Ohne automatisch erzeugte Begriffe: nur selbst eingegebene (und später Einsendungen).

    Begriffe mit dem Tag 'meiden' fallen immer weg, dazu alle, für die skip(e) wahr ist."""
    return [e for e in entries if (use_auto or e.quelle != "auto") and AVOID_TAG not in e.tags
            and not (skip and skip(e))]


def theme_pool(entries: list[Entry], topics: set[str], use_auto: bool = True, skip=None) -> list[Entry]:
    entries = by_source(entries, use_auto, skip)
    if not topics:
        return entries
    return [e for e in entries if any(t in topics for t in e.tags)]


def word_pool(entries: list[Entry], topics: set[str], filler: bool = True, use_auto: bool = True,
              skip=None) -> list[Entry]:
    if not topics or not filler:
        return theme_pool(entries, topics, use_auto, skip)
    return [e for e in by_source(entries, use_auto, skip) if any(t in topics for t in e.tags) or is_filler(e)]


def preferred_words(entries: list[Entry], topics: set[str], use_auto: bool = True, skip=None) -> set[str] | None:
    """Wörter, die beim Füllen bevorzugt werden: mit Themen die Themenwörter, sonst die selbst eingegebenen."""
    if topics:
        return {e.wort for e in theme_pool(entries, topics, use_auto, skip)}
    own = {e.wort for e in by_source(entries, True, skip) if e.quelle != "auto"}
    return own or None


def pick_solution(p: Puzzle, mode: str, manual: str, min_len: int, max_len: int,
                  candidates: list[Entry], avoid: str | None = None, rng: random.Random | None = None):
    """Setzt p.solution. Liefert eine Meldung (str) bei Problemen, sonst None."""
    rng = rng or random.Random()
    if mode == "none":
        p.solution = None
        return None
    if mode == "manual":
        w = normalize(manual)
        if len(w) < 2:
            return "Bitte ein eigenes Lösungswort eingeben."
        cells = assign_solution(p, w, rng)
        if cells is None:
            miss = ", ".join(missing_letters(p, w))
            return f"Lösungswort „{w}“ passt nicht – im Rätsel fehlen die Buchstaben: {miss}."
        p.solution = (w, cells)
        return None
    in_grid = {wd.wort for wd in p.words}
    words = sorted({e.wort for e in candidates if min_len <= len(e.wort) <= max_len and e.wort not in in_grid})
    rng.shuffle(words)
    for w in words:
        if w == avoid:
            continue
        cells = assign_solution(p, w, rng)
        if cells:
            p.solution = (w, cells)
            return None
    return "Kein passendes Lösungswort gefunden (Länge anpassen oder neu erzeugen)."
