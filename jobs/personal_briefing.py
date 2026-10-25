"""Hermes native cron entry point; saves a briefing without an LLM call."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path.home() / 'hermes-control'))
from personal import main

sys.argv = ['personal.py', 'briefing']
main()
