"""Harness adapters: translate each tool's hook payload into a `CodeEvent`.

Adding support for a new harness = adding one adapter here + one installer in
install.py. The core engine (core.py) is never touched.

Currently implemented: Claude Code, git (universal), and a generic passthrough.
Cursor / Copilot / Gemini adapters are documented in ARCHITECTURE.md and slot in
the same way once we wire their installers.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from abc import ABC, abstractmethod

from . import transcript
from .events import CodeEvent


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class HookAdapter(ABC):
    source = "generic"

    @abstractmethod
    def to_event(self, payload: dict, state: dict, cfg: dict) -> CodeEvent | None:
        """Return a CodeEvent (possibly with skip_reason), or None if the payload
        isn't usable. May update `state` in place to track a resume cursor."""
        ...


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #
class ClaudeAdapter(HookAdapter):
    source = "claude"

    def to_event(self, payload, state, cfg):
        tpath = payload.get("transcript_path", "")
        session = payload.get("session_id") or "unknown"
        cwd = payload.get("cwd", "")
        if not tpath:
            return None

        since = state.get(session, {}).get("last_uuid")
        entries = transcript.read_entries(tpath)
        if not entries:
            return None
        gen = transcript.collect_new_generation(entries, since)
        # Advance the cursor so the same turn is never reprocessed.
        state[session] = {"last_uuid": gen["last_uuid"], "updated": _now()}

        edits = gen["edits"]
        files = sorted({e["file"] for e in edits})
        total = sum(len(e["code"]) for e in edits)
        if not edits or total < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, "after_agent_turn", cwd, session, files,
                             skip_reason="no substantive code this turn")
        blob = transcript.build_input_blob(gen, int(cfg.get("max_input_chars", 14000)))
        return CodeEvent(self.source, "after_agent_turn", cwd, session, files, blob,
                         user_prompt=gen.get("user_prompt", ""))


# --------------------------------------------------------------------------- #
# git (universal — works with ANY editor/agent, triggers on commit)
# --------------------------------------------------------------------------- #
def _git(cwd: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=30)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


class GitAdapter(HookAdapter):
    source = "git"

    def to_event(self, payload, state, cfg):
        cwd = payload.get("cwd", "")
        commit = payload.get("commit", "HEAD")
        if not cwd:
            return None
        sha = _git(cwd, "rev-parse", commit).strip()
        if not sha:
            return None
        session = f"git:{cwd}"
        since = state.get(session, {}).get("last_commit")
        state[session] = {"last_commit": sha, "updated": _now()}
        if since == sha:
            return CodeEvent(self.source, "after_commit", cwd, session,
                             skip_reason="commit already processed")

        files = [f for f in _git(cwd, "diff-tree", "--no-commit-id",
                                 "--name-only", "-r", sha).splitlines() if f.strip()]
        diff = _git(cwd, "show", sha, "--unified=0", "--format=", "--no-color")
        added = [ln[1:] for ln in diff.splitlines()
                 if ln.startswith("+") and not ln.startswith("+++")]
        msg = _git(cwd, "log", "-1", "--format=%s", sha).strip()

        code = "\n".join(added)
        if len(code) < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, "after_commit", cwd, session, files,
                             skip_reason="no substantive code in commit")
        header = f"COMMIT: {msg}\n\n" if msg else ""
        blob = (header + code)[: int(cfg.get("max_input_chars", 14000))]
        return CodeEvent(self.source, "after_commit", cwd, session, files, blob,
                         user_prompt=msg)


# --------------------------------------------------------------------------- #
# generic passthrough — caller pre-assembles the event
# --------------------------------------------------------------------------- #
class GenericAdapter(HookAdapter):
    source = "generic"

    def to_event(self, payload, state, cfg):
        blob = payload.get("blob", "")
        files = payload.get("files", []) or []
        cwd = payload.get("cwd", "")
        session = payload.get("session") or "generic"
        if len(blob) < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, payload.get("event", "generic"), cwd,
                             session, files, skip_reason="blob too small")
        return CodeEvent(self.source, payload.get("event", "generic"), cwd,
                         session, files, blob, user_prompt=payload.get("user_prompt", ""))


_ADAPTERS = {"git": GitAdapter, "generic": GenericAdapter}


def detect(payload: dict) -> HookAdapter | None:
    """Pick the right adapter for an incoming hook payload."""
    src = payload.get("source")
    if src in _ADAPTERS:
        return _ADAPTERS[src]()
    # Claude Code payloads are recognized by their transcript_path field.
    if payload.get("transcript_path"):
        return ClaudeAdapter()
    return None
