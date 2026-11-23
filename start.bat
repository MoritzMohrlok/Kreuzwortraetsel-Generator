@echo off
rem Startet den Schwedenraetsel-Generator. Beim ersten Start wird die Python-Umgebung eingerichtet.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Richte Python-Umgebung ein ^(einmalig^) ...
  python -m venv .venv || goto :fehler
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fehler
)
rem Neue Pakete (z. B. anthropic fuer die Claude-Pruefung) bei bestehender Umgebung nachinstallieren
".venv\Scripts\python.exe" -c "import reportlab, anthropic" 2>nul || (
  echo Installiere fehlende Pakete ...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fehler
)
start "" ".venv\Scripts\pythonw.exe" schwedenraetsel.py
exit /b 0

:fehler
echo.
echo Einrichtung fehlgeschlagen. Ist Python installiert und im PATH?
pause
exit /b 1
