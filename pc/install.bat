@echo off
rem KnobDeck PC app - one-time setup
cd /d "%~dp0"
set PY=
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "pass" >nul 2>nul && set PY=py -%%V
  )
)
if not defined PY (
  python -c "pass" >nul 2>nul && set PY=python
)
if not defined PY (
  echo Python was not found. Install it first, e.g. in a terminal:  py install 3.13
  pause
  exit /b 1
)
echo Using %PY%
%PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing packages...
python -m pip install -r requirements.txt || goto :fail
echo Installing the settings window (optional)...
python -m pip install -r requirements-window.txt || echo   Not available for this Python version - settings will open in your browser instead.
echo Starting KnobDeck when the deck is plugged in (change it in the settings, Device tab)...
python -c "from knobdeck import autostart; autostart.set_mode('plugin')"
echo.
echo Done. Start KnobDeck with KnobDeck.bat (it lives in the tray, next to the clock).
echo From now on it also starts by itself with Windows and shows up when you plug the deck in.
pause
exit /b 0
:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
