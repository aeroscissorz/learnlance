"""Zero-config setup: detect which harnesses are in use and install their hooks.

Runs once after `pip install learnlance` — on the first CLI invocation or the
first time any hook fires — so the user never has to run `learnlance install`.

Detection is deliberately conservative: we look for a harness's own config
directory, which only exists once that tool has actually run. `~/.copilot/`
means Copilot CLI is installed; a bare `.github/` directory does not.

Nothing here raises. A harness we can't set up is logged and skipped.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config, inchat, install

_SETUP_FLAG = "auto_setup_done"
_OPTOUT_FLAG = "auto_setup_opted_out"  # set by `uninstall`, cleared by install/setup


# --------------------------------------------------------------------------- #
# Per-harness knowledge: how to tell it's in use, and where its hook file lives
# --------------------------------------------------------------------------- #
def _kiro_marker(cwd):
    return (Path(cwd) / ".kiro").is_dir() or (Path.home() / ".kiro").is_dir()


def _cursor_marker(cwd):
    return (Path(cwd) / ".cursor").is_dir() or (Path.home() / ".cursor").is_dir()


def _copilot_marker(cwd):
    # Only the user-level dir is evidence; every repo has a .github folder.
    return (Path.home() / ".copilot").is_dir()


def _gemini_marker(cwd):
    return (Path(cwd) / ".gemini").is_dir() or (Path.home() / ".gemini").is_dir()


def _antigravity_marker(cwd):
    # `.agents/` in the workspace is Antigravity's customization dir. It also
    # falls back to ~/.gemini/config/, being Gemini CLI's successor.
    return ((Path(cwd) / ".agents").is_dir()
            or (Path.home() / ".gemini" / "config").is_dir())


def _codex_marker(cwd):
    return ((Path(cwd) / ".codex").is_dir()
            or (Path.home() / ".codex").is_dir()
            or shutil.which("codex") is not None)


def _git_marker(cwd):
    try:
        p = subprocess.run(["git", "-C", cwd, "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=10)
        return p.returncode == 0
    except Exception:
        return False


# Every `install` here takes (cwd, in_chat) so the caller never has to know which
# harness supports which mode. Uniform signatures matter: an earlier version used
# single-argument lambdas and passed in_chat positionally, which raised TypeError
# and silently fell back to installing without in-chat mode.
def _gemini_target(cwd):
    """Project settings if the project has them, else the user-level file."""
    return cwd if (Path(cwd) / ".gemini").is_dir() else None


HARNESSES = {
    "kiro": {
        "label": "Kiro",
        "detect": _kiro_marker,
        "config": lambda cwd: install._kiro_hooks_dir(cwd) / install.KIRO_HOOK_FILENAME,
        "install": lambda cwd, in_chat=False: install.install_kiro_hook(cwd, in_chat),
    },
    "cursor": {
        "label": "Cursor",
        "detect": _cursor_marker,
        "config": lambda cwd: install.cursor_hooks_path(cwd),
        "install": lambda cwd, in_chat=False: install.install_cursor_hook(cwd, in_chat),
    },
    "copilot": {
        "label": "Copilot / VS Code",
        "detect": _copilot_marker,
        "config": lambda cwd: install.copilot_hooks_path(cwd),
        "install": lambda cwd, in_chat=False: install.install_copilot_hook(cwd, in_chat),
    },
    "gemini": {
        "label": "Gemini CLI",
        "detect": _gemini_marker,
        "config": lambda cwd: install.gemini_settings_path(_gemini_target(cwd)),
        "install": lambda cwd, in_chat=False: install.install_gemini_hook(
            _gemini_target(cwd), in_chat),
    },
    "antigravity": {
        "label": "Antigravity",
        "detect": _antigravity_marker,
        "config": lambda cwd: install.antigravity_hooks_path(cwd),
        "install": lambda cwd, in_chat=False: install.install_antigravity_hook(
            cwd, in_chat),
    },
    "codex": {
        "label": "OpenAI Codex",
        "detect": _codex_marker,
        "config": lambda cwd: install.codex_hooks_path(cwd),
        "install": lambda cwd, in_chat=False: install.install_codex_hook(cwd, in_chat),
    },
    "git": {
        "label": "git commit",
        "detect": _git_marker,
        "config": lambda cwd: (install._hooks_dir(cwd) or Path(cwd)) / "post-commit",
        # git has no in-chat variant: a commit hook has no agent to ask.
        "install": lambda cwd, in_chat=False: install.install_git_hook(cwd),
    },
}


# --------------------------------------------------------------------------- #
# Presence checks (also used by `learnlance doctor`)
# --------------------------------------------------------------------------- #
def hook_present(name: str, cwd: str) -> bool:
    """True if our hook is already installed for `name` in this project."""
    spec = HARNESSES.get(name)
    if spec is None:
        return False
    try:
        path = spec["config"](cwd)
        if not path or not Path(path).exists():
            return False
        return install.MARK in Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False


def hook_in_chat(name: str, cwd: str) -> bool:
    """True if `name`'s installed hook is configured for in-chat analysis."""
    spec = HARNESSES.get(name)
    if spec is None:
        return False
    try:
        path = spec["config"](cwd)
        if not path or not Path(path).exists():
            return False
        body = Path(path).read_text(encoding="utf-8", errors="replace")
        return install.MARK in body and "--in-chat" in body
    except Exception:
        return False


def claude_hook_present() -> bool:
    """True if learnlance is in Claude Code's user-level Stop hooks."""
    try:
        sp = install.settings_path()
        if not sp.exists():
            return False
        data = json.loads(sp.read_text(encoding="utf-8"))
        for grp in data.get("hooks", {}).get("Stop", []):
            if any(install.MARK in h.get("command", "") for h in grp.get("hooks", [])):
                return True
    except Exception:
        pass
    return False


def detect_harnesses(cwd: str) -> list[str]:
    """Names of harnesses that appear to be in use here."""
    found = []
    for name, spec in HARNESSES.items():
        try:
            if spec["detect"](cwd):
                found.append(name)
        except Exception:
            pass
    return found


# --------------------------------------------------------------------------- #
# One-time setup
# --------------------------------------------------------------------------- #
def needs_setup() -> bool:
    return not config.load_config().get(_SETUP_FLAG, False)


def mark_done() -> None:
    cfg = config.load_config()
    cfg[_SETUP_FLAG] = True
    config.save_config(cfg)


def opted_out() -> bool:
    """True once the user has explicitly uninstalled.

    Without this, the implicit auto-setup re-installs on the very next command
    and `learnlance uninstall` can never stick.
    """
    return bool(config.load_config().get(_OPTOUT_FLAG, False))


def set_opted_out(value: bool) -> None:
    cfg = config.load_config()
    cfg[_OPTOUT_FLAG] = bool(value)
    config.save_config(cfg)


def run(cwd: str | None = None, force: bool = False,
        in_chat: bool = False) -> list[str]:
    """Install hooks for every harness we can detect here.

    `force` skips the once-ever gate. That gate exists so the implicit run doesn't
    write into every directory you happen to invoke learnlance from — but four of
    the six hooks are *per-project*, so `learnlance setup` must be able to
    configure a new project regardless of it.
    """
    cwd = cwd or os.getcwd()
    actions: list[str] = []

    # An explicit `uninstall` wins over the implicit run. `force` (i.e. the user
    # ran `setup`/`install` on purpose) clears the opt-out.
    if opted_out():
        if not force:
            return []
        set_opted_out(False)

    # Claude Code's hook is user-level, so install it regardless — it's the
    # original use case and costs nothing when Claude Code isn't present.
    try:
        if not claude_hook_present():
            install.install_hook()
            actions.append("Claude Code")
            config.log("[autosetup] installed Claude Code Stop hook")
    except Exception as e:
        config.log(f"[autosetup] Claude Code hook failed: {e!r}")

    for name in detect_harnesses(cwd):
        spec = HARNESSES[name]
        try:
            # When forced, reinstall even if present — the mode may have changed.
            # Reconcile mode automatically: a first run may have installed a
            # normal hook before the user selected in-chat fallback, and vice
            # versa. Do not leave a valid-but-wrong-mode hook in place.
            #
            # Only compare modes for harnesses that can actually *record* one.
            # git has no in-chat mode, so hook_in_chat() is permanently False
            # there; comparing it to in_chat=True re-installed the hook (and
            # re-announced it) on every single invocation.
            if hook_present(name, cwd) and not force:
                if name not in inchat.SUPPORTED:
                    continue  # no in-chat mode to reconcile (e.g. git)
                if hook_in_chat(name, cwd) == in_chat:
                    continue
            result = spec["install"](cwd, in_chat)
            # Installers return a human-readable string; log it, summarize short.
            config.log(f"[autosetup] {result}")
            actions.append(spec["label"])
        except Exception as e:
            config.log(f"[autosetup] {name} hook failed: {e!r}")

    # Keep this flag for compatibility/diagnostics, but do not use it as a gate:
    # auto-setup must also work when the user moves to a new project after the
    # first invocation.
    mark_done()
    return actions
