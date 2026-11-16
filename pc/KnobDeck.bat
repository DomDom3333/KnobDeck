@echo off
rem Starts KnobDeck in the background (tray icon) and opens the settings window
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m knobdeck --settings
