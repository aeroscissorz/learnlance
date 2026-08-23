"""Install / uninstall learnlance hooks.

Two mechanisms:
  * Claude Code Stop hook in ~/.claude/settings.json (native, per-turn).
  * git post-commit hook in a repo (universal — works with ANY editor/agent
    since it triggers on commit, not on the AI tool).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

MARK = "learnlance"  # substring used to recognize our own hook entry
GIT_MARK = "learnlance-post-commit"  # marker line inside the git hook script


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


# --------------------------------------------------------------------------- #
# git post-commit hook (universal, editor/agent-agnostic)
# --------------------------------------------------------------------------- #
def _hooks_dir(repo: str) -> Path | None:
    """Resolve the git hooks directory for `repo`, honoring core.hooksPath."""
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--git-path", "hooks"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return None
        hp = Path(out.stdout.strip())
        if not hp.is_absolute():
            hp = Path(repo) / hp
        return hp
    except Exception:
        return None


def install_git_hook(repo: str) -> str:
    hooks = _hooks_dir(repo)
    if hooks is None:
        return f"Not a git repository (or git unavailable): {repo}"
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "post-commit"

    if target.exists() and GIT_MARK not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"A post-commit hook already exists at {target} and isn't ours.\n"
                f"Refusing to overwrite it. Add this line to it manually:\n"
                f"  {_git_hook_body().splitlines()[-1]}")

    target.write_text(_git_hook_body(), encoding="utf-8")
    try:
        target.chmod(0o755)  # no-op semantics on Windows; git still runs it
    except Exception:
        pass
    return f"Installed git post-commit hook at {target}\n  (learns from every commit, any editor)"


def uninstall_git_hook(repo: str) -> str:
    hooks = _hooks_dir(repo)
    if hooks is None:
        return f"Not a git repository: {repo}"
    target = hooks / "post-commit"
    if target.exists() and GIT_MARK in target.read_text(encoding="utf-8", errors="replace"):
        target.unlink()
        return f"Removed learnlance git hook at {target}"
    return "No learnlance git hook found here; nothing to remove."


def _git_hook_body() -> str:
    # POSIX sh — git runs hooks through its bundled shell, even on Windows.
    # We pipe a small JSON payload to the same entrypoint Claude Code uses.
    return (
        "#!/bin/sh\n"
        f"# {GIT_MARK}\n"
        'CWD="$(git rev-parse --show-toplevel)"\n'
        'COMMIT="$(git rev-parse HEAD)"\n'
        f"printf '{{\"source\":\"git\",\"cwd\":\"%s\",\"commit\":\"%s\"}}' \"$CWD\" \"$COMMIT\" | {hook_command()}\n"
    )
