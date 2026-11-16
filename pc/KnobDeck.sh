#!/usr/bin/env bash
# Starts KnobDeck in the background (tray icon) and opens the settings window.
# If it's already running, this just opens the settings window.
cd "$(dirname "$0")"
nohup .venv/bin/python -m knobdeck --settings >/dev/null 2>&1 &
