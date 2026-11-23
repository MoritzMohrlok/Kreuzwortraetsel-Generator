"""Schwedenrätsel-Generator – Startdatei.

Start:  Doppelklick auf start.bat   oder   .venv\\Scripts\\python schwedenraetsel.py
"""
import multiprocessing
import os


def main():
    multiprocessing.freeze_support()
    from sr.gui import run
    from sr.words import ensure_db
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "woerter.csv")
    ensure_db(path)
    run(path)


if __name__ == "__main__":     # wichtig: die Rechenprozesse importieren diese Datei erneut
    main()
