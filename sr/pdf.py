"""PDF-Export: Seite 1 Rätsel, Seite 2 optional Lösung.

Formate: "a4" (Blatt in eingestellter Kästchengröße, zu groß -> verkleinert) und
"a5" (A4 quer, Rätsel hochkant auf der rechten Hälfte = 148,5 × 210 mm)."""
from __future__ import annotations

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from .layout import MM, page_ops

MARGIN = 12 * MM


def _draw(c: canvas.Canvas, ops, width, height, fmt="a4"):
    pw, ph = A4 if fmt != "a5" else landscape(A4)
    if fmt == "a5":
        scale, ox, oy = 1.0, pw - width, ph      # rechte Hälfte des Querblatts
    else:
        scale = min(1.0, (pw - 2 * MARGIN) / width, (ph - 2 * MARGIN) / height)   # zu groß -> verkleinern
        ox, oy = (pw - width * scale) / 2, ph - MARGIN

    def P(x, y):
        return ox + x * scale, oy - y * scale

    for op in ops:
        kind = op[0]
        if kind == "rect":
            _, x, y, w, h, fill, lw = op
            X, Y = P(x, y + h)
            c.setLineWidth(lw * scale)
            if fill is not None:
                c.setFillGray(fill)
            c.rect(X, Y, w * scale, h * scale, stroke=1, fill=1 if fill is not None else 0)
        elif kind in ("text", "textl"):
            _, x, y, text, font, size = op
            c.setFillGray(0)
            c.setFont(font, size * scale)
            X, Y = P(x, y + size * 0.35)            # y ist die Mitte -> Grundlinie
            if kind == "text":
                c.drawCentredString(X, Y, text)
            else:
                c.drawString(X, Y, text)
        elif kind == "poly":
            path = c.beginPath()
            pts = [P(x, y) for x, y in op[1]]
            path.moveTo(*pts[0])
            for pt in pts[1:]:
                path.lineTo(*pt)
            path.close()
            c.setFillGray(0)
            c.drawPath(path, stroke=0, fill=1)
        elif kind == "line":
            pts = [P(x, y) for x, y in op[1]]
            c.setLineWidth(op[2] * scale)
            c.setStrokeGray(0)
            path = c.beginPath()
            path.moveTo(*pts[0])
            for pt in pts[1:]:
                path.lineTo(*pt)
            c.drawPath(path, stroke=1, fill=0)
        elif kind == "circle":
            _, x, y, r, lw = op
            c.setLineWidth(lw * scale)
            X, Y = P(x, y)
            c.circle(X, Y, r * scale, stroke=1, fill=0)
    return scale


def export_pdf(path: str, puzzle, title: str, cell_mm: float, answer_page: bool = True,
               highlight=None, fmt: str = "a4") -> tuple[float, float]:
    """Schreibt das PDF. Liefert (Verkleinerungsfaktor, Kästchengröße in mm).
    Bei A4 ist der Faktor 1.0 = Originalgröße; bei A5 wird die Kästchengröße passend gewählt."""
    c = canvas.Canvas(path, pagesize=landscape(A4) if fmt == "a5" else A4)
    c.setTitle(title or "Schwedenrätsel")
    c.setLineJoin(1)
    ops, w, h, cell = page_ops(puzzle, title, cell_mm, answers=False, highlight=highlight, fmt=fmt)
    scale = _draw(c, ops, w, h, fmt)
    c.showPage()
    if answer_page:
        ops, w, h, _ = page_ops(puzzle, f"Lösung: {title}" if title else "Lösung", cell_mm, answers=True,
                                highlight=highlight, fmt=fmt)
        _draw(c, ops, w, h, fmt)
        c.showPage()
    c.save()
    return scale, cell * scale
