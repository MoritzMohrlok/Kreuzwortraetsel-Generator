# Anleitung: Schwedenrätsel-Generator

Überblick über das Projekt: siehe [README](README.md).

Erstellt druckfertige Schwedenrätsel als PDF aus einer Wortdatenbank. **Jede Frage steht in einem eigenen
Kästchen.** Der Pfeil zeigt gerade oder abgeknickt auf den ersten Buchstaben des Worts.

## Starten

**`start.bat` doppelklicken.** Beim ersten Start richtet das Skript einmalig eine eigene Python-Umgebung
(`.venv`) ein und installiert `reportlab` für den PDF-Export und `anthropic` für die Prüfung durch Claude.
Danach öffnet sich das Programmfenster. Voraussetzung ist Python 3.10 oder neuer unter Windows.

Beim allerersten Start legt das Programm die eigene Datenbank `woerter.csv` als Kopie von
`woerter_beispiel.csv` an (rund 1.150 allgemeine Begriffe). Ab dann arbeitest du nur noch in `woerter.csv`.

Ohne Oberfläche, z. B. für viele Rätsel hintereinander:

```
.venv\Scripts\python -m sr --breite 13 --hoehe 15 --zeit 20 --themen tiere,natur --pdf tiere.pdf
```

Themen-Modus: `--themen tiere --durchlaeufe 100 --zeit 600` (die Zeit ist dann die Obergrenze).

(`python -m sr --help` zeigt alle Optionen.)

## Reiter „Datenbank“

- Die Datenbank ist `woerter.csv`. Sie öffnet sich beim Start automatisch und lässt sich auch in Excel bearbeiten.
  Format: `id;frage;antwort;laenge;tags;quelle;erstellt`, die Tags sind durch Komma getrennt.
- **Quelle:** `auto` = automatisch erzeugte Startbegriffe (in der Tabelle grau), `manuell` = selbst eingegeben,
  `einsendung` = über das Google-Formular eingereicht (siehe unten). Neue Einträge bekommen automatisch
  `manuell` und das heutige Datum. Wer einen automatischen Begriff inhaltlich ändert, macht ihn zu `manuell`.
  Mit dem Filter „Quelle“ zeigst du nur eine Sorte an.
- **Eingabemaske links:** Frage, Antwort und Tags eintragen, dann Enter oder „Hinzufügen“. Die Maske zeigt, wie
  die Antwort im Rätsel geschrieben wird (Ä → AE, ß → SS), und warnt vor doppelten Antworten. Ein Doppelklick
  auf einen vorhandenen Tag fügt ihn hinzu. Die Tags bleiben für die nächste Eingabe stehen.
- **Tabelle rechts:** Suche, Tag-Filter, Sortieren per Klick auf die Spaltenüberschrift. Doppelklick bearbeitet
  einen Eintrag, die Entf-Taste löscht ihn.
- **Speichern mit Strg+S.** Beim Schließen fragt das Programm nach, wenn noch Änderungen offen sind.
- Eigene Trennstellen für lange Fragen setzt du mit `|`, z. B. `Blech|blas|instrument`.
- **Persönliche Rätsel** (Geburtstag, Hochzeit, Weihnachten …): Begriffe mit einem eigenen Tag anlegen, z. B.
  `familie`, und dieses Thema beim Erzeugen auswählen. Passende allgemeine Füllbegriffe kannst du mit einem
  zweiten Tag wie `familie-fill` markieren und zusammen mit `familie` auswählen, dann zählen sie als Themenwörter.
  Was ein eigener Tag bedeutet, kannst du Claude in `tag_hinweise.txt` erklären (siehe „Prüfung durch Claude“).
- Antworten mit bis zu 3 Buchstaben bekommen automatisch den Tag `kurzwort`. Das gilt für neue Einträge
  und beim Laden auch für vorhandene. Gespeichert wird es mit dem nächsten Speichern.

## Prüfung durch Claude

Claude (KI von Anthropic) kann die Datenbank und fertige Rätsel begutachten. Das Gitter baut weiterhin der
Generator. Claude prüft nur die Sprache: Fragen, Bekanntheit der Begriffe, Tags und Themenbezug.

**Einmal einrichten:** In der Anthropic Console (platform.claude.com > API Keys) einen API-Schlüssel anlegen und
Guthaben aufladen. Im Reiter „Datenbank“ auf **„Claude-Zugang…“** klicken und den Schlüssel einfügen. Er wird
in `%APPDATA%\Schwedenraetsel\anthropic_key.txt` gespeichert, nicht im Projektordner. Alternativ geht die
Umgebungsvariable `ANTHROPIC_API_KEY`. Das Paket `anthropic` installiert `start.bat` beim nächsten Start
automatisch nach.

**Datenbank prüfen** („Mit Claude prüfen…“ im Reiter „Datenbank“):

- Claude bewertet jeden Begriff mit 1 bis 5 Sternen, meldet Probleme (Frage falsch, zu ungenau, zu lang, verrät
  die Antwort, Begriff kaum bekannt, Tags fehlen …) und schlägt bei Bedarf eine bessere Frage oder Tags vor.
- Vor dem Start zeigt das Programm, wie viele Begriffe geprüft werden, und schätzt die Kosten grob (alle rund
  1.150 Begriffe: wenige Dollar). Die tatsächlichen Kosten stehen danach im Dialog.
- Die Ergebnisse stehen in `pruefung.json`. **Erneut prüfen** prüft nur neue und geänderte Begriffe. Ein
  Begriff gilt als geändert, sobald sich Frage, Antwort oder Tags ändern. „Alle Begriffe neu prüfen“ prüft alles.
- Die Liste unten zeigt die offenen Hinweise. **„Vorschlag übernehmen“** setzt Claudes Frage und Tags ein, die
  Quelle des Begriffs bleibt gleich. **„Abhaken“** blendet den Hinweis aus. **„Begriff meiden“** gibt ihm den
  Tag `meiden`. **„Bearbeiten“** öffnet ihn in der Eingabemaske. Danach mit Strg+S speichern.
- In der Tabelle zeigt die Spalte „Claude-Prüfung“ Sterne und Probleme. Der Filter „Prüfung“ zeigt nur Begriffe
  mit Hinweisen, ungeprüfte oder schwache (★1–2).
- Begriffe mit ★1 lässt der Generator weg, solange im Reiter „Rätsel erstellen“ das Häkchen **„Von Claude mit
  ★1 bewertete Begriffe meiden“** gesetzt ist. Begriffe mit dem Tag `meiden` kommen nie ins Rätsel (auch nicht
  über die Kommandozeile), außer als feste Begriffe.

**Eigene Tags erklären:** Legst du neben `woerter.csv` eine Datei `tag_hinweise.txt` an, gibt das Programm
sie bei jeder Prüfung an Claude weiter. Eine Erklärung pro Zeile, Zeilen mit `#` werden übersprungen, z. B.
`familie = Insider aus unserer Familie, Namen bitte nicht ändern`.

**Rätsel prüfen** („Rätsel prüfen (Claude)…“ unter „Rätsel erzeugen“):

- Oben stehen Zahlen, die ohne Claude feststehen: Anteil der Themenwörter, aus welchen Tags die Füllwörter kommen,
  Quellen und die Sterne aus der Datenbank-Prüfung.
- Darunter Claudes Urteil (Note 1–10, Fazit, Themenbezug, Stärken, Verbesserungen) und die Liste der Wörter mit
  Problemen. **„Frage übernehmen“** ändert die Frage sofort im Rätsel und in der Datenbank. Ein Wort selbst lässt
  sich nicht austauschen, ohne das Gitter neu zu bauen: „Begriff meiden“ und dann neu erzeugen.
- Eine Rätsel-Prüfung kostet nur wenige Cent.

## Einsendungen über ein Google-Formular

Andere (z. B. Familie und Freunde) können Begriffe über ein Google-Formular einreichen, auch am Handy. Die Antworten
landen in einer Google-Tabelle, und **„Einsendungen holen…“** (Reiter „Datenbank“, oben) übernimmt sie.

**Einmal einrichten:**

1. Auf forms.google.com ein neues Formular anlegen, mit diesen Fragen:
   - **Frage** (Kurzantwort, Pflichtfeld). Tipp: unter ⋮ > „Antwortvalidierung“ > „Länge“ > „Maximale
     Zeichenanzahl“ 35 einstellen, sonst passt die Frage nicht ins Kästchen.
   - **Antwort** (Kurzantwort, Pflichtfeld). Ein Wort oder Name, Umlaute sind erlaubt.
   - **Themen** (optional, z. B. Kontrollkästchen mit `familie`, `urlaub` …). Ohne diese Frage helfen die
     „Zusätzlichen Tags“ im Programm.
   - **Name** (optional), damit du siehst, von wem ein Begriff kommt.
   Die Spalten erkennt das Programm an diesen Wörtern in der Überschrift. „Frage (bitte kurz)“ geht also auch.
2. Im Formular unter „Antworten“ auf „Mit Google Sheets verknüpfen“ klicken. Es entsteht eine Tabelle.
3. Den Link zum Formular („Senden“ > Link) an die Mitmachenden schicken.

**Einsendungen holen**, zwei Wege:

- **Privat (Datei):** In der Tabelle „Datei > Herunterladen > Kommagetrennte Werte (.csv)“. Dann im Programm
  „Einsendungen holen…“ > „Datei…“. Die Tabelle bleibt privat.
- **Bequem (Link):** In der Tabelle „Freigeben“ > Allgemeiner Zugriff „Jeder mit dem Link“ (Betrachter) und den
  Link kopieren. Im Programm einfügen und „Laden“. Der Link wird gespeichert, beim nächsten Mal genügt ein Klick.
  **Achtung:** Wer den Link kennt, kann die Tabelle mit allen Antworten lesen. Schick ihn also nicht weiter.
  Auch „Datei > Freigeben > Im Web veröffentlichen“ als CSV funktioniert.

**Im Dialog** stehen alle Zeilen mit Status:

| Status | Bedeutung |
|---|---|
| `neu` | vorausgewählt |
| `doppelt` | gleiche Frage und Antwort gibt es schon in der Datenbank |
| `übernommen` | wurde früher schon geholt |
| `ungültig` | Frage fehlt oder Antwort zu kurz |

Hinweise warnen vor langen Fragen, sehr langen Antworten und Antworten, die es schon mit anderer Frage gibt.
Mit Strg+Klick änderst du die Auswahl, „Zusätzliche Tags für alle“ (z. B. `familie`) hängt Tags an.
„Ausgewählte übernehmen“ fügt die Begriffe mit Quelle `einsendung` ein, danach mit Strg+S speichern. Erst
nach dem Speichern merkt sich das Programm die Zeilen als übernommen. Gelöschte oder geänderte Einsendungen
kommen beim nächsten Holen also nicht wieder. Diese Liste steht in `einstellungen.json`.

## Reiter „Rätsel erstellen“

1. Größe, Kästchengröße und Rechenzeit wählen. 13×15 Kästchen à 12 mm passen gut auf A4.
2. Themen wählen (Mehrfachauswahl). Ohne Auswahl werden alle Begriffe verwendet. Themenwörter werden bevorzugt.
   Kurze Füllwörter (Tag `kurzwort` oder bis 3 Buchstaben) kommen aus der ganzen Datenbank, solange das Häkchen
   gesetzt ist. Für ein dichtes Raster sind sie nötig.
   Mit dem Häkchen **„Automatisch erzeugte Begriffe verwenden“** schaltest du die Startbegriffe ab. Dann
   werden nur eigene Begriffe verwendet, was erst ab einigen hundert eigenen Begriffen funktioniert. Ohne
   Themenauswahl werden eigene Wörter beim Füllen bevorzugt. Bei gleicher Antwort wird immer die selbst
   eingegebene Frage genommen.
   **Themen-Modus „beste von N Rätseln“:** Das Programm erzeugt N fertige Rätsel und behält das mit den
   meisten Wörtern aus den gewählten Themen (bei Gleichstand das mit mehr Themen-Buchstaben). Die Rechenzeit
   gilt dann nicht. Die Statuszeile zeigt den Fortschritt und das bisher beste Ergebnis, „Stopp“ übernimmt es.
   Bei 11×11 dauern 100 Rätsel etwa 1,5 Minuten, große Raster deutlich länger.
   **Feste Begriffe:** Thema wählen, Begriff aus der Liste wählen, „Hinzufügen“. Diese Begriffe stehen sicher
   (mit genau dieser Frage) im Rätsel, der Rest wird darum herum gebaut. Kurze Begriffe (bis 5 Buchstaben)
   sind leicht. Für lange Begriffe legt der Generator eine feste Lücke an. Ein bis zwei lange Begriffe gehen gut,
   mehr machen die Suche deutlich schwerer (dann mehr Rechenzeit oder ein kleineres Raster).
   **Fragefelder hervorheben:** Die Fragefelder aller Begriffe mit dem gewählten Tag werden dunkler grau,
   in der Vorschau und im PDF. Das lässt sich auch nach dem Erzeugen noch umstellen.
   Ist die linke Spalte zu lang für den Bildschirm, scrollst du sie mit dem Mausrad.
3. Lösungswort: automatisch aus dem Thema, ein eigenes Wort oder keins.
4. **„Rätsel erzeugen“.** Das Programm rechnet auf allen Prozessorkernen und behält die beste Variante.
   Mit „Stopp“ kannst du früher abbrechen.
5. **„PDF speichern…“:** Seite 1 enthält das Rätsel, Seite 2 (optional) das Lösungsblatt. Das PDF druckst du direkt aus.
   **Format:** „A4“ nimmt die eingestellte Kästchengröße. Bei **„A5 hochkant (rechte Hälfte von A4 quer)“**
   ist die PDF-Seite ein A4-Querblatt. Das Rätsel steht mit Überschrift und Lösungswort hochkant auf der rechten
   Hälfte (148,5 × 210 mm), zum Drucken, Falten und Binden. Der Rand ist 6 mm, an der Falz 10 mm. Die
   Kästchengröße wird automatisch berechnet und unter „Format“ angezeigt. Dafür passt ein hohes Raster,
   z. B. 12 × 16 (ca. 11 mm Kästchen). Die Vorschau zeigt dann die rechte Blatthälfte.
   Kommandozeile: `--format a5`.

## Reiter „Auswertung“

Ein Dashboard über die Datenbank. Oben wählst du, ob alle, nur eigene oder nur automatisch erzeugte Begriffe
ausgewertet werden, und legst das Ziel je Thema fest.

- **Kennzahlen:** Begriffe, davon eigene und automatische, verschiedene Antworten, Themen, Ø Antwortlänge,
  Antworten mit mehreren Fragen, Begriffe ohne Tag.
- **Wortlängen:** Anteil der Antworten je Buchstabenzahl (blaue Säulen) im Vergleich zum **Bedarf des
  Generators** (schwarze Markierung). Der Bedarf ist der Anteil der Lücken dieser Länge in seinen Mustern,
  gemessen an 47 Mustern. Liegt eine Säule unter der Markierung, fehlen Wörter dieser Länge.
- **Buchstabenverteilung:** Anteil jedes Buchstabens in allen Antworten im Vergleich zur Häufigkeit im
  deutschen Text. Wörter mit häufigen Buchstaben (E, N, R, S …) lassen sich leichter verkreuzen.
- **Begriffe je Thema:** eigene (blau) und automatisch erzeugte (grau) Begriffe je Tag, mit einer Linie für
  das Ziel je Thema.

Über einer Säule oder einem Balken zeigt ein Tooltip die genauen Werte. „Als Tabelle“ blendet alle Zahlen als
Tabelle ein. Die Auswertung aktualisiert sich bei jeder Änderung an der Datenbank.

## So arbeitet der Generator

1. **Muster:** Zuerst wird festgelegt, wo Fragefelder und Buchstaben liegen. Jede Buchstabenfolge braucht ein
   eigenes, angrenzendes Fragefeld, und jedes Fragefeld trägt genau eine Frage. Graue Leerfelder gibt es nicht.
   Ob das aufgeht, prüft eine Zuordnung (bipartites Matching).
2. **Füllen:** Dann werden die Lücken mit Wörtern belegt, immer zuerst die am stärksten eingeschränkte Lücke.
   Bevorzugt werden Wörter, die den kreuzenden Lücken viele Möglichkeiten lassen.

Für Themenwörter mit einer Länge, von der es sonst kaum Wörter gibt (z. B. lange Namen in einem
persönlichen Rätsel), legt der Generator in einem Teil der Versuche gezielt passende Lücken an. Nach dem Erzeugen
zeigt die Statuszeile, wie viele Themenwörter im Rätsel stehen. Bei kleinen Themen (unter 50 Begriffen)
bleiben die meisten Wörter Füllwörter. Ein kleineres Raster und viele kurze Themenbegriffe erhöhen den
Themenanteil.

Je größer die Datenbank ist, vor allem bei Wörtern mit 3–7 Buchstaben, desto längere Wörter und desto weniger
Füllwörter wie „Chem. Zeichen …“ kommen im Rätsel vor. Große Raster (z. B. 17×21) brauchen mehr Rechenzeit
(30 s und mehr).

## Dateien

| Datei | Inhalt |
|---|---|
| `start.bat` | Start mit automatischer Einrichtung |
| `schwedenraetsel.py` | Startdatei des Programms |
| `woerter_beispiel.csv` | allgemeine Beispiel-Datenbank, Vorlage für den ersten Start |
| `woerter.csv` | deine eigene Wortdatenbank (privat, nicht in Git) |
| `tag_hinweise.txt` | optional: deine Erklärungen zu eigenen Tags für Claude (privat, nicht in Git) |
| `sr/gui.py` | Oberfläche (tkinter) |
| `sr/generator.py` | Muster und Füllen, parallel auf mehreren Prozessen |
| `sr/puzzle.py` | Rätsel-Datenmodell, Fragezuordnung, Lösungswort, Regelprüfung |
| `sr/layout.py` | Blattlayout, Silbentrennung, Pfeile |
| `sr/pdf.py` | PDF-Export |
| `sr/words.py` | CSV lesen und schreiben |
| `sr/einsendungen.py` | Einsendungen aus Google Sheets oder CSV übernehmen |
| `sr/claude_check.py` | Prüfung von Datenbank und Rätsel durch Claude |
| `pruefung.json` | Ergebnisse der Datenbank-Prüfung (wird automatisch angelegt) |
| `sr/service.py` | Themenauswahl, Lösungswort |
| `sr/stats.py` | Kennzahlen und Vergleichsverteilungen der Auswertung |
| `sr/charts.py` | Diagramme der Auswertung (tkinter) |
| `einstellungen.json` | zuletzt benutzte Einstellungen (wird automatisch angelegt) |
