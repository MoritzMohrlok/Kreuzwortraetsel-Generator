"""Erzeugt Beispielbilder für die README (docs/beispiel_raetsel.png und docs/beispiel_loesung.png).

Nimmt nur die öffentliche Beispiel-Datenbank. Aufruf aus dem Projektordner:
    .venv\\Scripts\\python tools\\beispielbild.py [thema] [breite] [hoehe] [sekunden]
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from sr import generator  # noqa: E402
from sr.layout import page_ops  # noqa: E402
from sr.service import pick_solution, preferred_words, theme_pool, word_pool  # noqa: E402
from sr.words import EXAMPLE_CSV, load_csv  # noqa: E402

SCALE = 3.0           # Pixel je Punkt
PAD = 24


def font(name: str, px: int):
    path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf" if "Bold" in name else "arial.ttf")
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        return ImageFont.load_default()


def render(ops, w, h, path):
    img = Image.new("RGB", (int(w * SCALE) + 2 * PAD, int(h * SCALE) + 2 * PAD), "white")
    d = ImageDraw.Draw(img)

    def P(x, y):
        return PAD + x * SCALE, PAD + y * SCALE

    for op in ops:
        kind = op[0]
        if kind == "rect":
            _, x, y, ww, hh, fill, lw = op
            col = None if fill is None else (int(fill * 255),) * 3
            d.rectangle([P(x, y), P(x + ww, y + hh)], fill=col, outline="black", width=max(1, round(lw * SCALE)))
        elif kind in ("text", "textl"):
            _, x, y, text, fn, size = op
            d.text(P(x, y), text, fill="black", font=font(fn, max(6, round(size * SCALE))),
                   anchor="mm" if kind == "text" else "lm")
        elif kind == "poly":
            d.polygon([P(*pt) for pt in op[1]], fill="black")
        elif kind == "line":
            d.line([P(*pt) for pt in op[1]], fill="black", width=max(1, round(op[2] * SCALE)))
        elif kind == "circle":
            _, x, y, r, lw = op
            d.ellipse([P(x - r, y - r), P(x + r, y + r)], outline="black", width=max(1, round(lw * SCALE)))
    img.save(path, optimize=True)


def main():
    topic = sys.argv[1] if len(sys.argv) > 1 else "tiere"
    W, H, secs = (int(sys.argv[k]) if len(sys.argv) > k else d for k, d in ((2, 11), (3, 11), (4, 15)))
    entries = load_csv(os.path.join(HERE, EXAMPLE_CSV))
    topics = {topic}
    pool = word_pool(entries, topics)
    p, info = generator.generate(pool, W, H, secs, theme_words=preferred_words(entries, topics),
                                 prefer=lambda e: topic in e.tags)
    if p is None:
        sys.exit("Kein Rätsel gefunden – mehr Zeit oder kleineres Raster.")
    pick_solution(p, "auto", "", 6, 9, theme_pool(entries, topics), rng=random.Random(1))
    out = os.path.join(HERE, "docs")
    os.makedirs(out, exist_ok=True)
    title = f"Schwedenrätsel: {topic.capitalize()}"
    for answers, name in ((False, "beispiel_raetsel.png"), (True, "beispiel_loesung.png")):
        ops, w, h, _ = page_ops(p, title, 12.0, answers=answers)
        render(ops, w, h, os.path.join(out, name))
    n_th = sum(1 for wd in p.words if wd.entry and topic in wd.entry.tags)
    print(f"{len(p.words)} Wörter, davon {n_th} zum Thema, Lösungswort {p.solution[0] if p.solution else '-'}")


if __name__ == "__main__":
    main()
