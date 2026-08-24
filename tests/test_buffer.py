"""The pending-edit buffer, and the rule that a turn's work is never lost.

The regression that matters here: the buffer used to be read-and-deleted in one
step, so a turn whose analysis couldn't run — no LLM installed, CLI erroring — was
destroyed with nothing to retry from.
"""
from __future__ import annotations

import json

import pytest

from learnlance import adapters, config, core, graph, insights, pending
from conftest import capture_payload, end_payload


def _edit(path="a.py", code="x" * 100):
    return {"file": path, "code": code, "action": "created"}


# --------------------------------------------------------------------------- #
# Storage basics
# --------------------------------------------------------------------------- #
def test_missing_buffer_reads_as_empty(home):
    assert pending.load("nobody") == {"cwd": "", "edits": [], "prompt": ""}


def test_add_then_load(home):
    assert pending.add_edit("s", _edit(), cwd="/p", prompt="do it") == 1
    got = pending.load("s")
    assert got["cwd"] == "/p" and got["prompt"] == "do it"
    assert got["edits"] == [_edit()]


def test_clear_is_idempotent(home):
    pending.add_edit("s", _edit())
    pending.clear("s")
    pending.clear("s")  # must not raise
    assert pending.load("s")["edits"] == []


def test_buffer_is_capped(home):
    for i in range(pending._MAX_EDITS + 25):
        pending.add_edit("big", _edit(path=f"f{i}.py"))
    edits = pending.load("big")["edits"]
    assert len(edits) == pending._MAX_EDITS
    # The cap keeps the most recent edits, not the oldest.
    assert edits[-1]["file"] == f"f{pending._MAX_EDITS + 24}.py"


def test_session_ids_are_made_filename_safe(home):
    weird = "git:C:\\Users\\me/proj?*"
    pending.add_edit(weird, _edit())
    assert pending.load(weird)["edits"] == [_edit()]


def test_corrupt_buffer_degrades_to_empty_rather_than_raising(home):
    pending.add_edit("bad", _edit())
    pending.path_for("bad").write_text("{not json", encoding="utf-8")
    assert pending.load("bad") == {"cwd": "", "edits": [], "prompt": ""}


def test_buffer_survives_across_separate_hook_invocations(home):
    """Each hook call is a fresh process; nothing may be held in memory."""
    pending.add_edit("s", _edit("a.py"))
    pending.add_edit("s", _edit("b.py"))
    assert [e["file"] for e in pending.load("s")["edits"]] == ["a.py", "b.py"]


# --------------------------------------------------------------------------- #
# Read-then-commit: work is only discarded once it's in the graph
# --------------------------------------------------------------------------- #
def test_end_of_turn_reads_without_deleting(cfg, project, home):
    p = capture_payload("kiro", "s", str(project))
    adapters.detect(p).to_event(p, {}, cfg)

    end = end_payload("kiro", "s", str(project))
    adapters.detect(end).to_event(end, {}, cfg)

    assert len(pending.load("s")["edits"]) == 1, (
        "the adapter must not consume the buffer; core clears it after the "
        "concepts land, so a failed analysis can be retried")


def test_no_backend_keeps_the_work_and_leaves_the_graph_empty(cfg, project, home):
    cfg["llm_cmd"] = cfg["claude_bin"] = ""
    assert insights.resolve_backend(cfg) is None

    p = capture_payload("kiro", "s", str(project))
    adapters.detect(p).to_event(p, {}, cfg)
    end = end_payload("kiro", "s", str(project))
    core.process_event(cfg, adapters.detect(end).to_event(end, {}, cfg))

    assert len(pending.load("s")["edits"]) == 1
    g = graph.load_project(str(project))
    assert [n for n in g["nodes"].values() if not n.get("placeholder")] == []


def test_a_backend_arriving_later_analyzes_the_kept_work(cfg, project, home, fake_llm):
    p = capture_payload("kiro", "s", str(project))
    adapters.detect(p).to_event(p, {}, cfg)

    # First turn: nothing available, work held.
    cfg["llm_cmd"] = ""
    end = end_payload("kiro", "s", str(project))
    core.process_event(cfg, adapters.detect(end).to_event(end, {}, cfg))
    assert len(pending.load("s")["edits"]) == 1

    # Backend installed; the held work is analyzed and only then forgotten.
    cfg["llm_cmd"] = fake_llm
    core.process_event(cfg, adapters.detect(end).to_event(end, {}, cfg))

    g = graph.load_project(str(project))
    names = [n["name"] for n in g["nodes"].values() if not n.get("placeholder")]
    assert "Delta encoding" in names
    assert pending.load("s")["edits"] == []


def test_a_failing_cli_keeps_the_work(cfg, project, home):
    import sys

    p = capture_payload("kiro", "s", str(project))
    adapters.detect(p).to_event(p, {}, cfg)
    cfg["llm_cmd"] = f'"{sys.executable}" -c "import sys; sys.exit(3)"'

    end = end_payload("kiro", "s", str(project))
    core.process_event(cfg, adapters.detect(end).to_event(end, {}, cfg))

    assert len(pending.load("s")["edits"]) == 1
    assert "exited 3" in config.LOG_PATH.read_text(encoding="utf-8", errors="replace")


def test_nothing_learnable_clears_the_buffer(cfg, project, home, monkeypatch):
    """An empty topic list is a real answer, not a failure — keeping the buffer
    would re-analyze the same code every turn forever."""
    monkeypatch.setattr(insights, "resolve_backend", lambda c: ["fake"])
    monkeypatch.setattr(insights, "generate", lambda c, blob: {"did": "", "topics": []})

    p = capture_payload("kiro", "s", str(project))
    adapters.detect(p).to_event(p, {}, cfg)
    end = end_payload("kiro", "s", str(project))
    core.process_event(cfg, adapters.detect(end).to_event(end, {}, cfg))

    assert pending.load("s")["edits"] == []


def test_a_second_end_of_turn_is_a_no_op(cfg, project, home, fake_llm):
    cfg["llm_cmd"] = fake_llm
    p = capture_payload("kiro", "s", str(project))
    adapters.detect(p).to_event(p, {}, cfg)
    end = end_payload("kiro", "s", str(project))

    core.process_event(cfg, adapters.detect(end).to_event(end, {}, cfg))
    before = len(graph.load_project(str(project))["edges"])

    event = adapters.detect(end).to_event(end, {}, cfg)
    assert event.skip_reason == "no code edits captured this session"
    core.process_event(cfg, event)
    assert len(graph.load_project(str(project))["edges"]) == before


def test_sessions_do_not_share_a_buffer(cfg, project, home):
    for session, path in (("s1", "one.py"), ("s2", "two.py")):
        p = capture_payload("kiro", session, str(project), path=path)
        adapters.detect(p).to_event(p, {}, cfg)
    assert [e["file"] for e in pending.load("s1")["edits"]] == ["one.py"]
    assert [e["file"] for e in pending.load("s2")["edits"]] == ["two.py"]
