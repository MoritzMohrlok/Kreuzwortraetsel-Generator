"""Kommandozeile: Rätsel ohne Oberfläche erzeugen.

Beispiel:
    python -m sr --breite 13 --hoehe 15 --zeit 15 --themen tiere,natur --pdf raetsel.pdf
"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(prog="python -m sr", description="Schwedenrätsel als PDF erzeugen")
    ap.add_argument("--csv", default=os.path.join(here, "woerter.csv"), help="Wortdatenbank (CSV)")
    ap.add_argument("--breite", type=int, default=13)
    ap.add_argument("--hoehe", type=int, default=15)
    ap.add_argument("--zeit", type=float, default=15, help="Rechenzeit in Sekunden")
    ap.add_argument("--themen", default="", help="Tags, durch Komma getrennt")
    ap.add_argument("--keine-fuellwoerter", action="store_true")
    ap.add_argument("--nur-eigene", action="store_true", help="keine automatisch erzeugten Begriffe verwenden")
    ap.add_argument("--loesungswort", default="", help="eigenes Lösungswort; 'auto' oder leer = automatisch, 'ohne'")
    ap.add_argument("--titel", default="Schwedenrätsel")
    ap.add_argument("--kaestchen", type=float, default=12, help="Kästchengröße in mm")
    ap.add_argument("--format", choices=("a4", "a5"), default="a4",
                    help="a4 = Kästchengröße wie angegeben; a5 = Rätsel hochkant auf der rechten Hälfte von A4 quer")
    ap.add_argument("--ohne-loesungsblatt", action="store_true")
    ap.add_argument("--pdf", default="raetsel.pdf")
    ap.add_argument("--durchlaeufe", type=int, default=0,
                    help="Themen-Modus: so viele Rätsel erzeugen und das mit den meisten Themenwörtern nehmen "
                         "(--zeit ist dann die Obergrenze)")
    a = ap.parse_args()
    if a.durchlaeufe and not a.themen:
        ap.error("--durchlaeufe braucht --themen")

    from .generator import generate
    from .pdf import export_pdf
    from .puzzle import validate
    from .service import pick_solution, preferred_words, theme_pool, word_pool
    from .words import ensure_db, load_csv, split_tags

    ensure_db(a.csv)
    entries = load_csv(a.csv)
    topics = set(split_tags(a.themen))
    use_auto = not a.nur_eigene
    pool = word_pool(entries, topics, not a.keine_fuellwoerter, use_auto)
    theme = preferred_words(entries, topics, use_auto)
    prefer = (lambda e: any(t in topics for t in e.tags)) if topics else None
    runs = a.durchlaeufe or None
    print(f"{len(pool)} Begriffe, Raster {a.breite}×{a.hoehe}, "
          + (f"{runs} Rätsel (höchstens {a.zeit:g} s) …" if runs else f"{a.zeit:g} s …"))
    p, info = generate(pool, a.breite, a.hoehe, a.zeit, theme_words=theme, prefer=prefer, runs=runs)
    if p is None:
        print("Kein Rätsel gefunden – mehr Zeit, kleineres Raster oder mehr Begriffe.")
        return 1
    mode = "none" if a.loesungswort == "ohne" else ("auto" if a.loesungswort in ("", "auto") else "manual")
    msg = pick_solution(p, mode, a.loesungswort, 6, 10, theme_pool(entries, topics, use_auto))
    if msg:
        print("Hinweis:", msg)
    errs = validate(p)
    if errs:
        print("Regelfehler:", errs[:5])
        return 2
    st = p.stats()
    print(f"{st['words']} Wörter, {st['gray']} graue Felder, Ø {st['avg_len']:.1f} Buchstaben, "
          f"{st['two_letter']} Zweibuchstaben-Wörter, {st['bent']} Knickpfeile"
          + (f", Lösungswort {p.solution[0]}" if p.solution else ""))
    if topics:
        n_th = sum(1 for w in p.words if w.entry and any(t in topics for t in w.entry.tags))
        print(f"{n_th} Themenwörter (beste von {info['found']} Rätseln, {info['elapsed']:.0f} s)")
    scale, cell = export_pdf(a.pdf, p, a.titel, a.kaestchen, not a.ohne_loesungsblatt, fmt=a.format)
    note = f" (Kästchen {cell:.1f} mm)" if a.format == "a5" else (f" (auf {scale:.0%} verkleinert)" if scale < 0.999 else "")
    print(f"PDF gespeichert: {os.path.abspath(a.pdf)}" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
