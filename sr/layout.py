"""Seitenlayout eines Rätselblatts – unabhängig vom Ausgabeziel.

`sheet_ops()` liefert eine Liste einfacher Zeichenbefehle in Punkt (1 pt = 1/72 Zoll), Ursprung oben
links, y nach unten. Daraus zeichnen sowohl der PDF-Export als auch die Vorschau in der Oberfläche.

    ("rect",   x, y, w, h, fill_grau_oder_None, linienbreite)
    ("text",   x_mitte, y_mitte, text, schrift, größe)
    ("textl",  x_links, y_mitte, text, schrift, größe)
    ("poly",   [(x, y), ...])                 # gefüllt, schwarz
    ("line",   [(x, y), ...], linienbreite)
    ("circle", x_mitte, y_mitte, radius, linienbreite)
"""
from __future__ import annotations

from reportlab.pdfbase.pdfmetrics import stringWidth

MM = 72 / 25.4
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
LEADING = 1.06


def measure(text: str, size: float, font: str = FONT) -> float:
    return stringWidth(text, font, size)


# ---------------------------------------------------------------- Silbentrennung

_VOWELS = set("aeiouyäöü")
_CLUSTERS = ("sch", "ch", "ck", "ph", "qu")
_ONSETS = ("schl", "schr", "schw", "str", "spr", "bl", "br", "pl", "pr", "kl", "kr",
           "gl", "gr", "fl", "fr", "dr", "tr", "st", "sp")


def hyphenate_word(word: str) -> list[str]:
    """Einfache deutsche Silbentrennung: zwischen zwei Vokalgruppen geht der letzte Konsonant
    (sch/ch/ck/ph zählen als einer) in die nächste Silbe; typische Silbenanfänge bleiben zusammen."""
    if len(word) < 7 or not word.isalpha():
        return [word]
    lw, n = word.lower(), len(word)
    parts, last, i = [], 0, 0
    while i < n and lw[i] not in _VOWELS:
        i += 1
    while i < n:
        while i < n and lw[i] in _VOWELS:
            i += 1
        cs = i
        while i < n and lw[i] not in _VOWELS:
            i += 1
        if i >= n:
            break
        cut = i - 1
        for cl in _CLUSTERS:
            if i - cs >= len(cl) and lw[i - len(cl):i] == cl:
                cut = i - len(cl)
                break
        if i - cs >= 3:
            for on in _ONSETS:
                if i - cs > len(on) and lw[i - len(on):i] == on:
                    cut = min(cut, i - len(on))
                    break
        if cut - last >= 2 and n - cut >= 2:
            parts.append(word[last:cut])
            last = cut
    parts.append(word[last:])
    return parts


def _tokens(text: str) -> list[list[tuple[str, bool]]]:
    """Text -> Wörter -> Teilstücke (Text, braucht Trennstrich beim Umbruch).
    Eigene Trennstellen: 'Blech|blas|instrument'."""
    out = []
    for word in text.split():
        if "|" in word:
            ps = [p for p in word.split("|") if p]
            out.append([(p, True) for p in ps])
            continue
        pieces = []
        segs = word.split("-")
        for k, seg in enumerate(segs):
            last = k == len(segs) - 1
            core = seg.strip(".,:;!?()\"'„“")       # Satzzeichen nicht mittrennen
            if core and core != seg and core.isalpha():
                a = seg.index(core)
                syl = hyphenate_word(core)
                syl[0] = seg[:a] + syl[0]
                syl[-1] = syl[-1] + seg[a + len(core):]
            else:
                syl = hyphenate_word(seg) if seg else [""]
            for j, s in enumerate(syl):
                end_of_seg = j == len(syl) - 1
                if end_of_seg and not last:
                    pieces.append((s + "-", False))      # vorhandener Bindestrich
                else:
                    pieces.append((s, True))
        out.append([p for p in pieces if p[0]])
    return out


def _wrap(words, width, size, font=FONT):
    lines, cur = [], ""
    for parts in words:
        remaining = parts
        while remaining:
            prefix = cur + " " if cur else ""
            whole = "".join(p for p, _ in remaining)
            if measure(prefix + whole, size, font) <= width:
                cur = prefix + whole
                break
            fit = 0
            for j in range(1, len(remaining)):
                t = "".join(p for p, _ in remaining[:j]) + ("-" if remaining[j - 1][1] else "")
                if measure(prefix + t, size, font) <= width:
                    fit = j
                else:
                    break
            if fit:
                lines.append(prefix + "".join(p for p, _ in remaining[:fit]) + ("-" if remaining[fit - 1][1] else ""))
                remaining, cur = remaining[fit:], ""
            elif cur:
                lines.append(cur)
                cur = ""
            else:
                return None                               # ein Teilstück passt nicht in die Breite
    if cur:
        lines.append(cur)
    return lines


def _wrap_hard(text, width, size, font=FONT):
    """Notlösung: an beliebiger Stelle umbrechen."""
    lines, cur = [], ""
    for ch in text:
        if measure(cur + ch, size, font) > width and cur:
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def fit_text(text: str, width: float, height: float, base: float, minimum: float = 3.8):
    """Größte Schrift (in pt), bei der der Text umbrochen in das Feld passt -> (größe, zeilen).
    Erst wenn es auch in der Mindestgröße nicht passt, wird mitten im Wort umbrochen."""
    words = _tokens(text)
    size = base
    while size >= minimum - 1e-9:
        lines = _wrap(words, width, size)
        if lines is not None and len(lines) * size * LEADING <= height:
            return size, lines
        size -= 0.25
    return minimum, _wrap_hard(text, width, minimum)


# ---------------------------------------------------------------- Pfeile (Einheitszelle, y nach unten)

ARROWS = {
    "R":  [("poly", [(0, .39), (.14, .5), (0, .61)])],
    "D":  [("poly", [(.39, 0), (.5, .14), (.61, 0)])],
    "DR": [("line", [(.5, 0), (.5, .2), (.62, .2)]), ("poly", [(.6, .11), (.74, .2), (.6, .29)])],
    "UR": [("line", [(.5, 1), (.5, .8), (.62, .8)]), ("poly", [(.6, .71), (.74, .8), (.6, .89)])],
    "RD": [("line", [(0, .5), (.2, .5), (.2, .62)]), ("poly", [(.11, .6), (.2, .74), (.29, .6)])],
    "LD": [("line", [(1, .5), (.8, .5), (.8, .62)]), ("poly", [(.71, .6), (.8, .74), (.89, .6)])],
}


# ---------------------------------------------------------------- Blatt

CLUE_GRAY = 0.88          # Fragefeld
HIGHLIGHT_GRAY = 0.72     # Fragefeld eines hervorgehobenen Themas


def is_highlighted(wd, highlight) -> bool:
    return bool(highlight and wd.entry and any(t in highlight for t in wd.entry.tags))


def _metrics(compact: bool):
    """(Titelgröße, Titelmitte, Rasteranfang unter dem Titel, Abstand zum Lösungswort, Schrift Lösungswort)."""
    if compact:
        return 12, 5.5, 14, 3 * MM, 9
    return 15, 7, 18, 7 * MM, 10


def sheet_size(p, title: str, cell_mm: float, compact: bool = False):
    """Breite und Höhe (pt) des Blatts, ohne es zu zeichnen."""
    t_size, _, t_h, gap, l_size = _metrics(compact)
    s = cell_mm * MM
    w, h = p.w * s, (t_h if title else 0) + p.h * s
    if title:
        w = max(w, measure(title, t_size, FONT_BOLD))
    if p.solution:
        bs = min(s, 10 * MM)
        h += gap + bs
        w = max(w, measure("Lösungswort:", l_size, FONT_BOLD) + 4 * MM + len(p.solution[0]) * bs)
    return w, h


def fit_cell(p, title: str, box_w: float, box_h: float, compact: bool = True, top: float = 14.0) -> float:
    """Größte Kästchengröße (mm, in 0,05-mm-Schritten), bei der das Blatt in das Feld passt."""
    c = top
    while c > 3:
        w, h = sheet_size(p, title, c, compact)
        if w <= box_w + 1e-6 and h <= box_h + 1e-6:
            return c
        c = round(c - 0.05, 2)
    return 3.0


# Ausgabeformate: A4 = Blatt in eingestellter Kästchengröße (bei Bedarf verkleinert);
# a5 = Rätsel hochkant auf der rechten Hälfte eines A4-Blatts im Querformat (148,5 × 210 mm),
#      zum Drucken, Falten und Binden. Innen (an der Falz) bleibt etwas mehr Rand.
FORMATS = {"a4": "A4", "a5": "A5 hochkant (rechte Hälfte von A4 quer)"}
A5_HALF = (148.5 * MM, 210 * MM)
A5_MARGIN = 6 * MM          # außen, oben, unten
A5_INNER = 10 * MM          # links, an der Falz
A5_AVAIL = (A5_HALF[0] - A5_MARGIN - A5_INNER, A5_HALF[1] - 2 * A5_MARGIN)


def _shift(ops, dx, dy):
    out = []
    for op in ops:
        k = op[0]
        if k in ("rect", "text", "textl", "circle"):
            out.append((k, op[1] + dx, op[2] + dy) + tuple(op[3:]))
        elif k == "poly":
            out.append((k, [(x + dx, y + dy) for x, y in op[1]]))
        else:
            out.append((k, [(x + dx, y + dy) for x, y in op[1]], op[2]))
    return out


def page_ops(p, title: str, cell_mm: float, answers: bool = False, highlight=None, fmt: str = "a4"):
    """Wie sheet_ops, aber für ein Ausgabeformat. Liefert (ops, breite_pt, höhe_pt, kästchen_mm).

    Bei "a5" ist das Ergebnis die ganze rechte A4-Hälfte (148,5 × 210 mm) mit dem Rätsel darin;
    die Kästchengröße wird so gewählt, dass Überschrift, Raster und Lösungswort sie ausfüllen."""
    if fmt != "a5":
        ops, w, h = sheet_ops(p, title, cell_mm, answers, highlight)
        return ops, w, h, cell_mm
    bw, bh = A5_HALF
    aw, ah = A5_AVAIL
    cell = fit_cell(p, title, aw, ah, top=16.0)
    ops, w, h = sheet_ops(p, title, cell, answers, highlight, compact=True)
    return _shift(ops, A5_INNER + (aw - w) / 2, A5_MARGIN + (ah - h) / 2), bw, bh, cell


def sheet_ops(p, title: str, cell_mm: float, answers: bool = False, highlight=None, compact: bool = False):
    """Zeichenbefehle für ein Rätselblatt. Liefert (ops, breite_pt, höhe_pt).

    highlight – Menge von Tags; Fragefelder von Begriffen mit einem dieser Tags werden dunkler.
    compact – kleinere Überschrift und engere Abstände (für das A5-Format)."""
    t_size, t_y, t_h, gap, l_size = _metrics(compact)
    s = cell_mm * MM
    line = 0.35 * MM
    ops = []
    y0 = 0.0
    if title:
        ops.append(("textl", 0, t_y, title, FONT_BOLD, t_size))
        y0 = t_h
    gw, gh = p.w * s, p.h * s
    sol_num = {}
    if p.solution:
        for k, i in enumerate(p.solution[1]):
            sol_num[i] = k + 1
    base = min(8.0, cell_mm * 0.62)
    pad = 0.35 * MM
    for i in range(p.w * p.h):
        r, c = divmod(i, p.w)
        x, y = c * s, y0 + r * s
        if p.letters[i] is None and p.clue_of[i] is None:
            ops.append(("rect", x, y, s, s, 0.62, line))
        elif p.letters[i] is None:
            wd = p.words[p.clue_of[i]]
            ops.append(("rect", x, y, s, s, HIGHLIGHT_GRAY if is_highlighted(wd, highlight) else CLUE_GRAY, line))
            text = wd.entry.frage if wd.entry else wd.wort
            size, lines = fit_text(text, s - 2 * pad, s - 2 * pad, base)
            total = len(lines) * size * LEADING
            top = y + (s - total) / 2
            for k, ln in enumerate(lines):
                ops.append(("text", x + s / 2, top + (k + 0.5) * size * LEADING, ln, FONT, size))
        else:
            ops.append(("rect", x, y, s, s, None, line))
            if i in sol_num:
                ops.append(("circle", x + s / 2, y + s / 2, s * 0.41, 0.3 * MM))
                ops.append(("text", x + s - 2.2, y + s - 3.0, str(sol_num[i]), FONT_BOLD, 5.5))
            if answers:
                ops.append(("text", x + s / 2, y + s / 2, p.letters[i], FONT_BOLD, s * 0.5))
    lw = max(0.6, s * 0.035)
    for wd in p.words:
        x, y = wd.c * s, y0 + wd.r * s
        for kind, pts in ARROWS[wd.kind]:
            pts = [(x + u * s, y + v * s) for u, v in pts]
            ops.append(("poly", pts) if kind == "poly" else ("line", pts, lw))
    ops.append(("rect", 0, y0, gw, gh, None, 0.6 * MM))      # Außenrahmen
    height = y0 + gh
    if p.solution:
        word = p.solution[0]
        bs = min(s, 10 * MM)
        ty = height + gap
        ops.append(("textl", 0, ty + bs / 2, "Lösungswort:", FONT_BOLD, l_size))
        bx = measure("Lösungswort:", l_size, FONT_BOLD) + 4 * MM
        for k, ch in enumerate(word):
            x = bx + k * bs
            ops.append(("rect", x, ty, bs, bs, None, line))
            ops.append(("text", x + bs - 2.2, ty + bs - 3.0, str(k + 1), FONT_BOLD, 5.5))
            if answers:
                ops.append(("text", x + bs / 2, ty + bs / 2, ch, FONT_BOLD, bs * 0.5))
    w, h = sheet_size(p, title, cell_mm, compact)
    return ops, w, h
