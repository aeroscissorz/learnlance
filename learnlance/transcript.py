"""Parse a Claude Code session transcript (JSONL) and pull out the code that
Claude generated or edited in the turn(s) we haven't processed yet."""
from __future__ import annotations

import json
from typing import Any

# Tool names that represent "Claude wrote/changed code".
_GEN_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Update"}


def read_entries(path: str) -> list[dict]:
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return entries


def _edit_from_tool_use(block: dict) -> list[dict]:
    """Normalize a tool_use block into [{file, code}] fragments."""
    name = block.get("name", "")
    inp = block.get("input", {}) or {}
    out: list[dict] = []
    fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "?"
    if name == "Write":
        out.append({"file": fp, "code": inp.get("content", ""), "action": "created"})
    elif name in ("Edit", "Update"):
        out.append({"file": fp, "code": inp.get("new_string", ""), "action": "edited"})
    elif name == "MultiEdit":
        for e in inp.get("edits", []) or []:
            out.append({"file": fp, "code": e.get("new_string", ""), "action": "edited"})
    elif name == "NotebookEdit":
        out.append({"file": fp, "code": inp.get("new_source", ""), "action": "edited"})
    return [o for o in out if o.get("code")]


def collect_new_generation(entries: list[dict], since_uuid: str | None) -> dict:
    """Return everything generated after `since_uuid`.

    -> {"edits": [...], "text": "...", "last_uuid": "...", "user_prompt": "..."}
    """
    start = 0
    if since_uuid:
        for i, e in enumerate(entries):
            if e.get("uuid") == since_uuid:
                start = i + 1
                break

    new = entries[start:]
    edits: list[dict] = []
    texts: list[str] = []
    last_user = ""

    for e in new:
        etype = e.get("type")
        msg = e.get("message", {}) or {}
        content = msg.get("content", [])
        if etype == "user":
            # Capture the most recent human prompt for context (skip tool_result echoes).
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        last_user = b.get("text", "") or last_user
        elif etype == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    texts.append(b.get("text", ""))
                elif b.get("type") == "tool_use" and b.get("name") in _GEN_TOOLS:
                    edits.extend(_edit_from_tool_use(b))

    last_uuid = entries[-1].get("uuid") if entries else since_uuid
    return {
        "edits": edits,
        "text": "\n\n".join(t for t in texts if t).strip(),
        "last_uuid": last_uuid,
        "user_prompt": last_user.strip(),
    }


def build_input_blob(gen: dict, max_chars: int) -> str:
    """Turn collected edits into a compact, budget-capped string for the API."""
    parts: list[str] = []
    if gen.get("user_prompt"):
        parts.append("USER ASKED:\n" + gen["user_prompt"][:800])
    per = max(400, max_chars // max(1, len(gen["edits"])))
    for ed in gen["edits"]:
        code = ed["code"]
        if len(code) > per:
            code = code[:per] + "\n… (truncated)"
        parts.append(f"FILE {ed['file']} ({ed['action']}):\n{code}")
    blob = "\n\n---\n\n".join(parts)
    return blob[:max_chars]
