"""Install / uninstall learnlance hooks.

Per harness:
  * Claude Code  Stop hook in ~/.claude/settings.json (native, per-turn).
  * Kiro         PostToolUse + Stop in .kiro/hooks/learnlance.json.
  * Cursor       afterFileEdit + stop in .cursor/hooks.json.
  * Copilot      postToolUse + agentStop in .github/hooks/learnlance.json.
  * Gemini CLI   AfterTool + AfterAgent in .gemini/settings.json.
  * git          post-commit in a repo (universal fallback — triggers on commit,
                 so it works with any editor, but see ARCHITECTURE.md for why
                 it's the last resort rather than the default).

Every hook runs the same entrypoint with `--source <harness>`; the harness's own
stdin JSON is passed through untouched. Each hook matcher is derived from the
matching adapter's `write_tools` table, so the tools we ask to be notified about
can't drift from the ones we know how to read.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import adapters, config

MARK = "learnlance"  # substring used to recognize our own hook entry
GIT_MARK = "learnlance-post-commit"  # marker line inside the git hook script
KIRO_MARK = "learnlance-kiro"  # identifier for the Kiro hook file
KIRO_HOOK_FILENAME = "learnlance.json"  # hook file name inside .kiro/hooks/
COPILOT_HOOK_FILENAME = "learnlance.json"  # hook file name inside .github/hooks/


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


# --------------------------------------------------------------------------- #
# Shared helpers for the tool-at-a-time harnesses
# --------------------------------------------------------------------------- #
def _source_cmd(source: str, in_chat: bool = False) -> str:
    """The command a harness runs: our entrypoint with the adapter pinned.

    `--in-chat` only goes on the end-of-turn hook; capturing an edit is identical
    either way.
    """
    cmd = f"{hook_command()} --source {source}"
    return f"{cmd} --in-chat" if in_chat else cmd


def _matcher_for(source: str) -> str:
    """Regex alternation of the tools whose output that adapter can read."""
    return "|".join(adapters.write_tools_for(source))


def _read_json(p: Path) -> dict:
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _is_ours(entry) -> bool:
    """True if a hook entry is one we wrote (checked before we replace it)."""
    return MARK in json.dumps(entry)


def _merge_event(hooks: dict, event: str, entry: dict) -> None:
    """Put `entry` on `hooks[event]`, replacing any learnlance entry already there.

    Used for the harnesses whose config is a shared file we must not clobber:
    other tools' hooks in the same file are left exactly as they are.
    """
    existing = hooks.get(event)
    if not isinstance(existing, list):
        existing = []
    hooks[event] = [e for e in existing if not _is_ours(e)] + [entry]


def _drop_ours(hooks: dict, events: tuple[str, ...]) -> int:
    """Remove our entries from the given events. Returns how many were removed."""
    removed = 0
    for event in events:
        existing = hooks.get(event)
        if not isinstance(existing, list):
            continue
        keep = [e for e in existing if not _is_ours(e)]
        removed += len(existing) - len(keep)
        if keep:
            hooks[event] = keep
        else:
            hooks.pop(event, None)
    return removed


# --------------------------------------------------------------------------- #
# Kiro (.kiro/hooks/learnlance.json — our own file)
# --------------------------------------------------------------------------- #
def _kiro_hooks_dir(project: str) -> Path:
    return Path(project) / ".kiro" / "hooks"


def install_kiro_hook(project: str, in_chat: bool = False) -> str:
    """PostToolUse captures each edit; Stop analyzes the session.

    With `in_chat`, a third hook is added using Kiro's `agent` action, which is how
    the prompt reaches the model — Kiro's command hooks have no field for
    submitting a follow-up turn, unlike the other harnesses.
    """
    target = _kiro_hooks_dir(project) / KIRO_HOOK_FILENAME
    matcher = _matcher_for("kiro")
    hooks = [
        {"name": f"{KIRO_MARK}: capture code edits",
         "trigger": "PostToolUse", "matcher": matcher,
         "action": {"type": "command", "command": _source_cmd("kiro")}},
        {"name": f"{KIRO_MARK}: learn from the session",
         "trigger": "Stop",
         "action": {"type": "command", "command": _source_cmd("kiro", in_chat)}},
    ]
    if in_chat:
        from . import inchat
        hooks.append({
            "name": f"{KIRO_MARK}: ask the agent to record concepts",
            "trigger": "Stop",
            "action": {"type": "agent", "prompt": inchat.static_prompt(config.load_config())},
        })
    _write_json(target, {"version": "v1", "hooks": hooks})

    out = (f"Installed Kiro hooks at {target}\n"
           f"  PostToolUse ({matcher}) -> captures each edit\n"
           f"  Stop                     -> learns from the session")
    if in_chat:
        out += ("\n  Stop (agent)             -> asks the agent to name the concepts"
                "\n  in-chat mode: no LLM CLI needed; analysis happens in your chat.")
    return out


def uninstall_kiro_hook(project: str) -> str:
    hooks_dir = _kiro_hooks_dir(project)
    target = hooks_dir / KIRO_HOOK_FILENAME
    if not target.exists():
        return "No learnlance Kiro hook found here; nothing to remove."
    if MARK not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"Hook file exists at {target} but doesn't appear to be ours.\n"
                f"Remove it manually if you're sure.")
    target.unlink()
    try:
        if hooks_dir.exists() and not any(hooks_dir.iterdir()):
            hooks_dir.rmdir()
    except Exception:
        pass
    return f"Removed learnlance Kiro hook at {target}"


# --------------------------------------------------------------------------- #
# Cursor (.cursor/hooks.json — shared file, merge)
# --------------------------------------------------------------------------- #
CURSOR_EVENTS = ("afterFileEdit", "stop")


def cursor_hooks_path(project: str) -> Path:
    return Path(project) / ".cursor" / "hooks.json"


def install_cursor_hook(project: str, in_chat: bool = False) -> str:
    """`afterFileEdit` exists for exactly this ("accounting of agent-written
    code"); `stop` fires when the agent loop ends. Timeouts are in seconds.

    In-chat mode leans on `stop`'s `followup_message`, and on `loop_limit` as a
    second line of defence behind our own one-ask cap.
    """
    target = cursor_hooks_path(project)
    data = _read_json(target)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return f"{target} has an unexpected 'hooks' shape; leaving it alone."

    _merge_event(hooks, "afterFileEdit",
                 {"command": _source_cmd("cursor"), "timeout": 30})
    stop_entry = {"command": _source_cmd("cursor", in_chat), "timeout": 30}
    if in_chat:
        stop_entry["loop_limit"] = 1
    _merge_event(hooks, "stop", stop_entry)
    _write_json(target, data)

    out = (f"Installed Cursor hooks at {target}\n"
           f"  afterFileEdit -> captures each edit\n"
           f"  stop          -> learns from the session")
    if in_chat:
        out += "\n  in-chat mode: analysis happens in your chat (loop_limit 1)."
    return out


def uninstall_cursor_hook(project: str) -> str:
    target = cursor_hooks_path(project)
    if not target.exists():
        return "No Cursor hooks.json here; nothing to remove."
    data = _read_json(target)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return "No learnlance Cursor hooks found; nothing to remove."
    n = _drop_ours(hooks, CURSOR_EVENTS)
    if not n:
        return "No learnlance Cursor hooks found; nothing to remove."
    _write_json(target, data)
    return f"Removed {n} learnlance hook entr(y/ies) from {target}"


# --------------------------------------------------------------------------- #
# GitHub Copilot (.github/hooks/learnlance.json — our own file)
# --------------------------------------------------------------------------- #
def copilot_hooks_path(project: str) -> Path:
    return Path(project) / ".github" / "hooks" / COPILOT_HOOK_FILENAME


def install_copilot_hook(project: str, in_chat: bool = False) -> str:
    """One registration covering Copilot CLI, the cloud agent, and VS Code Copilot
    Chat — all three read `.github/hooks/*.json`.

    **Event names are PascalCase on purpose.** VS Code's end-of-turn event is
    `Stop`; Copilot CLI's is `agentStop`, and VS Code maps camelCase to PascalCase,
    turning `agentStop` into a non-existent `AgentStop`. PascalCase is native to
    VS Code and documented by Copilot as its VS Code-compatible format, so writing
    it once fires on every surface. Registering both spellings would instead make
    Copilot CLI fire twice per event.

    `command` is the cross-platform field: Copilot copies it to bash and
    powershell, and VS Code falls back to it when no OS-specific override is set,
    so a single entry covers every platform.
    """
    target = copilot_hooks_path(project)
    _write_json(target, {
        "version": 1,
        "hooks": {
            "PostToolUse": [{"type": "command", "command": _source_cmd("copilot"),
                             "matcher": _matcher_for("copilot"), "timeoutSec": 30}],
            "Stop": [{"type": "command",
                      "command": _source_cmd("copilot", in_chat),
                      "timeoutSec": 30}],
        },
    })
    out = (f"Installed Copilot hooks at {target}\n"
           f"  PostToolUse -> captures each edit\n"
           f"  Stop        -> learns from the turn\n"
           f"  covers Copilot CLI, the cloud agent, and VS Code Copilot Chat")
    if in_chat:
        out += "\n  in-chat mode: analysis happens in your chat."
    return out


def uninstall_copilot_hook(project: str) -> str:
    target = copilot_hooks_path(project)
    if not target.exists():
        return "No learnlance Copilot hook found here; nothing to remove."
    if MARK not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"Hook file exists at {target} but doesn't appear to be ours.\n"
                f"Remove it manually if you're sure.")
    target.unlink()
    return f"Removed learnlance Copilot hook at {target}"


# --------------------------------------------------------------------------- #
# Gemini CLI (.gemini/settings.json — shared file, merge, two-level nesting)
# --------------------------------------------------------------------------- #
GEMINI_EVENTS = ("AfterTool", "AfterAgent")


def gemini_settings_path(project: str | None = None) -> Path:
    """Project settings when given a project, else the user-level file."""
    base = Path(project) if project else Path.home()
    return base / ".gemini" / "settings.json"


def install_gemini_hook(project: str | None = None, in_chat: bool = False) -> str:
    """Gemini nests twice: event -> [{matcher, hooks: [{...}]}]. Timeouts are in
    **milliseconds** here, unlike every other harness we support.

    In-chat mode uses `AfterAgent`'s `decision: "deny"`, whose `reason` is sent to
    the agent as a new prompt.
    """
    target = gemini_settings_path(project)
    data = _read_json(target)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return f"{target} has an unexpected 'hooks' shape; leaving it alone."

    def inner(cmd):
        return [{"type": "command", "command": cmd,
                 "name": "learnlance", "timeout": 60000}]

    _merge_event(hooks, "AfterTool",
                 {"matcher": _matcher_for("gemini"),
                  "hooks": inner(_source_cmd("gemini"))})
    _merge_event(hooks, "AfterAgent",
                 {"hooks": inner(_source_cmd("gemini", in_chat))})
    _write_json(target, data)

    out = (f"Installed Gemini CLI hooks at {target}\n"
           f"  AfterTool  -> captures each edit\n"
           f"  AfterAgent -> learns from the turn")
    if in_chat:
        out += "\n  in-chat mode: analysis happens in your chat."
    return out


def uninstall_gemini_hook(project: str | None = None) -> str:
    target = gemini_settings_path(project)
    if not target.exists():
        return "No Gemini settings.json here; nothing to remove."
    data = _read_json(target)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return "No learnlance Gemini hooks found; nothing to remove."
    n = _drop_ours(hooks, GEMINI_EVENTS)
    if not n:
        return "No learnlance Gemini hooks found; nothing to remove."
    _write_json(target, data)
    return f"Removed {n} learnlance hook entr(y/ies) from {target}"


# --------------------------------------------------------------------------- #
# Antigravity (.agents/hooks.json — shared file of *named blocks*, merge)
# --------------------------------------------------------------------------- #
ANTIGRAVITY_BLOCK = "learnlance"  # our top-level block name in hooks.json


def antigravity_hooks_path(project: str) -> Path:
    return Path(project) / ".agents" / "hooks.json"


def install_antigravity_hook(project: str, in_chat: bool = False) -> str:
    """Antigravity keys hooks.json by *named block*, each with its own `enabled`
    flag — and that flag defaults to false, so a correctly-shaped config sits
    inert until it's set. We always write `enabled: true` for exactly that reason.

    Inside a block the shape is Gemini's: event -> [{matcher, hooks:[…]}], but the
    event names are Claude's (`PostToolUse`, `Stop`).
    """
    target = antigravity_hooks_path(project)
    data = _read_json(target)

    data[ANTIGRAVITY_BLOCK] = {
        "enabled": True,
        "PostToolUse": [{"matcher": _matcher_for("antigravity"),
                         "hooks": [{"type": "command",
                                    "command": _source_cmd("antigravity")}]}],
        "Stop": [{"hooks": [{"type": "command",
                             "command": _source_cmd("antigravity", in_chat)}]}],
    }
    _write_json(target, data)
    out = (f"Installed Antigravity hooks at {target}\n"
           f"  PostToolUse -> captures each edit\n"
           f"  Stop        -> learns from the turn")
    if in_chat:
        out += "\n  in-chat mode: analysis happens in your chat."
    return out + (
        f"\n  NOTE: open bug reports say Stop/PostToolUse may not fire in the"
        f"\n        Antigravity IDE (they do in Antigravity CLI). Verify with"
        f"\n        `learnlance doctor` after a turn, and see ARCHITECTURE.md.")


def uninstall_antigravity_hook(project: str) -> str:
    target = antigravity_hooks_path(project)
    if not target.exists():
        return "No Antigravity hooks.json here; nothing to remove."
    data = _read_json(target)
    if ANTIGRAVITY_BLOCK not in data:
        return "No learnlance Antigravity block found; nothing to remove."
    data.pop(ANTIGRAVITY_BLOCK, None)
    _write_json(target, data)
    return f"Removed the learnlance block from {target}"
