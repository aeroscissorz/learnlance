"""Per-session buffer of code edits captured mid-session.

Claude Code hands learnlance a whole transcript at the end of a turn, so the
edits are all there for the taking. Other harnesses (Kiro, Cursor, …) instead
notify us of *one tool call at a time* via a PostToolUse-style hook, and give us
nothing at the end.

This module bridges that gap: each edit is appended to a small per-session JSON
file as it happens, then the session's Stop hook drains the buffer and gets the
same `[{file, code, action}]` shape `transcript.collect_new_generation` produces.

Everything here is best-effort — a failed buffer write must never break the
user's editing session, so nothing raises.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

# Keep a session's buffer bounded so a very long session can't grow unbounded.
_MAX_EDITS = 400


def _safe(session: str) -> str:
    """Make a session id safe to use as a filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", session or "unknown")[:120] or "unknown"


def path_for(session: str) -> Path:
    return config.PENDING_DIR / f"{_safe(session)}.json"


def load(session: str) -> dict:
    """Return {"cwd": str, "edits": [...], "prompt": str} for a session."""
    p = path_for(session)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("cwd", "")
                data.setdefault("edits", [])
                data.setdefault("prompt", "")
                return data
        except Exception:
            pass
    return {"cwd": "", "edits": [], "prompt": ""}


def add_edit(session: str, edit: dict, cwd: str = "", prompt: str = "") -> int:
    """Append one {file, code, action} edit to the session buffer.

    Returns the buffer's edit count afterwards (0 on failure).
    """
    try:
        config.ensure_home()
        data = load(session)
        if cwd:
            data["cwd"] = cwd
        if prompt:
            data["prompt"] = prompt
        data["edits"].append(edit)
        if len(data["edits"]) > _MAX_EDITS:
            data["edits"] = data["edits"][-_MAX_EDITS:]
        path_for(session).write_text(json.dumps(data), encoding="utf-8")
        return len(data["edits"])
    except Exception as e:
        config.log(f"pending.add_edit failed for {session!r}: {e!r}")
        return 0


def clear(session: str) -> None:
    """Delete a session's buffer.

    Deliberately separate from `load`: the buffer is only discarded once its
    contents have actually made it into the graph. Reading and deleting in one
    step meant a turn's work was destroyed whenever the analysis couldn't run —
    no LLM installed, CLI erroring — with nothing to retry from.
    """
    try:
        p = path_for(session)
        if p.exists():
            p.unlink()
    except Exception as e:
        config.log(f"pending.clear failed for {session!r}: {e!r}")


def drain(session: str) -> dict:
    """Read and delete in one step. Prefer `load` + `clear` so a failed analysis
    leaves the edits in place to be retried; this remains for callers that really
    do want the buffer gone regardless."""
    data = load(session)
    clear(session)
    return data
