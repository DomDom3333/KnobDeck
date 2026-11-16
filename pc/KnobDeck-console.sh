#!/usr/bin/env bash
# Runs KnobDeck in this terminal with detailed logging - use this when something goes wrong
cd "$(dirname "$0")"
.venv/bin/python -m knobdeck --settings -v
echo
echo "KnobDeck stopped. Copy the messages above if you need help."
