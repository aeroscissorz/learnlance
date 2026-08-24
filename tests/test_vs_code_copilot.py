"""VS Code Copilot Chat, and the three ways it differs from Copilot CLI.

Each difference fails silently — the hook runs, captures nothing, and the graph
just stays empty:

  1. Its end-of-turn event is `Stop`; the CLI's is `agentStop`. VS Code maps
     camelCase config to PascalCase, so `agentStop` becomes a non-existent
     `AgentStop` and the analyze hook never fires.
  2. Its write tools have entirely different names.
  3. It ignores hook matchers, so the adapter must do all the filtering.
"""
from __future__ import annotations

import json

import pytest

from learnlance import adapters, install, pending
from conftest import CODE

VSCODE_WRITE_TOOLS = ("create_file", "createFile", "replace_string_in_file",
                      "insert_edit_into_file", "editFiles")
CLI_WRITE_TOOLS = ("create", "edit", "str_replace_editor", "apply_patch")
CLAUDE_WRITE_TOOLS = ("Write", "Edit", "MultiEdit")


def _vscode_capture(session, cwd, tool, args=None):
    """VS Code's shape: PascalCase event, snake_case fields, camelCase tool props."""
    return {"source": "copilot", "hook_event_name": "PostToolUse",
            "session_id": session, "cwd": cwd, "timestamp": "2026-08-23T00:00:00Z",
            "tool_name": tool,
            "tool_input": args or {"filePath": "app/delta.py", "content": CODE},
            "tool_result": {"result_type": "success", "text_result_for_llm": "ok"}}


# --------------------------------------------------------------------------- #
# 1. Tool names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", VSCODE_WRITE_TOOLS)
def test_vscode_write_tools_are_captured(tool, cfg, project, home):
    p = _vscode_capture(f"vsc-{tool}", str(project), tool)
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert "buffered" in (event.skip_reason or "")
    assert len(pending.load(f"vsc-{tool}")["edits"]) == 1


@pytest.mark.parametrize("tool", CLI_WRITE_TOOLS + CLAUDE_WRITE_TOOLS)
def test_the_other_copilot_surfaces_still_work(tool, cfg, project, home):
    """One adapter serves the CLI, the cloud agent and VS Code, so its table is the
    union of all three naming schemes."""
    args = {"path": "app/delta.py", "content": CODE, "new_string": CODE,
            "patch": CODE}
    p = _vscode_capture(f"cli-{tool}", str(project), tool, args)
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert "buffered" in (event.skip_reason or ""), (tool, event.skip_reason)


@pytest.mark.parametrize("field", ["content", "newString", "new_string", "code"])
def test_content_is_found_under_any_documented_field(field, cfg, project, home):
    """Copilot types tool arguments as `unknown`, so the adapter tries candidates
    rather than assuming one name."""
    p = _vscode_capture(f"f-{field}", str(project), "replace_string_in_file",
                        {"filePath": "app/delta.py", field: CODE})
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert "buffered" in (event.skip_reason or ""), field


def test_camelcase_tool_input_path_is_read(cfg, project, home):
    """VS Code uses tool_input.filePath where Claude uses tool_input.file_path."""
    p = _vscode_capture("path", str(project), "create_file",
                        {"filePath": "app/only-camel.py", "content": CODE})
    adapters.detect(p).to_event(p, {}, cfg)
    assert pending.load("path")["edits"][0]["file"] == "app/only-camel.py"


# --------------------------------------------------------------------------- #
# 2. End-of-turn event
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("event_name", ["Stop", "agentStop"])
def test_both_end_of_turn_spellings_drain_the_buffer(event_name, cfg, project, home):
    """`Stop` is VS Code's; `agentStop` is Copilot CLI's. Both must work, because
    one registration serves both surfaces."""
    session = f"end-{event_name}"
    p = _vscode_capture(session, str(project), "create_file")
    adapters.detect(p).to_event(p, {}, cfg)

    end = {"source": "copilot", "hook_event_name": event_name,
           "session_id": session, "cwd": str(project),
           "stop_reason": "end_turn", "stop_hook_active": False}
    event = adapters.detect(end).to_event(end, {}, cfg)

    assert event.skip_reason is None
    assert event.event == "after_agent_turn"
    assert "def delta" in event.blob


def test_installed_config_uses_pascalcase(project):
    """The actual regression: camelCase `agentStop` is rewritten by VS Code to
    `AgentStop`, which is not one of its eight events."""
    install.install_copilot_hook(str(project))
    spec = json.loads(install.copilot_hooks_path(str(project)).read_text("utf-8"))

    assert sorted(spec["hooks"]) == ["PostToolUse", "Stop"]
    assert "agentStop" not in spec["hooks"]
    assert "postToolUse" not in spec["hooks"]


def test_only_one_registration_per_event(project):
    """Registering both spellings looks like the safe fix but makes Copilot CLI
    fire twice for every event."""
    install.install_copilot_hook(str(project))
    spec = json.loads(install.copilot_hooks_path(str(project)).read_text("utf-8"))
    for event, entries in spec["hooks"].items():
        ours = [e for e in entries if "learnlance" in json.dumps(e)]
        assert len(ours) == 1, f"{event} has {len(ours)} learnlance entries"


def test_config_is_cross_platform_and_matches_the_adapter(project):
    install.install_copilot_hook(str(project))
    spec = json.loads(install.copilot_hooks_path(str(project)).read_text("utf-8"))
    entry = spec["hooks"]["PostToolUse"][0]

    # `command` is copied to bash and powershell by Copilot, and used as the
    # fallback by VS Code — so one entry covers every OS.
    assert "command" in entry and entry["type"] == "command"
    assert entry["timeoutSec"] == 30
    assert set(entry["matcher"].split("|")) == set(adapters.write_tools_for("copilot"))


# --------------------------------------------------------------------------- #
# 3. Matchers are ignored, so the adapter filters
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", ["runTerminalCommand", "readFile", "deleteFile",
                                  "grep", "glob", "web_fetch", "ask_user"])
def test_tools_that_write_no_code_are_skipped(tool, cfg, project, home):
    """VS Code fires the hook for every tool regardless of the matcher, so an
    unrecognised tool has to be skipped rather than mis-captured."""
    p = _vscode_capture("skip", str(project), tool,
                        {"filePath": "x.py", "content": "not agent-written code"})
    event = adapters.detect(p).to_event(p, {}, cfg)
    assert "no new code" in (event.skip_reason or ""), (tool, event.skip_reason)
    assert pending.load("skip")["edits"] == []


def test_a_full_vscode_turn(cfg, project, home, fake_llm):
    """Two edits and a Stop should produce one analysis containing both."""
    from learnlance import core, graph

    cfg["llm_cmd"] = fake_llm
    session = "vsc-turn"
    for path, tool in (("app/delta.py", "create_file"),
                       ("tests/test_delta.py", "createFile")):
        p = _vscode_capture(session, str(project), tool,
                            {"filePath": path, "content": CODE})
        adapters.detect(p).to_event(p, {}, cfg)

    end = {"source": "copilot", "hook_event_name": "Stop",
           "session_id": session, "cwd": str(project), "stop_hook_active": False}
    event = adapters.detect(end).to_event(end, {}, cfg)
    assert event.files == ["app/delta.py", "tests/test_delta.py"]

    core.process_event(cfg, event)
    g = graph.load_project(str(project))
    assert "Delta encoding" in [n["name"] for n in g["nodes"].values()
                                if not n.get("placeholder")]
    assert pending.load(session)["edits"] == []
