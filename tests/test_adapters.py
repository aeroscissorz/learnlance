"""Adapter routing, per-harness capture, and the layering rule.

The rule, stated as a test rather than a comment:

    Adapters translate external agent protocols into CodeEvents.
    They must not contain LearnLance's analysis logic.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from learnlance import adapters, pending
from conftest import BUFFERED, CODE, capture_payload, end_payload


# --------------------------------------------------------------------------- #
# The layering rule
# --------------------------------------------------------------------------- #
def test_adapters_do_not_import_analysis_or_storage_layers():
    """An adapter that reaches into insights/graph/viz has stopped being a
    translator. Checked structurally so it can't drift back."""
    src = Path(adapters.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module is None:
            imported.update(a.name for a in node.names)  # from . import x, y
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[-1] for a in node.names)

    forbidden = {"insights", "graph", "viz", "core", "inchat"}
    assert not (imported & forbidden), (
        f"adapters.py must not import {imported & forbidden}: adapters translate "
        f"protocols into CodeEvents, they don't analyze or persist.")


def test_every_adapter_returns_a_code_event_or_none(cfg, project):
    """The one thing the core relies on from every adapter."""
    from learnlance.events import CodeEvent

    for source in BUFFERED:
        payload = capture_payload(source, "s", str(project))
        got = adapters.detect(payload).to_event(payload, {}, cfg)
        assert isinstance(got, (CodeEvent, type(None)))


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source", list(BUFFERED) + ["git", "generic", "claude"])
def test_detect_routes_by_pinned_source(source):
    got = adapters.detect({"source": source})
    assert got is not None and got.source == source


def test_detect_recognizes_claude_by_transcript_path():
    """Claude Code's hook config has no way to pass --source, so its payload is
    identified by the one field only it sends."""
    got = adapters.detect({"transcript_path": "/tmp/t.jsonl"})
    assert got is not None and got.source == "claude"


def test_detect_returns_none_for_an_unusable_payload():
    assert adapters.detect({}) is None
    assert adapters.detect({"source": "not-a-harness"}) is None


def test_install_matchers_come_from_the_adapter_tables():
    """The hook matcher and the table used to read tool output must not drift."""
    from learnlance import install

    for source in ("kiro", "copilot", "gemini", "antigravity"):
        declared = set(adapters.write_tools_for(source))
        assert declared, f"{source} declares no write tools"
        matcher = install._matcher_for(source)
        assert set(matcher.split("|")) == declared


# --------------------------------------------------------------------------- #
# Capture, per harness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source", BUFFERED)
def test_a_write_tool_is_buffered_not_analyzed(source, cfg, project, home):
    """A single edit is never worth an analysis, so capture always skips."""
    payload = capture_payload(source, f"{source}-1", str(project))
    event = adapters.detect(payload).to_event(payload, {}, cfg)

    assert event.skip_reason and "buffered" in event.skip_reason
    assert len(pending.load(f"{source}-1")["edits"]) == 1


@pytest.mark.parametrize("source", BUFFERED)
def test_end_of_turn_yields_the_captured_code(source, cfg, project, home):
    session = f"{source}-2"
    p = capture_payload(source, session, str(project))
    adapters.detect(p).to_event(p, {}, cfg)

    end = end_payload(source, session, str(project))
    event = adapters.detect(end).to_event(end, {}, cfg)

    assert event.skip_reason is None
    assert event.event == "after_agent_turn"
    assert event.files == ["app/delta.py"]
    assert "def delta" in event.blob


@pytest.mark.parametrize("source", BUFFERED)
def test_several_edits_in_a_turn_become_one_analysis(source, cfg, project, home):
    session = f"{source}-3"
    for path in ("app/a.py", "app/b.py", "app/c.py"):
        p = capture_payload(source, session, str(project), path=path)
        adapters.detect(p).to_event(p, {}, cfg)
    assert len(pending.load(session)["edits"]) == 3

    end = end_payload(source, session, str(project))
    event = adapters.detect(end).to_event(end, {}, cfg)
    assert event.files == ["app/a.py", "app/b.py", "app/c.py"]
    assert event.blob.count("FILE app/") == 3


@pytest.mark.parametrize("source", BUFFERED)
def test_non_writing_tools_are_ignored(source, cfg, project, home):
    session = f"{source}-4"
    for tool in ("read_file", "grep", "runTerminalCommand", "list_directory"):
        p = capture_payload(source, session, str(project), tool=tool)
        if source == "cursor":
            # Cursor captures per event, so give it a non-edit event instead.
            p["hook_event_name"] = "beforeReadFile"
            p.pop("edits", None)
        event = adapters.detect(p).to_event(p, {}, cfg)
        assert "no new code" in (event.skip_reason or ""), (tool, event.skip_reason)
    assert pending.load(session)["edits"] == []


def test_a_write_tool_carrying_no_content_is_ignored(cfg, project, home):
    p = capture_payload("kiro", "empty", str(project))
    p["tool_input"] = {"path": "app/x.py", "text": ""}
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert "no new code" in event.skip_reason
    assert pending.load("empty")["edits"] == []


def test_trivial_turns_are_skipped(cfg, project, home):
    """min_chars exists so renames and one-liners don't cost an LLM call."""
    p = capture_payload("kiro", "tiny", str(project), code="x = 1\n")
    adapters.detect(p).to_event(p, {}, cfg)
    end = end_payload("kiro", "tiny", str(project))
    event = adapters.detect(end).to_event(end, {}, cfg)
    assert event.skip_reason == "no substantive code this session"


def test_learnlance_own_files_are_never_captured(cfg, project, home):
    """In-chat mode has the agent write into ~/.learnlance/inbox/. That write is a
    tool call, so without this filter we'd analyze our own output next turn."""
    p = capture_payload("kiro", "self", str(project),
                        path=str(home / "inbox" / "concepts-1.json"))
    adapters.detect(p).to_event(p, {}, cfg)
    assert pending.load("self")["edits"] == []


# --------------------------------------------------------------------------- #
# Harness-specific shapes that broke at some point
# --------------------------------------------------------------------------- #
def test_cursor_reads_cwd_from_workspace_roots(cfg, project, home):
    """Cursor sends a list of roots and no `cwd` at all."""
    p = capture_payload("cursor", "cur", str(project))
    assert "cwd" in p
    del p["cwd"]
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert event.cwd == str(project)


def test_cursor_captures_every_delta_in_one_event(cfg, project, home):
    """afterFileEdit reports `edits: [{old_string, new_string}]` — several per call."""
    p = capture_payload("cursor", "cur2", str(project))
    p["edits"] = [{"old_string": "", "new_string": CODE},
                  {"old_string": "a", "new_string": "b" * 60}]
    adapters.detect(p).to_event(p, {}, cfg)
    assert len(pending.load("cur2")["edits"]) == 2


def test_cursor_session_is_the_conversation_not_the_generation(cfg, project, home):
    """generation_id changes every message; buffering against it would scatter one
    turn across many buffers."""
    p = capture_payload("cursor", "conv-1", str(project))
    p["generation_id"] = "gen-changes-every-message"
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert event.session == "conv-1"


def test_kiro_accepts_camelcase_keys(cfg, project, home):
    p = {"source": "kiro", "hookEventName": "PostToolUse", "sessionId": "kc",
         "workspaceRoot": str(project), "toolName": "fs_write",
         "toolInput": {"path": "app/x.py", "text": CODE}}
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert event.session == "kc" and event.cwd == str(project)
    assert len(pending.load("kc")["edits"]) == 1


def test_antigravity_unwraps_the_toolcall_shape(cfg, project, home):
    """Some Antigravity versions nest tool details under `toolCall`."""
    p = {"source": "antigravity", "hook_event_name": "PostToolUse",
         "session_id": "ag", "cwd": str(project),
         "toolCall": {"name": "write_file",
                      "args": {"file_path": "app/x.py", "content": CODE}}}
    adapters.detect(p).to_event(p, {}, cfg)
    assert len(pending.load("ag")["edits"]) == 1


def test_gemini_takes_the_user_prompt_from_the_end_event(cfg, project, home):
    """Gemini only reveals the request on AfterAgent, never on AfterTool — so the
    end payload has to be consulted, not just the buffer."""
    p = capture_payload("gemini", "gp", str(project))
    adapters.detect(p).to_event(p, {}, cfg)
    end = end_payload("gemini", "gp", str(project), prompt="add a delta helper")
    event = adapters.detect(end).to_event(end, {}, cfg)
    assert event.user_prompt == "add a delta helper"
    assert "USER ASKED" in event.blob


def test_claude_and_buffered_adapters_produce_the_same_blob_format(cfg, project, home):
    """The LLM must see one format regardless of which harness produced the code."""
    from learnlance import transcript

    p = capture_payload("kiro", "fmt", str(project))
    adapters.detect(p).to_event(p, {}, cfg)
    end = end_payload("kiro", "fmt", str(project))
    buffered_blob = adapters.detect(end).to_event(end, {}, cfg).blob

    claude_blob = transcript.build_input_blob(
        {"edits": [{"file": "app/delta.py", "code": CODE, "action": "created"}],
         "user_prompt": ""}, 14000)
    assert buffered_blob == claude_blob
