"""Schwedenrätsel-Generator – jede Frage hat ihr eigenes Kästchen.

Zwei Schritte, wie bei professionellen Kreuzworträtsel-Programmen:

1. MUSTER: Wo liegen Fragefelder, wo Buchstaben? Eine lokale Optimierung (Simulated Annealing)
   sucht ein Muster, in dem
     * jede Buchstabenfolge ab 2 Feldern ("Lücke") ein eigenes, angrenzendes Fragefeld hat
       (waagerecht: links/oben/unten, senkrecht: oben/links/rechts – gerader oder Knickpfeil),
     * jedes Fragefeld genau eine Frage trägt (keine grauen Felder),
     * jeder Buchstabe zu mindestens einem Wort gehört,
     * es keine Lücken gibt, für die die Datenbank zu wenige Wörter hat.
   Ob Fragefelder und Lücken zusammenpassen, prüft ein bipartites Matching.

2. FÜLLEN: Die Lücken werden mit Wörtern belegt (Constraint-Suche: immer die Lücke mit den
   wenigsten passenden Wörtern zuerst, Vorausprüfung der Kreuzungen über Bitmasken,
   Themenwörter werden bevorzugt, kein Wort doppelt).

Viele Muster/Füllversuche laufen parallel in mehreren Prozessen; das beste Ergebnis gewinnt.
"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .puzzle import build_puzzle

sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))

DEFAULTS = {
    "block_ratio": 0.36,     # Startanteil Fragefelder
    "min_words_len": 12,     # so viele Wörter muss es von einer Länge mindestens geben
    "w_two": 1.5,            # Kosten je Zweibuchstaben-Lücke
    "w_long": 0.6,           # Kosten je Buchstabe über 6 in einer Lücke
    "w_bent": 0.15,          # Kosten je Knickpfeil
    "anneal_steps": 4000,
    "fill_nodes": 4000,      # Suchschritte je Füllversuch
    "fill_tries": 3,         # Füllversuche je Muster
    "theme_boost": 4,        # Gewicht von Themenwörtern bei der Kandidatenwahl
    "seed_max": None,        # höchstens so viele vorgegebene Lücken für lange Themenwörter (None = nach Größe)
}


# ====================================================================== 1. Muster

def analyze(blk, W, H, lmax, len_ok, o, allow=frozenset()):
    """Bewertet ein Muster. blk[i] = 1 Fragefeld, 0 Buchstabe."""
    N = W * H
    slots = []                       # (Richtung, Startzelle, Länge, Kandidaten-Fragefelder)
    cover = [0] * N
    for r in range(H):
        base = r * W
        c = 0
        while c < W:
            if blk[base + c]:
                c += 1
                continue
            s = c
            while c < W and not blk[base + c]:
                c += 1
            L = c - s
            if L >= 2:
                st = base + s
                cand = []
                if s > 0:
                    cand.append(st - 1)
                if r > 0 and blk[st - W]:
                    cand.append(st - W)
                if r < H - 1 and blk[st + W]:
                    cand.append(st + W)
                slots.append(("A", st, L, cand))
                for k in range(st, st + L):
                    cover[k] += 1
    for c in range(W):
        r = 0
        while r < H:
            if blk[r * W + c]:
                r += 1
                continue
            s = r
            while r < H and not blk[r * W + c]:
                r += 1
            L = r - s
            if L >= 2:
                st = s * W + c
                cand = []
                if s > 0:
                    cand.append(st - W)
                if c > 0 and blk[st - 1]:
                    cand.append(st - 1)
                if c < W - 1 and blk[st + 1]:
                    cand.append(st + 1)
                slots.append(("D", st, L, cand))
                for k in range(st, st + L * W, W):
                    cover[k] += 1

    owner = {}

    def aug(u, seen):
        for b in slots[u][3]:
            if b in seen:
                continue
            seen.add(b)
            if b not in owner or aug(owner[b], seen):
                owner[b] = u
                return True
        return False

    unmatched = [u for u in sorted(range(len(slots)), key=lambda k: len(slots[k][3])) if not aug(u, set())]
    orphans = [i for i in range(N) if not blk[i] and cover[i] == 0]
    gray = [i for i in range(N) if blk[i] and i not in owner]
    # Längen mit zu wenigen Wörtern sind nur als vorgegebene Lücke für ein Themenwort erlaubt
    bad = [u for u, sl in enumerate(slots)
           if (sl[2] > lmax or not len_ok.get(sl[2])) and (sl[0], sl[1], sl[2]) not in allow]
    # Lücken, die feste Buchstaben kreuzen, brauchen mindestens ein passendes Wort
    fixed_letters, lex = o.get("_fixed_letters"), o.get("_lex")
    if fixed_letters and lex is not None:
        for u, (d, st, L, _) in enumerate(slots):
            if (d, st, L) in allow:
                continue
            if L not in lex.pos:
                continue                 # Länge ohne Wörter ist ohnehin schon als 'bad' gezählt
            step = 1 if d == "A" else W
            m = lex.full[L]
            hit = False
            for k in range(L):
                ch = fixed_letters.get(st + k * step)
                if ch is not None:
                    hit = True
                    m &= lex.pos[L][k][ch]
            if hit and m == 0 and u not in bad:
                bad.append(u)
    two = sum(1 for sl in slots if sl[2] == 2)
    long_ = sum(max(0, sl[2] - 6) for sl in slots)
    bent = sum(1 for b, u in owner.items() if b != slots[u][3][0] or (slots[u][0] == "A" and b != slots[u][1] - 1)
               or (slots[u][0] == "D" and b != slots[u][1] - W))
    cost = (10 * len(unmatched) + 10 * len(orphans) + 10 * len(bad) + 5 * len(gray)
            + o["w_two"] * two + o["w_long"] * long_ + o["w_bent"] * bent)
    hard = len(unmatched) + len(orphans) + len(bad) + len(gray)
    return cost, hard, slots, unmatched, orphans, gray, bad, owner


def _problem_cells(W, H, slots, unmatched, orphans, gray, bad):
    cells = []
    for u in unmatched + bad:
        d, st, L, _ = slots[u]
        r, c = divmod(st, W)
        cells += [st, st + (1 if d == "A" else W)]
        for dr, dc in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            if 0 <= r + dr < H and 0 <= c + dc < W:
                cells.append((r + dr) * W + c + dc)
        if L > 4:
            mid = st + (L // 2) * (1 if d == "A" else W)
            cells.append(mid)
    for i in orphans + gray:
        r, c = divmod(i, W)
        cells.append(i)
        for dr, dc in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            if 0 <= r + dr < H and 0 <= c + dc < W:
                cells.append((r + dr) * W + c + dc)
    return cells


def fits(W, H, L) -> bool:
    """Passt eine Lücke der Länge L (mit Fragefeld davor) in das Raster?"""
    return L + 1 <= max(W, H)


def _place_run(W, H, L, frozen, rng, tries, edge=0.0):
    """Sucht eine freie Lage für eine Lücke der Länge L. Liefert (Richtung, Start, {Zelle: Wert}) oder None.

    edge – Anteil der Versuche, bei denen das Wort am Rand endet (spart ein festes Fragefeld)."""
    for _ in range(tries):
        d = rng.choice("AD")
        span, other = (W, H) if d == "A" else (H, W)
        if L + 1 > span:
            d = "D" if d == "A" else "A"
            span, other = (W, H) if d == "A" else (H, W)
            if L + 1 > span:
                return None
        s = span - L if rng.random() < edge else rng.randint(1, span - L)   # erster Buchstabe
        line = rng.randrange(other)
        cell = (lambda p: line * W + p) if d == "A" else (lambda p: p * W + line)
        want = {cell(s - 1): 1}
        want.update({cell(p): 0 for p in range(s, s + L)})
        if s + L < span:
            want[cell(s + L)] = 1
        if all(frozen.get(i, v) == v and (v == 1 or i not in frozen) for i, v in want.items()):
            return d, cell(s), want
    return None


def _seed_runs(W, H, seed_lens, seed_max, rng, fixed=()):
    """Legt feste Lücken an (Fragefeld davor, Ende am Rand oder Fragefeld):

    * für jedes vorgegebene Wort (`fixed`) genau eine Lücke, die nur mit diesem Wort gefüllt wird,
    * zufällig einige für lange Themenwörter – ohne diese Vorgabe entstehen solche Längen im
      Muster nie, weil es von ihnen zu wenige Wörter gibt.

    Liefert ({Zelle: 1 Fragefeld / 0 Buchstabe}, {(Richtung, Start, Länge)}, {(Richtung, Start, Länge): Wort})
    oder None, wenn ein vorgegebenes Wort keinen Platz fand. Die Zellen bleiben beim Optimieren fest."""
    frozen: dict[int, int] = {}
    allow, forced = set(), {}
    for w in sorted(fixed, key=len, reverse=True):          # lange zuerst, die brauchen am meisten Platz
        res = _place_run(W, H, len(w), frozen, rng, 200, edge=0.5)
        if res is None:
            return None
        d, st, want = res
        frozen.update(want)
        allow.add((d, st, len(w)))
        forced[(d, st, len(w))] = w
    if seed_lens and seed_max > 0:
        for L in rng.sample(seed_lens, min(len(seed_lens), rng.randint(1, seed_max))):
            res = _place_run(W, H, L, frozen, rng, 40)
            if res is not None:
                d, st, want = res
                frozen.update(want)
                allow.add((d, st, L))
    return frozen, allow, forced


def make_pattern(W, H, lmax, len_ok, o, rng, deadline):
    """Sucht ein gültiges Muster (keine Regelverletzung, keine grauen Felder)."""
    N = W * H
    blk = bytearray(1 if rng.random() < o["block_ratio"] else 0 for _ in range(N))
    seeds = _seed_runs(W, H, o.get("seed_lens") or [], o.get("seed_max", 0), rng, o.get("fixed") or ())
    if seeds is None:
        return None
    frozen, allow, forced = seeds
    if forced:
        letters = {}
        for (d, st, L), w in forced.items():
            for k, ch in enumerate(w):
                letters[st + k * (1 if d == "A" else W)] = ord(ch) - 64
        o = dict(o, _fixed_letters=letters)
    for i, v in frozen.items():
        blk[i] = v
    if 0 not in frozen:
        blk[0] = 1
    cost, hard, *rest = analyze(blk, W, H, lmax, len_ok, o, allow)
    steps = int(o["anneal_steps"])
    t0, t1 = 2.5, 0.15
    for k in range(steps):
        if hard == 0:
            return blk, rest[0], rest[-1], forced
        if k % 200 == 0 and time.perf_counter() > deadline:
            return None
        T = t0 * (t1 / t0) ** (k / steps)
        probs = _problem_cells(W, H, *rest[:5]) if rng.random() < 0.75 else None
        i = rng.choice(probs) if probs else rng.randrange(N)
        if i in frozen:
            continue
        blk[i] ^= 1
        nc, nh, *nrest = analyze(blk, W, H, lmax, len_ok, o, allow)
        if nc <= cost or rng.random() < math.exp((cost - nc) / T):
            cost, hard, rest = nc, nh, nrest
        else:
            blk[i] ^= 1
    if hard == 0:
        return blk, rest[0], rest[-1], forced
    return None


# ====================================================================== 2. Füllen

class Lexicon:
    """Wörter nach Länge, mit Bitmasken je (Länge, Position, Buchstabe)."""

    def __init__(self, words, theme):
        self.by_len: dict[int, list[str]] = {}
        for w in words:
            self.by_len.setdefault(len(w), []).append(w)
        self.full, self.pos, self.theme_mask = {}, {}, {}
        self.index = {w: k for ws in self.by_len.values() for k, w in enumerate(ws)}
        for L, ws in self.by_len.items():
            self.full[L] = (1 << len(ws)) - 1
            masks = [[0] * 27 for _ in range(L)]
            tm = 0
            for k, w in enumerate(ws):
                bit = 1 << k
                for p, ch in enumerate(w):
                    masks[p][ord(ch) - 64] |= bit
                if theme and w in theme:
                    tm |= bit
            self.pos[L] = masks
            self.theme_mask[L] = tm
        self.theme = theme

    def counts(self):
        return {L: len(ws) for L, ws in self.by_len.items()}


class _Abort(Exception):
    pass


def fill(W, H, slots, lex, rng, node_limit, theme_first=True, theme_boost=4, forced=None):
    """Belegt alle Lücken mit Wörtern. Liefert Zellwerte (0 Fragefeld, 1..26 Buchstabe) oder None.

    forced – {(Richtung, Start, Länge): Wort}: diese Lücken bekommen genau dieses Wort."""
    N = W * H
    n = len(slots)
    sl_cells = []
    cell_slots = [[] for _ in range(N)]
    for u, (d, st, L, _) in enumerate(slots):
        step = 1 if d == "A" else W
        cells = [st + k * step for k in range(L)]
        sl_cells.append(cells)
        for p, ci in enumerate(cells):
            cell_slots[ci].append((u, p))
    dom = [lex.full.get(sl[2], 0) for sl in slots]
    for u, sl in enumerate(slots):
        w = forced.get(sl[:3]) if forced else None
        if w is not None:
            dom[u] = 1 << lex.index[w] if w in lex.index else 0
    if any(x == 0 for x in dom):
        return None
    # Reihenfolge der Kandidaten je Länge: Themenwörter zuerst, sonst zufällig
    order = {}
    for L, ws in lex.by_len.items():
        ids = list(range(len(ws)))
        rng.shuffle(ids)
        if theme_first and lex.theme:
            tm = lex.theme_mask[L]
            ids.sort(key=lambda k: 0 if (tm >> k) & 1 else 1)
        order[L] = ids
    assigned = [-1] * n
    used = set()
    nodes = [0]
    letters = [0] * N

    def pick_slot():
        best, best_cnt = -1, None
        for u in range(n):
            if assigned[u] < 0:
                cnt = dom[u].bit_count()
                if best_cnt is None or cnt < best_cnt or (cnt == best_cnt and rng.random() < 0.3):
                    best, best_cnt = u, cnt
        return best

    def lookahead(u, w):
        """Kleinste Zahl verbleibender Wörter in einer kreuzenden Lücke (0 = Sackgasse)."""
        best = 1 << 30
        for p, ci in enumerate(sl_cells[u]):
            x = ord(w[p]) - 64
            for (t, q) in cell_slots[ci]:
                if t == u or assigned[t] >= 0:
                    continue
                cnt = (dom[t] & lex.pos[slots[t][2]][q][x]).bit_count()
                if cnt < best:
                    best = cnt
                    if cnt == 0:
                        return 0
        return best

    def candidates(u):
        """Bis zu 14 Kandidaten, sortiert nach Vorausschau (Themenwörter zählen doppelt)."""
        L = slots[u][2]
        ws, d = lex.by_len[L], dom[u]
        tm = lex.theme_mask.get(L, 0)
        found = []
        for k in order[L]:
            if (d >> k) & 1 and ws[k] not in used:
                la = lookahead(u, ws[k])
                if la > 0:
                    found.append((la * (theme_boost if (tm >> k) & 1 else 1) * (0.7 + 0.6 * rng.random()), k))
                    if len(found) >= 14:
                        break
        found.sort(reverse=True)
        return [k for _, k in found]

    def solve(left):
        if left == 0:
            return True
        u = pick_slot()
        L = slots[u][2]
        ws = lex.by_len[L]
        for k in candidates(u):
            w = ws[k]
            nodes[0] += 1
            if nodes[0] > node_limit:
                raise _Abort
            changes = []
            ok = True
            for p, ci in enumerate(sl_cells[u]):
                x = ord(w[p]) - 64
                for (t, q) in cell_slots[ci]:
                    if t == u or assigned[t] >= 0:
                        continue
                    nd = dom[t] & lex.pos[slots[t][2]][q][x]
                    if nd == 0:
                        ok = False
                        break
                    if nd != dom[t]:
                        changes.append((t, dom[t]))
                        dom[t] = nd
                if not ok:
                    break
            if ok:
                assigned[u] = k
                used.add(w)
                if solve(left - 1):
                    return True
                used.discard(w)
                assigned[u] = -1
            for t, old in reversed(changes):
                dom[t] = old
        return False

    try:
        if not solve(n):
            return None
    except _Abort:
        return None
    for u, (d, st, L, _) in enumerate(slots):
        w = lex.by_len[L][assigned[u]]
        for p, ci in enumerate(sl_cells[u]):
            letters[ci] = ord(w[p]) - 64
    return letters


# ====================================================================== Bewertung

def quality(slots, letters, owner, theme, W):
    lens = [sl[2] for sl in slots] or [0]
    two = sum(1 for x in lens if x == 2)
    words = []
    for d, st, L, _ in slots:
        step = 1 if d == "A" else W
        words.append("".join(chr(64 + letters[st + k * step]) for k in range(L)))
    tw = [w for w in words if theme and w in theme]
    th = len(tw) + 0.5 * sum(len(w) for w in tw)        # lange Themenwörter zählen mehr
    n_letters = sum(1 for x in letters if x > 0)
    bent = 0
    for b, u in owner.items():
        d, st = slots[u][0], slots[u][1]
        if b != (st - 1 if d == "A" else st - W):
            bent += 1
    # weniger Zweibuchstaben-Wörter, längere Wörter, mehr Buchstaben, mehr Thema, wenige Knickpfeile
    q = two * 1.2 - (sum(lens) / len(lens)) * 3 - n_letters * 0.1 - th * 1.5 + bent * 0.3
    return q, len(tw), sum(len(w) for w in tw)


def score(q, n_theme, theme_letters, maximize_theme=False):
    """Vergleichswert (kleiner = besser). Im Themen-Modus zählt zuerst die Zahl der Themenwörter."""
    return (-n_theme, -theme_letters, q) if maximize_theme else (q,)


# ====================================================================== Arbeitsprozesse

_WORKER: dict = {}


def _init_worker(W, H, words, theme, opts):
    lex = Lexicon(words, theme)
    counts = lex.counts()
    lim = max(W, H)
    len_ok = {L: cnt >= opts["min_words_len"] for L, cnt in counts.items()}
    lmax = max([L for L, ok in len_ok.items() if ok] or [2])
    # Themenwörter, deren Länge sonst kaum vorkommt, bekommen gezielt vorgegebene Lücken
    fixed = list(opts.get("fixed") or ())
    seed_lens = sorted(len(w) for w in theme if len(w) < lim and not len_ok.get(len(w)) and w not in fixed)
    # Feste Wörter: lange bekommen eine vorgegebene Lücke im Muster, kurze eine passende Lücke beim Füllen
    opts = dict(opts, seed_lens=seed_lens,
                fixed=[w for w in fixed if not len_ok.get(len(w))],
                fixed_short=[w for w in fixed if len_ok.get(len(w))])
    if opts.get("seed_max") is None:
        opts["seed_max"] = max(1, W * H // 80)
    _WORKER.update(W=W, H=H, theme=theme, opts=opts, lex=lex, len_ok=len_ok, lmax=min(lmax, max(W, H)))


def _place_short(slots, forced, words, rng):
    """Weist jedem kurzen festen Wort eine zufällige freie Lücke passender Länge zu."""
    if not words:
        return forced
    out = dict(forced)
    for w in words:
        free = [sl[:3] for sl in slots if sl[2] == len(w) and sl[:3] not in out]
        if not free:
            return None
        out[rng.choice(free)] = w
    return out


def _task(seconds, seed, tweaks=None):
    st = _WORKER
    W, H, lex = st["W"], st["H"], st["lex"]
    o = {**st["opts"], **(tweaks or {})}
    grow = max(1.0, W * H / 195)             # größere Raster brauchen mehr Suchschritte
    o["anneal_steps"] = o["anneal_steps"] * grow
    o["fill_nodes"] = o["fill_nodes"] * grow * grow
    o["_lex"] = lex
    rng = random.Random(seed)
    end = time.perf_counter() + seconds
    best = None
    stats = {"patterns": 0, "fills": 0, "found": 0}
    while time.perf_counter() < end:
        pat = make_pattern(W, H, st["lmax"], st["len_ok"], o, rng, end)
        if pat is None:
            continue
        blk, slots, owner, forced = pat
        stats["patterns"] += 1
        for _ in range(int(o["fill_tries"])):
            if time.perf_counter() > end:
                break
            f_all = _place_short(slots, forced, o.get("fixed_short") or (), rng)
            if f_all is None:
                break
            stats["fills"] += 1
            letters = fill(W, H, slots, lex, rng, int(o["fill_nodes"]), theme_boost=o["theme_boost"],
                           forced=f_all)
            if letters is not None:
                stats["found"] += 1
                q, nth, nlet = quality(slots, letters, owner, st["theme"], W)
                sc = score(q, nth, nlet, o.get("maximize_theme", False))
                if best is None or sc < best[0]:
                    best = (sc, letters, nth)
                break
    return best, stats


def generate(entries, width, height, seconds=10.0, theme_words=None, prefer=None,
             progress=None, stop_event=None, workers=None, runs=None, fixed=None, **overrides):
    """Erzeugt ein Rätsel. Liefert (Puzzle, info) oder (None, info).

    entries      – erlaubte Einträge (Thema + Füllwörter)
    theme_words  – Menge normalisierter Themenwörter (werden bevorzugt)
    prefer       – Funktion(entry) -> bool: Fragen aus dem Thema bevorzugen
    progress     – Rückruf(dict) für Fortschrittsanzeigen
    stop_event   – threading.Event zum vorzeitigen Beenden
    runs         – Themen-Modus: so viele fertige Rätsel erzeugen und das mit den meisten
                   Themenwörtern behalten ('seconds' ist dann nur die Obergrenze)
    fixed        – Einträge, die auf jeden Fall (mit genau dieser Frage) ins Rätsel kommen
    """
    if runs:
        overrides["maximize_theme"] = True
    W, H = width, height
    fixed = [e for e in (fixed or []) if fits(W, H, len(e.wort))]
    fixed_words = list(dict.fromkeys(e.wort for e in fixed))
    if fixed_words:
        overrides["fixed"] = fixed_words
    by_word: dict[str, list] = {}
    for e in list(entries) + [e for e in fixed if e not in entries]:
        w = e.wort
        if 2 <= len(w) <= max(W, H) and e not in by_word.get(w, []):
            by_word.setdefault(w, []).append(e)
    info = {"patterns": 0, "fills": 0, "found": 0, "elapsed": 0.0, "seconds": seconds,
            "runs": runs, "best_theme": None}
    if not by_word:
        return None, info
    theme = frozenset(theme_words) if theme_words else frozenset()
    o = {**DEFAULTS, **overrides}
    n = workers or max(1, (os.cpu_count() or 2) - 1)
    start = time.perf_counter()
    deadline = start + seconds
    best = None
    seed = random.randrange(1 << 30)

    with ProcessPoolExecutor(max_workers=n, initializer=_init_worker,
                             initargs=(W, H, list(by_word), theme, o)) as ex:
        pending = set()

        def submit():
            nonlocal seed
            left = deadline - time.perf_counter()
            if left < 0.3 or (stop_event is not None and stop_event.is_set()):
                return
            if runs and info["found"] >= runs:
                return
            seed += 1
            # abwechselnd strenge (wenige Zweibuchstaben-Wörter) und lockere Muster, jeweils mit und
            # ohne vorgegebene Lücken für lange Themenwörter – die Bewertung wählt
            tweaks = {} if seed % 2 else {"w_two": o["w_two"] / 3}
            if seed % 4 >= 2:
                tweaks["seed_max"] = 0
            pending.add(ex.submit(_task, min(2.0, left), seed, tweaks))

        for _ in range(n):
            submit()
        while pending:
            done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for f in done:
                pending.discard(f)
                res, stats = f.result()
                for k, v in stats.items():
                    info[k] += v
                if res is not None and (best is None or res[0] < best[0]):
                    best = res
                    info["best_theme"] = res[2]
                if not (best is not None and stop_event is not None and stop_event.is_set()):
                    submit()
            info["elapsed"] = time.perf_counter() - start
            if progress:
                progress(dict(info, has_result=best is not None))

    if best is None:
        return None, info
    puzzle = build_puzzle(W, H, best[1], by_word, prefer, fixed_ids={e.id for e in fixed})
    return puzzle, info
