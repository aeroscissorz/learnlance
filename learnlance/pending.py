"""Per-session buffer of code edits captured mid-session.

Claude Code hands learnlance a whole transcript at the end of a turn, so the
edits are all there for the taking. Other harnesses (Kiro, Cursor, …) instead
notify us of *one tool call at a time* via a PostToolUse-style hook, and give us
nothing at the end.

This module bridges that gap: each edit is appended to a small per-session file
as it happens, then the session's Stop hook drains the buffer and gets the same
`[{file, code, action}]` shape `transcript.collect_new_generation` produces.

The store is a **directory per session with one file per edit**. Agents routinely
issue several edits in parallel, so several hook processes write at the same
moment. Two earlier designs both lost edits:

  * read-modify-write of a single JSON document — one of any two concurrent
    edits was silently dropped, and a torn write invalidated the whole turn;
  * appending lines to one JSONL file — Windows emulates ``O_APPEND`` as a
    non-atomic seek-then-write, so concurrent appends still overwrote each other
    (measured: 10 of 40 parallel edits survived).

Giving every edit its own uniquely-named file means no two writers ever touch the
same path, which needs no locking and behaves identically on every platform.
Filenames are timestamp-prefixed so reading them back preserves edit order.

Everything here is best-effort — a failed buffer write must never break the
user's editing session, so nothing raises.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import config

# Keep a session's buffer bounded so a very long session can't grow unbounded.
_MAX_EDITS = 400


def _safe(session: str) -> str:
    """Make a session id safe to use as a filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", session or "unknown")[:120] or "unknown"


def path_for(session: str) -> Path:
    """The session's buffer directory (one file per edit inside it)."""
    return config.PENDING_DIR / _safe(session)


def _legacy_path(session: str) -> Path:
    """The old single-document buffer, still drained if one is left over."""
    return config.PENDING_DIR / f"{_safe(session)}.json"


def _records(session: str) -> list[Path]:
    d = path_for(session)
    if not d.is_dir():
        return []
    try:
        # Names start with a nanosecond timestamp, so sorting restores order.
        return sorted(d.glob("*.json"))
    except OSError:
        return []


def load(session: str) -> dict:
    """Return {"cwd": str, "edits": [...], "prompt": str} for a session.

    An unreadable record is skipped rather than discarding the whole buffer — a
    partially written file costs one edit, not the turn.
    """
    data: dict = {"cwd": "", "edits": [], "prompt": ""}

    legacy = _legacy_path(session)
    if legacy.exists():
        try:
            old = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                data["cwd"] = old.get("cwd", "") or ""
                data["prompt"] = old.get("prompt", "") or ""
                data["edits"] = [e for e in (old.get("edits") or [])
                                 if isinstance(e, dict)]
        except Exception:
            pass

    for f in _records(session):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue  # torn or half-written record
        if not isinstance(rec, dict):
            continue
        if rec.get("cwd"):
            data["cwd"] = rec["cwd"]
        if rec.get("prompt"):
            data["prompt"] = rec["prompt"]
        edit = rec.get("edit")
        if isinstance(edit, dict):
            data["edits"].append(edit)

    if len(data["edits"]) > _MAX_EDITS:
        data["edits"] = data["edits"][-_MAX_EDITS:]
    return data


def add_edit(session: str, edit: dict, cwd: str = "", prompt: str = "") -> int:
    """Record one {file, code, action} edit for a session.

    Returns the buffer's edit count afterwards (0 on failure). The edit goes in
    its own file, so concurrent writers never contend for the same path.
    """
    try:
        config.ensure_home()
        d = path_for(session)
        d.mkdir(parents=True, exist_ok=True)
        rec = {"edit": edit}
        if cwd:
            rec["cwd"] = cwd
        if prompt:
            rec["prompt"] = prompt
        name = f"{time.time_ns():020d}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        # Write to a temp name then rename, so a reader can never observe a
        # half-written record under its final name.
        tmp = d / (name + ".part")
        tmp.write_text(json.dumps(rec, ensure_ascii=True), encoding="utf-8")
        os.replace(tmp, d / name)
        return count(session)
    except Exception as e:
        config.log(f"pending.add_edit failed for {session!r}: {e!r}")
        return 0


def count(session: str) -> int:
    """Number of buffered edits."""
    try:
        if _legacy_path(session).exists():
            return len(load(session)["edits"])  # mixed store: count properly
        return min(len(_records(session)), _MAX_EDITS)
    except Exception:
        return 0


def clear(session: str) -> None:
    """Delete a session's buffer.

    Deliberately separate from `load`: the buffer is only discarded once its
    contents have actually made it into the graph. Reading and deleting in one
    step meant a turn's work was destroyed whenever the analysis couldn't run —
    no LLM installed, CLI erroring — with nothing to retry from.
    """
    try:
        d = path_for(session)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        legacy = _legacy_path(session)
        if legacy.exists():
            legacy.unlink()
    except Exception as e:
        config.log(f"pending.clear failed for {session!r}: {e!r}")


def drain(session: str) -> dict:
    """Read and delete in one step. Prefer `load` + `clear` so a failed analysis
    leaves the edits in place to be retried; this remains for callers that really
    do want the buffer gone regardless."""
    data = load(session)
    clear(session)
    return data
