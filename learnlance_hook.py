#!/usr/bin/env python3
"""Standalone launcher so Claude Code can run the hook without pip-installing.

The `install` command writes an absolute path to this file into settings.json.
It simply puts the repo on sys.path and hands stdin to the hook / worker.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learnlance.hook import run_hook, run_worker  # noqa: E402

def _arg(name: str) -> str | None:
    """Read `--name value` or `--name=value` out of argv."""
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "_worker":
        run_worker(sys.argv[2])
    else:
        # --source pins the adapter (e.g. Kiro). Must be forwarded, or the
        # payload can't be routed and the hook silently does nothing.
        # --end marks the end-of-turn hook; without it this launcher's hooks
        # would have to infer the phase from the payload.
        run_hook(_arg("--source"), "--in-chat" in sys.argv, "--end" in sys.argv)
