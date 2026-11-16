@echo off
rem Runs KnobDeck (with tray icon) in a console window with detailed logging - use this when something goes wrong
cd /d "%~dp0"
".venv\Scripts\python.exe" -m knobdeck --settings -v
echo.
echo KnobDeck stopped. Copy the messages above if you need help.
pause
