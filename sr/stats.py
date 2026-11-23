"""Kennzahlen der Wortdatenbank und Vergleichsverteilungen für das Dashboard."""
from __future__ import annotations

from collections import Counter

from .words import Entry

# Buchstabenhäufigkeit im deutschen Fließtext (in %, übliche Tabellenwerte).
# Umlaute und ß werden wie im Rätsel aufgelöst: Ä -> A+E, Ö -> O+E, Ü -> U+E, ß -> S+S.
_GERMAN_RAW = {
    "E": 17.40, "N": 9.78, "I": 7.55, "S": 7.27, "R": 7.00, "A": 6.51, "T": 6.15, "D": 5.08,
    "H": 4.76, "U": 4.35, "L": 3.44, "C": 3.06, "G": 3.01, "M": 2.53, "O": 2.51, "B": 1.89,
    "W": 1.89, "F": 1.66, "K": 1.21, "Z": 1.13, "P": 0.79, "V": 0.67, "J": 0.27, "Y": 0.04,
    "X": 0.03, "Q": 0.02,
}
_UMLAUTE = {"Ä": (0.54, "AE"), "Ö": (0.30, "OE"), "Ü": (0.65, "UE"), "ß": (0.31, "SS")}


def _german_letters() -> dict[str, float]:
    f = dict(_GERMAN_RAW)
    for share, repl in _UMLAUTE.values():
        for ch in repl:
            f[ch] += share
    total = sum(f.values())
    return {ch: 100 * v / total for ch, v in f.items()}


GERMAN_LETTERS = _german_letters()

# Anteil der Lückenlängen, die der Generator in seinen Mustern verlangt
# (gemessen an 47 Mustern 11×13, 13×15 und 15×17 mit 2948 Lücken, Stand November 2026).
SLOT_DEMAND = {2: 32.6, 3: 26.8, 4: 16.7, 5: 9.9, 6: 6.9, 7: 4.1, 8: 2.2, 9: 0.8}

LENGTH_BINS = [2, 3, 4, 5, 6, 7, 8, 9, 10]     # 10 = "10+"


def filter_source(entries: list[Entry], source: str) -> list[Entry]:
    if source == "eigene":
        return [e for e in entries if e.quelle != "auto"]
    if source == "automatisch":
        return [e for e in entries if e.quelle == "auto"]
    return list(entries)


def overview(entries: list[Entry]) -> dict:
    words = {e.wort for e in entries if e.wort}
    tags = {t for e in entries for t in e.tags}
    lens = [len(w) for w in words]
    return {
        "begriffe": len(entries),
        "eigene": sum(1 for e in entries if e.quelle != "auto"),
        "automatisch": sum(1 for e in entries if e.quelle == "auto"),
        "antworten": len(words),
        "themen": len(tags),
        "avg_len": sum(lens) / len(lens) if lens else 0.0,
        "ohne_tag": sum(1 for e in entries if not e.tags),
        "mehrfach": sum(1 for c in Counter(e.wort for e in entries).values() if c > 1),
    }


def length_distribution(entries: list[Entry]) -> list[dict]:
    """Verschiedene Antworten je Wortlänge, Anteil in % und Bedarf des Generators in %."""
    words = {e.wort for e in entries if len(e.wort) >= 2}
    cnt = Counter(min(len(w), 10) for w in words)
    total = sum(cnt.values()) or 1
    rows = []
    for L in LENGTH_BINS:
        demand = sum(v for k, v in SLOT_DEMAND.items() if (k >= 10 if L == 10 else k == L))
        rows.append({"label": f"{L}+" if L == 10 else str(L), "count": cnt.get(L, 0),
                     "share": 100 * cnt.get(L, 0) / total, "expected": demand})
    return rows


def letter_distribution(entries: list[Entry]) -> list[dict]:
    """Buchstabenanteile über alle verschiedenen Antworten im Vergleich zum Deutschen."""
    words = {e.wort for e in entries if e.wort}
    cnt = Counter(ch for w in words for ch in w)
    total = sum(cnt.values()) or 1
    return [{"label": ch, "count": cnt.get(ch, 0), "share": 100 * cnt.get(ch, 0) / total,
             "expected": GERMAN_LETTERS[ch]} for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]


def tag_distribution(entries: list[Entry]) -> list[dict]:
    """Begriffe je Thema, getrennt nach eigenen und automatisch erzeugten."""
    own, auto = Counter(), Counter()
    for e in entries:
        for t in e.tags or ["(ohne Tag)"]:
            (auto if e.quelle == "auto" else own)[t] += 1
    tags = set(own) | set(auto)
    rows = [{"label": t, "own": own[t], "auto": auto[t], "count": own[t] + auto[t]} for t in tags]
    rows.sort(key=lambda r: (-r["count"], r["label"]))
    return rows


def biggest_gaps(rows: list[dict], n: int = 2) -> list[dict]:
    """Kategorien, in denen der Ist-Anteil am weitesten unter dem erwarteten liegt."""
    gaps = sorted(rows, key=lambda r: r["share"] - r["expected"])
    return [r for r in gaps[:n] if r["share"] < r["expected"]]
