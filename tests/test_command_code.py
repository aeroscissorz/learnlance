"""Command Code integration: the harness whose hook matcher uses display names.

Command Code tests its `matcher` against `tool_display_name` (`WRITE`/`EDIT`),
not the canonical wire names (`write_file`/`edit_file`). A matcher derived from
`write_tools` would silently match nothing, so the adapter declares it explicitly.
"""
from __future__ import annotations

import json

from learnlance import adapters, autosetup, capabilities, inchat, install, pending


def _adapter():
    return adapters.detect({"source": "commandcode"})


def test_write_and_edit_are_captured(cfg, project, home):
    a = _adapter()
    a.to_event({"source": "commandcode", "hook_event_name": "PostToolUse",
                "session_id": "s", "cwd": str(project), "tool_name": "write_file",
                "tool_input": {"file_path": "app/a.py", "content": "x = 1\n" * 20}},
               {}, cfg)
    a.to_event({"source": "commandcode", "hook_event_name": "PostToolUse",
                "session_id": "s", "cwd": str(project), "tool_name": "edit_file",
                "tool_input": {"file_path": "app/a.py", "new_value": "y = 2\n" * 20}},
               {}, cfg)

    edits = pending.load("s")["edits"]
    assert [e["action"] for e in edits] == ["created", "edited"]
    assert edits[1]["code"].startswith("y = 2")


def test_matcher_uses_display_names_not_wire_names():
    assert adapters.matcher_for("commandcode") == "write|edit"
    assert "write_file" not in adapters.matcher_for("commandcode")
    assert "edit_file" not in adapters.matcher_for("commandcode")


def test_installed_config_shape(project, home):
    install.install_commandcode_hook(str(project))
    spec = json.loads(install.commandcode_settings_path(str(project)).read_text("utf-8"))

    post = spec["hooks"]["PostToolUse"][0]
    assert post["matcher"] == "write|edit"
    assert "--source commandcode" in json.dumps(post["hooks"][0]["command"])

    stop = spec["hooks"]["Stop"][0]
    # Stop carries no tool, so a matcher there would make the hook never fire.
    assert "matcher" not in stop
    assert "--source commandcode --end" in json.dumps(stop["hooks"][0]["command"])


def test_install_merges_and_uninstall_preserves_other_hooks(project, home):
    target = install.commandcode_settings_path(str(project))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "./welcome.sh"}]}]}}),
        encoding="utf-8")

    install.install_commandcode_hook(str(project))
    spec = json.loads(target.read_text("utf-8"))
    assert "SessionStart" in spec["hooks"]
    assert any("welcome.sh" in json.dumps(e) for e in spec["hooks"]["SessionStart"])

    install.uninstall_commandcode_hook(str(project))
    spec = json.loads(target.read_text("utf-8"))
    assert "PostToolUse" not in spec["hooks"]
    assert "Stop" not in spec["hooks"]
    assert "SessionStart" in spec["hooks"]


def test_detection_via_user_config_dir(home, user_home, project):
    assert "commandcode" not in autosetup.detect_harnesses(str(project))
    (user_home / ".commandcode").mkdir()
    assert "commandcode" in autosetup.detect_harnesses(str(project))


def test_in_chat_followup():
    assert inchat.followup_output("commandcode", "p") == {
        "decision": "block", "reason": "p"}
    assert "commandcode" in inchat.SUPPORTED


def test_capability_declared():
    assert capabilities.supports_in_chat("commandcode")
    assert capabilities.CAPABILITIES["commandcode"].hook == "PostToolUse + Stop"
