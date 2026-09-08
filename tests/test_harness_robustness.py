"""Regression tests for cross-harness reliability defects.

Each reproduced a real way learnlance failed to learn from an agent's work:
  * a tool payload with no `hook_event_name` was treated as end-of-turn, so
    VS Code Copilot (which fires for every tool call) drained the buffer and
    paid for an LLM call mid-turn;
  * Codex `Edit`/`Write` edits went through the apply-patch parser and vanished;
  * concurrent edits clobbered each other in a read-modify-write buffer;
  * the in-chat inbox path contained `<n>`, which is illegal on Windows;
  * `add "delta encoding"` matched any line containing "encoding".
"""
from __future__ import annotations

import json
import threading

import pytest

from conftest import capture_payload, end_payload

from learnlance import adapters, codesearch, config, inchat, pending


# --------------------------------------------------------------------------- #
# turn-phase detection
# --------------------------------------------------------------------------- #
def _adapter(source: str):
    return adapters.detect({"source": source})


@pytest.mark.parametrize("source", ["copilot", "cursor", "kiro", "gemini",
                                    "antigravity", "codex"])
def test_tool_payload_without_event_name_is_not_end_of_turn(source):
    """The VS Code Copilot case: matchers ignored, so we see every tool call."""
    a = _adapter(source)
    payload = {"source": source, "session_id": "s", "tool_name": "some_tool"}
    assert a.is_end_of_turn(payload, "") is False


@pytest.mark.parametrize("source", ["copilot", "cursor", "kiro"])
def test_explicit_end_flag_forces_a_drain(source):
    a = _adapter(source)
    payload = {"source": source, "session_id": "s", "tool_name": "some_tool",
               adapters.PHASE_KEY: "end"}
    assert a.is_end_of_turn(payload, "") is True


@pytest.mark.parametrize("source", ["copilot", "cursor", "kiro"])
def test_payload_with_neither_event_nor_tool_still_drains(source):
    """Back-compat: a hook installed before --end existed, on a harness that
    omits the event name, must still end the turn."""
    a = _adapter(source)
    assert a.is_end_of_turn({"source": source, "session_id": "s"}, "") is True


def test_end_flag_drains_a_payload_that_also_names_a_tool(home, project, cfg):
    """The combination that used to be unreachable: an end-of-turn hook whose
    payload still carries a tool name (and so looked like a capture call)."""
    session = "endflag"
    pending.add_edit(session, {"file": "a.py", "code": "x = 1\n" * 20,
                               "action": "created"}, cwd=str(project))

    payload = {"source": "kiro", "session_id": session, "cwd": str(project),
               "tool_name": "fs_write", adapters.PHASE_KEY: "end"}
    event = _adapter("kiro").to_event(payload, {}, cfg)

    assert event.blob, f"--end did not drain the buffer ({event.skip_reason})"
    assert "a.py" in event.files


# --------------------------------------------------------------------------- #
# Codex: plain Edit/Write, not just apply_patch
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool,field", [
    ("Edit", "new_string"),
    ("Write", "content"),
])
def test_codex_plain_edits_are_captured(tool, field):
    a = _adapter("codex")
    payload = {"source": "codex", "hook_event_name": "PostToolUse",
               "tool_name": tool,
               "tool_input": {"file_path": "svc/retry.py",
                              field: "def retry(f):\n    return f()\n"}}
    edits = a.extract_edits(payload, "posttooluse")
    assert len(edits) == 1, "Codex Edit/Write was dropped by the patch parser"
    assert edits[0]["file"] == "svc/retry.py"
    assert "retry" in edits[0]["code"]


def test_codex_apply_patch_still_wins():
    a = _adapter("codex")
    payload = capture_payload("codex", "s", ".")
    edits = a.extract_edits(payload, "posttooluse")
    assert edits and edits[0]["file"] == "app/delta.py"


def test_codex_matcher_is_derived_from_the_adapter():
    """A hardcoded matcher can drift from the tools we can actually read."""
    from learnlance import install
    matcher = install._matcher_for("codex")
    for tool in _adapter("codex").write_tools:
        assert tool in matcher


# --------------------------------------------------------------------------- #
# concurrency: agents edit in parallel
# --------------------------------------------------------------------------- #
def test_concurrent_edits_are_not_lost(home):
    session = "parallel"
    n = 40

    def add(i):
        pending.add_edit(session, {"file": f"f{i}.py", "code": f"x={i}\n",
                                   "action": "created"}, cwd="/p")

    threads = [threading.Thread(target=add, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    edits = pending.load(session)["edits"]
    assert len(edits) == n, f"lost {n - len(edits)} of {n} concurrent edits"
    assert len({e["file"] for e in edits}) == n


def test_a_torn_record_costs_one_edit_not_the_turn(home):
    session = "torn"
    pending.add_edit(session, {"file": "a.py", "code": "a\n", "action": "created"})
    pending.add_edit(session, {"file": "b.py", "code": "b\n", "action": "created"})
    # A record whose write was interrupted part-way through.
    (pending.path_for(session) / "99999999999999999999-x-torn.json").write_text(
        '{"edit": {"file": "c.py", "cod', encoding="utf-8")

    edits = pending.load(session)["edits"]
    assert [e["file"] for e in edits] == ["a.py", "b.py"]


def test_legacy_json_buffer_is_still_drained(home):
    """A buffer written by the previous version must not be orphaned."""
    session = "legacy"
    p = pending._legacy_path(session)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"cwd": "/old", "prompt": "do it", "edits": [
        {"file": "old.py", "code": "old\n", "action": "created"}]}),
        encoding="utf-8")

    data = pending.load(session)
    assert data["edits"][0]["file"] == "old.py"
    assert data["cwd"] == "/old"

    pending.clear(session)
    assert not p.exists(), "legacy buffer survived clear() and would replay"


# --------------------------------------------------------------------------- #
# in-chat: the inbox path must be writable on Windows
# --------------------------------------------------------------------------- #
def test_inbox_path_has_no_characters_illegal_on_windows(home, cfg):
    prompt = inchat.build_prompt(cfg)
    assert inchat.INBOX_FILENAME in prompt
    for bad in '<>"|?*':
        assert bad not in inchat.INBOX_FILENAME, f"{bad!r} is reserved on NTFS"


def test_the_advertised_inbox_file_is_actually_writable(home, cfg):
    target = inchat.inbox_dir() / inchat.INBOX_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"did": "d", "topics": [
        {"name": "Debouncing", "category": "pattern", "level": "beginner",
         "explanation": "e", "why_here": "w", "tags": ["ui"], "related": ["Rate limiting"]}]}),
        encoding="utf-8")
    result = inchat.ingest()
    assert result and result["topics"][0]["name"] == "Debouncing"


# --------------------------------------------------------------------------- #
# codesearch precision + safety
# --------------------------------------------------------------------------- #
def test_multiword_topic_requires_all_terms(tmp_path):
    (tmp_path / "a.py").write_text(
        "# uses delta encoding to shrink payloads\n"
        "def delta_encode(xs): pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "\n".join(f"encoding = 'utf-8'  # line {i}" for i in range(200)),
        encoding="utf-8")

    blob, files = codesearch.gather("delta encoding", tmp_path)

    assert "a.py" in " ".join(files)
    assert "b.py" not in " ".join(files), "matched any term instead of all"


def test_symlink_cycle_does_not_hang(tmp_path):
    (tmp_path / "x.py").write_text("delta encoding here\n", encoding="utf-8")
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable (needs privilege on Windows)")

    blob, files = codesearch.gather("delta encoding", tmp_path)
    assert "x.py" in " ".join(files)
