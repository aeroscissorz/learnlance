"""Install / uninstall the learnlance Stop hook into Claude Code settings.json."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import config

MARK = "learnlance"  # substring used to recognize our own hook entry


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hook_command() -> str:
    """Build the command Claude Code runs on Stop, correct for how learnlance
    was installed. All variants contain 'learnlance' so uninstall stays idempotent.

    Priority:
      1. Installed as a package (pip/pipx) -> use the `learnlance` console script
         by absolute path (survives PATH differences in the hook's environment).
      2. Running from a git clone -> the repo's launcher script (no install needed).
      3. Fallback -> `python -m learnlance hook` with the current interpreter.
    """
    exe = shutil.which("learnlance")
    if exe:
        return f'"{exe}" hook'
    launcher = Path(__file__).resolve().parent.parent / "learnlance_hook.py"
    if launcher.exists():
        return f'"{sys.executable}" "{launcher}"'
    return f'"{sys.executable}" -m learnlance hook'


def _load_settings(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def install_hook() -> str:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(p)
    hooks = settings.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])

    cmd = hook_command()
    # Remove any prior learnlance entries, then add fresh (idempotent).
    for group in stop:
        group.get("hooks", [])  # touch
    stop[:] = [
        g for g in stop
        if not any(MARK in (h.get("command", "")) for h in g.get("hooks", []))
    ]
    stop.append({"hooks": [{"type": "command", "command": cmd}]})

    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return f"Installed Stop hook in {p}\n  command: {cmd}"


def uninstall_hook() -> str:
    p = settings_path()
    if not p.exists():
        return "No settings.json found; nothing to remove."
    settings = _load_settings(p)
    stop = settings.get("hooks", {}).get("Stop", [])
    before = len(stop)
    stop[:] = [
        g for g in stop
        if not any(MARK in (h.get("command", "")) for h in g.get("hooks", []))
    ]
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return f"Removed {before - len(stop)} learnlance hook entr(y/ies) from {p}"
