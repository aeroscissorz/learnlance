"""Shared fixtures.

Every test runs against an isolated `LEARNLANCE_HOME` so nothing touches the
developer's real graph. `config` derives its paths from `HOME` at import time, so
the `home` fixture repoints each of them rather than just `HOME`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from learnlance import config

# Paths config derives from HOME. Kept here (not imported) on purpose: if someone
# adds a new one and forgets to list it, a test will write to the real home and the
# resulting failure points straight at this list.
_FILES = {
    "CONFIG_PATH": "config.json",
    "GRAPH_PATH": "graph.json",
    "HTML_PATH": "graph.html",
    "STATE_PATH": "state.json",
    "LOG_PATH": "learnlance.log",
    "PROJECTS_REGISTRY": "projects.json",
}
_DIRS = {
    "INSIGHTS_DIR": "insights",
    "WORK_DIR": "work",
    "PROJECTS_DIR": "projects",
    "PENDING_DIR": "pending",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect all learnlance storage into a temp directory.

    `Path.home()` is redirected too, and that part is not optional. Several
    installers target user-level config — Gemini falls back to
    `~/.gemini/settings.json` when a project has no local one — and those paths come
    from `Path.home()`, not `config.HOME`. Without this patch the suite rewrote the
    developer's real Gemini settings, which is exactly the kind of damage a test
    run must never do.
    """
    h = tmp_path / "llhome"
    monkeypatch.setattr(config, "HOME", h)
    for attr, leaf in {**_FILES, **_DIRS}.items():
        monkeypatch.setattr(config, attr, h / leaf)

    fake_user_home = tmp_path / "userhome"
    fake_user_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_user_home))

    config.ensure_home()
    return h


@pytest.fixture
def user_home(tmp_path):
    """The redirected `Path.home()` used by the `home` fixture."""
    return tmp_path / "userhome"


@pytest.fixture
def project(tmp_path):
    """A stand-in user project directory."""
    p = tmp_path / "project"
    p.mkdir()
    return p


@pytest.fixture
def cfg(home):
    return config.load_config()


@pytest.fixture
def fake_llm(tmp_path):
    """An executable that behaves like an LLM CLI: prompt on stdin, JSON on stdout.

    Returns the `llm_cmd` string to put in config. Using a real subprocess rather
    than monkeypatching `insights.generate` keeps argv handling, stdin piping and
    JSON parsing under test — that's where the quoting bug lived.
    """
    script = tmp_path / "fake_llm.py"
    script.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "print(json.dumps({\n"
        "    'did': 'Did the thing.',\n"
        "    'topics': [{\n"
        "        'name': 'Delta encoding', 'category': 'algorithm',\n"
        "        'level': 'intermediate', 'explanation': 'Store differences.',\n"
        "        'why_here': 'saw delta' if 'delta' in prompt else 'n/a',\n"
        "        'tags': ['compression'], 'related': ['Data compression'],\n"
        "    }],\n"
        "}))\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


# --------------------------------------------------------------------------- #
# Payload builders — one per harness, shaped as that vendor documents it
# --------------------------------------------------------------------------- #
CODE = ("def delta(xs):\n"
        "    return [b - a for a, b in zip(xs, xs[1:])]\n")


def capture_payload(source: str, session: str, cwd: str, code: str = CODE,
                    path: str = "app/delta.py", tool: str | None = None) -> dict:
    """A payload announcing that the agent just wrote `code` to `path`."""
    base = {"source": source, "session_id": session, "cwd": cwd}
    if source == "kiro":
        return {**base, "hook_event_name": "PostToolUse",
                "tool_name": tool or "fs_write",
                "tool_input": {"path": path, "text": code, "newStr": code}}
    if source == "cursor":
        return {**base, "hook_event_name": "afterFileEdit",
                "conversation_id": session, "workspace_roots": [cwd],
                "file_path": path,
                "edits": [{"old_string": "", "new_string": code}]}
    if source == "copilot":
        # VS Code-compatible shape: PascalCase event, snake_case fields.
        return {**base, "hook_event_name": "PostToolUse",
                "tool_name": tool or "create_file",
                "tool_input": {"filePath": path, "content": code},
                "tool_result": {"result_type": "success",
                                "text_result_for_llm": "ok"}}
    if source == "gemini":
        return {**base, "hook_event_name": "AfterTool",
                "tool_name": tool or "write_file",
                "tool_input": {"file_path": path, "content": code},
                "tool_response": {"llmContent": "written"}}
    if source == "antigravity":
        return {**base, "hook_event_name": "PostToolUse",
                "tool_name": tool or "write_file",
                "tool_input": {"file_path": path, "content": code}}
    raise AssertionError(f"no capture payload defined for {source!r}")


#: The event each harness fires when a turn ends.
END_EVENT = {
    "kiro": "Stop",
    "cursor": "stop",
    "copilot": "Stop",
    "gemini": "AfterAgent",
    "antigravity": "Stop",
}

BUFFERED = tuple(END_EVENT)


def end_payload(source: str, session: str, cwd: str, **extra) -> dict:
    return {"source": source, "session_id": session, "conversation_id": session,
            "cwd": cwd, "workspace_roots": [cwd],
            "hook_event_name": END_EVENT[source], **extra}
