#!/usr/bin/env python3
"""Standalone launcher so Claude Code can run the hook without pip-installing.

The `install` command writes an absolute path to this file into settings.json.
It simply puts the repo on sys.path and hands stdin to the hook / worker.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learnlance.hook import run_hook, run_worker  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "_worker":
        run_worker(sys.argv[2])
    else:
        run_hook()
