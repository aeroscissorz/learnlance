"""The harness-agnostic event that the core engine operates on.

Every AI coding harness (Claude Code, Cursor, Copilot, Gemini, …) has its own
hook payload shape. An *adapter* (see adapters.py) normalizes each into this
single `CodeEvent`, so the core learning engine never has to know which tool
produced the code.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CodeEvent:
    source: str  # "claude" | "git" | "generic" | "cursor" | "copilot" | "gemini"
    event: str  # normalized lifecycle: "after_agent_turn" | "after_commit" | …
    cwd: str = ""
    session: str = "unknown"
    files: list = field(default_factory=list)  # files touched
    blob: str = ""  # assembled code/diff to analyze
    user_prompt: str = ""  # optional context (the request / commit message)
    skip_reason: str | None = None  # set by the adapter when there's nothing to do
