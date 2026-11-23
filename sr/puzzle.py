"""Rätsel-Datenmodell.

Raster-Zellen (zeilenweise, Index = r * w + c):
    Buchstabenfeld  -> letters[i] = 'A'..'Z'
    Fragefeld       -> clue_of[i] = Index des Wortes (genau EINE Frage pro Feld)
    graues Feld     -> weder noch

Pfeilarten (wo das Fragefeld relativ zum ersten Buchstaben liegt):
    waagerechte Wörter (A):  R  links    | DR oben (Pfeil runter, dann rechts) | UR unten (hoch, dann rechts)
    senkrechte Wörter (D):   D  oben     | RD links (rechts, dann runter)      | LD rechts (links, dann runter)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .words import Entry

# kind -> (Richtung, dr, dc) des Fragefeldes relativ zum Wortanfang
KINDS = {
    "R": ("A", 0, -1), "DR": ("A", -1, 0), "UR": ("A", 1, 0),
    "D": ("D", -1, 0), "RD": ("D", 0, -1), "LD": ("D", 0, 1),
}
KINDS_FOR = {"A": ("R", "DR", "UR"), "D": ("D", "RD", "LD")}   # gerade Pfeile zuerst


@dataclass
class Word:
    wort: str
    r: int
    c: int
    dir: str            # 'A' waagerecht, 'D' senkrecht
    entry: Optional[Entry] = None
    kind: str = ""
    clue: int = -1      # Zellindex des Fragefeldes

    def cells(self, w: int) -> list[int]:
        step = 1 if self.dir == "A" else w
        start = self.r * w + self.c
        return [start + k * step for k in range(len(self.wort))]


@dataclass
class Puzzle:
    w: int
    h: int
    letters: list                       # str oder None
    clue_of: list                       # Wortindex oder None
    words: list[Word]
    solution: Optional[tuple[str, list[int]]] = None   # (Lösungswort, Zellindizes)

    def is_letter(self, i: int) -> bool:
        return self.letters[i] is not None

    def gray_cells(self) -> int:
        return sum(1 for i in range(self.w * self.h) if self.letters[i] is None and self.clue_of[i] is None)

    def stats(self) -> dict:
        n_letters = sum(1 for x in self.letters if x)
        cover = [0] * (self.w * self.h)
        for wd in self.words:
            for i in wd.cells(self.w):
                cover[i] += 1
        double = sum(1 for i, x in enumerate(self.letters) if x and cover[i] >= 2)
        lens = [len(wd.wort) for wd in self.words] or [0]
        return {
            "words": len(self.words),
            "gray": self.gray_cells(),
            "letters": n_letters,
            "double": double / max(1, n_letters),
            "avg_len": sum(lens) / len(lens),
            "two_letter": sum(1 for x in lens if x == 2),
            "bent": sum(1 for wd in self.words if wd.kind in ("DR", "UR", "RD", "LD")),
        }


# ---------------------------------------------------------------- Wörter & Zuordnung

def find_runs(cells: list[int], w: int, h: int) -> list[Word]:
    """Alle Buchstabenfolgen ab Länge 2 (cells: >0 Buchstabe 1..26, sonst kein Buchstabe)."""
    out = []
    for r in range(h):
        c = 0
        while c < w:
            if cells[r * w + c] > 0:
                s = c
                while c < w and cells[r * w + c] > 0:
                    c += 1
                if c - s >= 2:
                    out.append(Word("".join(chr(64 + cells[r * w + k]) for k in range(s, c)), r, s, "A"))
            else:
                c += 1
    for c in range(w):
        r = 0
        while r < h:
            if cells[r * w + c] > 0:
                s = r
                while r < h and cells[r * w + c] > 0:
                    r += 1
                if r - s >= 2:
                    out.append(Word("".join(chr(64 + cells[k * w + c]) for k in range(s, r)), s, c, "D"))
            else:
                r += 1
    return out


def clue_candidates(word: Word, cells: list[int], w: int, h: int) -> list[tuple[int, str]]:
    """Mögliche Fragefelder (Blöcke = cells[i] == 0) für ein Wort, gerade Pfeile zuerst."""
    out = []
    for kind in KINDS_FOR[word.dir]:
        _, dr, dc = KINDS[kind]
        r, c = word.r + dr, word.c + dc
        if 0 <= r < h and 0 <= c < w and cells[r * w + c] == 0:
            out.append((r * w + c, kind))
    return out


def match_clues(words: list[Word], cells: list[int], w: int, h: int) -> Optional[dict[int, tuple[int, str]]]:
    """Ordnet jedem Wort ein eigenes Fragefeld zu (bipartites Matching). None, wenn unmöglich."""
    cands = [clue_candidates(wd, cells, w, h) for wd in words]
    owner: dict[int, int] = {}

    def augment(u: int, seen: set) -> bool:
        for b, _ in cands[u]:
            if b in seen:
                continue
            seen.add(b)
            if b not in owner or augment(owner[b], seen):
                owner[b] = u
                return True
        return False

    for u in sorted(range(len(words)), key=lambda k: len(cands[k])):
        if not augment(u, set()):
            return None
    result = {}
    for b, u in owner.items():
        kind = next(k for bb, k in cands[u] if bb == b)
        result[u] = (b, kind)
    return result


def build_puzzle(w: int, h: int, cells: list[int], by_word: dict[str, list[Entry]],
                 prefer=None, rng: random.Random | None = None, fixed_ids=frozenset()) -> Puzzle:
    """Baut aus einem gefüllten Raster (0 = Block, 1..26 = Buchstabe) ein fertiges Rätsel.

    fixed_ids – IDs fest vorgegebener Einträge: deren Frage wird für ihr Wort immer genommen."""
    rng = rng or random.Random()
    words = find_runs(cells, w, h)
    m = match_clues(words, cells, w, h)
    if m is None:
        raise ValueError("Raster hat keine gültige Fragezuordnung")
    letters = [chr(64 + x) if x > 0 else None for x in cells]
    clue_of = [None] * (w * h)
    for u, wd in enumerate(words):
        b, kind = m[u]
        wd.clue, wd.kind = b, kind
        clue_of[b] = u
        options = by_word.get(wd.wort, [])
        fx = [e for e in options if e.id in fixed_ids]
        if fx:
            wd.entry = rng.choice(fx)
            continue
        if prefer:
            pref = [e for e in options if prefer(e)]
            options = pref or options
        own = [e for e in options if e.quelle != "auto"]     # gleiche Antwort: selbst eingegebene Frage bevorzugen
        options = own or options
        wd.entry = rng.choice(options) if options else None
    return Puzzle(w, h, letters, clue_of, words)


# ---------------------------------------------------------------- Lösungswort

def assign_solution(p: Puzzle, word: str, rng: random.Random | None = None) -> Optional[list[int]]:
    """Verteilt die Buchstaben des Lösungswortes möglichst gestreut auf Buchstabenfelder."""
    rng = rng or random.Random()
    by_letter: dict[str, list[int]] = {}
    for i, ch in enumerate(p.letters):
        if ch:
            by_letter.setdefault(ch, []).append(i)
    chosen: list[int] = []
    for ch in word:
        opts = [i for i in by_letter.get(ch, []) if i not in chosen]
        if not opts:
            return None
        best, best_d = None, -1.0
        for i in opts:
            r, c = divmod(i, p.w)
            d = min((abs(r - r2) + abs(c - c2) for r2, c2 in (divmod(j, p.w) for j in chosen)), default=0)
            d += rng.random() * 1.5
            if d > best_d:
                best, best_d = i, d
        chosen.append(best)
    return chosen


def missing_letters(p: Puzzle, word: str) -> list[str]:
    have: dict[str, int] = {}
    for ch in p.letters:
        if ch:
            have[ch] = have.get(ch, 0) + 1
    miss = []
    for ch in word:
        if have.get(ch):
            have[ch] -= 1
        else:
            miss.append(ch)
    return miss


# ---------------------------------------------------------------- Prüfung

def validate(p: Puzzle) -> list[str]:
    """Prüft alle Regeln; liefert eine Liste von Fehlern (leer = in Ordnung)."""
    errs = []
    cells = [ord(x) - 64 if x else (0 if p.clue_of[i] is not None else -2) for i, x in enumerate(p.letters)]
    runs = {(wd.dir, wd.r, wd.c, wd.wort) for wd in find_runs(cells, p.w, p.h)}
    have = {(wd.dir, wd.r, wd.c, wd.wort) for wd in p.words}
    errs += [f"Buchstabenfolge ohne Frage: {x}" for x in runs - have]
    errs += [f"Wort ist keine Buchstabenfolge: {x}" for x in have - runs]
    seen = set()
    for u, wd in enumerate(p.words):
        if wd.wort in seen:
            errs.append(f"doppeltes Wort {wd.wort}")
        seen.add(wd.wort)
        if wd.entry is None or wd.entry.wort != wd.wort:
            errs.append(f"keine passende Frage für {wd.wort}")
        _, dr, dc = KINDS[wd.kind]
        if KINDS[wd.kind][0] != wd.dir or wd.clue != (wd.r + dr) * p.w + wd.c + dc:
            errs.append(f"Fragefeld liegt falsch: {wd.wort}")
        if p.clue_of[wd.clue] != u:
            errs.append(f"Fragefeld gehört nicht zum Wort: {wd.wort}")
    in_word = set()
    for wd in p.words:
        in_word.update(wd.cells(p.w))
    for i, x in enumerate(p.letters):
        if x and i not in in_word:
            errs.append(f"Buchstabe ohne Wort bei {divmod(i, p.w)}")
    return errs
