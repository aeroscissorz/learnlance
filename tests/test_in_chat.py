"""In-chat mode: the agent analyzes its own work, with no LLM CLI.

The property that matters most is **termination**. An end-of-turn hook that asks
for another turn loops forever if the guard is wrong, and that wedges a real
session — so every refusal path is tested explicitly.
"""
from __future__ import annotations

import json

import pytest

from learnlance import adapters, core, graph, inchat, install, pending
from conftest import capture_payload, end_payload

ANSWER = {
    "did": "Added a delta helper.",
    "topics": [{
        "name": "Delta encoding", "category": "algorithm", "level": "intermediate",
        "explanation": "Store differences between successive values.",
        "why_here": "delta() subtracts each element from the next.",
        "tags": ["compression"], "related": ["Data compression"],
    }],
}


def _answer(home, name="concepts-1.json", data=None):
    (inchat.inbox_dir() / name).write_text(
        json.dumps(ANSWER if data is None else data), encoding="utf-8")


# --------------------------------------------------------------------------- #
# The per-harness channel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source,expected", [
    ("cursor", {"followup_message": "P"}),
    ("copilot", {"decision": "block", "reason": "P"}),
    ("antigravity", {"decision": "block", "reason": "P"}),
    ("gemini", {"decision": "deny", "reason": "P"}),
])
def test_each_harness_gets_its_documented_field(source, expected):
    assert inchat.followup_output(source, "P") == expected


def test_kiro_has_no_command_hook_channel():
    """Kiro's command hooks can't submit a follow-up turn, so its installer writes
    a separate `agent`-action hook instead."""
    assert inchat.followup_output("kiro", "P") is None


def test_unknown_harness_gets_nothing():
    assert inchat.followup_output("claude", "P") is None


# --------------------------------------------------------------------------- #
# Termination
# --------------------------------------------------------------------------- #
def test_the_first_ask_is_allowed(home):
    assert inchat.should_ask({}, {}, "s")[0] is True


def test_a_second_ask_in_the_same_turn_is_refused(home):
    state = {}
    inchat.record_ask(state, "s")
    ask, why = inchat.should_ask({}, state, "s")
    assert ask is False and "not looping" in why


@pytest.mark.parametrize("signal", [
    {"stop_hook_active": True},     # Copilot, Gemini
    {"stopHookActive": True},
    {"loop_count": 1},              # Cursor
    {"loopCount": 3},
])
def test_the_harnesses_own_retry_signal_is_respected(signal, home):
    """Cooperating with each vendor's runaway guard rather than racing it."""
    ask, why = inchat.should_ask(signal, {}, "s")
    assert ask is False and "already a retry" in why


def test_a_nonsense_loop_count_does_not_crash_the_guard(home):
    ask, _ = inchat.should_ask({"loop_count": "not-a-number"}, {}, "s")
    assert ask is True


def test_asking_is_allowed_again_after_an_answer_lands(home):
    state = {}
    inchat.record_ask(state, "s")
    inchat.clear_asks(state, "s")
    assert inchat.should_ask({}, state, "s")[0] is True


# --------------------------------------------------------------------------- #
# The inbox
# --------------------------------------------------------------------------- #
def test_an_empty_inbox_yields_nothing(home):
    assert inchat.ingest() is None


def test_one_answer_is_ingested_and_removed(home):
    _answer(home)
    got = inchat.ingest()
    assert [t["name"] for t in got["topics"]] == ["Delta encoding"]
    assert list(inchat.inbox_dir().glob("*.json")) == []


def test_several_answers_merge(home):
    """The agent may write one file per tool call."""
    _answer(home, "a.json")
    other = {"did": "And tests.",
             "topics": [dict(ANSWER["topics"][0], name="Quantization")]}
    _answer(home, "b.json", other)
    got = inchat.ingest()
    assert sorted(t["name"] for t in got["topics"]) == ["Delta encoding", "Quantization"]
    assert "Added a delta helper." in got["did"]


@pytest.mark.parametrize("body", [
    "not json at all",
    '{"topics": "not a list"}',
    '{"topics": [{"no_name": true}]}',
    "[]",
])
def test_malformed_answers_are_discarded_not_retried(body, home):
    """Leaving a broken file in place would make every later turn retry it forever.
    The discard is explicit and logged rather than silent."""
    from learnlance import config

    (inchat.inbox_dir() / "bad.json").write_text(body, encoding="utf-8")
    assert inchat.ingest() is None
    assert list(inchat.inbox_dir().glob("*.json")) == []
    assert "inchat" in config.LOG_PATH.read_text(encoding="utf-8", errors="replace")


def test_a_good_answer_survives_a_bad_neighbour(home):
    _answer(home, "good.json")
    (inchat.inbox_dir() / "bad.json").write_text("{{{", encoding="utf-8")
    got = inchat.ingest()
    assert got is not None and len(got["topics"]) == 1


# --------------------------------------------------------------------------- #
# Kiro's static prompt and its marker
# --------------------------------------------------------------------------- #
def test_the_request_marker_round_trips(home):
    assert inchat.request_open() is False
    inchat.open_request(["app/delta.py"])
    assert inchat.request_open() is True
    inchat.close_request()
    assert inchat.request_open() is False


def test_the_static_prompt_is_conditional_on_the_marker(home, cfg):
    """Kiro's `agent` action carries a fixed string, so it would re-fire on the very
    turn it produced. Gating it on a marker only the command hook creates turns
    that into a deterministic check."""
    prompt = inchat.static_prompt(cfg)
    assert str(inchat.request_path()) in prompt
    assert "If it does NOT exist, do nothing" in prompt
    assert "delete" in prompt.lower()


def test_the_prompt_names_the_inbox_and_the_schema(home, cfg):
    prompt = inchat.build_prompt(cfg)
    assert str(inchat.inbox_dir()) in prompt
    for key in ("did", "topics", "category", "why_here", "tags", "related"):
        assert key in prompt


def test_the_prompt_respects_the_topic_limit(home, cfg):
    cfg["max_topics_per_turn"] = 2
    assert "At most 2 topics" in inchat.build_prompt(cfg)


# --------------------------------------------------------------------------- #
# A whole turn, per harness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source", ["cursor", "copilot", "gemini", "antigravity"])
def test_a_full_in_chat_turn_reaches_the_graph(source, cfg, project, home):
    session = f"{source}-ic"
    p = capture_payload(source, session, str(project))
    adapters.detect(p).to_event(p, {}, cfg)

    # Turn A: nothing answered yet, so the hook asks.
    end = end_payload(source, session, str(project))
    event = adapters.detect(end).to_event(end, {}, cfg)
    state = {}
    ask, _ = inchat.should_ask(end, state, session)
    assert ask and inchat.followup_output(source, "P") is not None
    inchat.record_ask(state, session)

    # The agent complies.
    _answer(home, f"{source}.json")

    # Turn B: the answer is found and merged. announce=False because stdout is the
    # harness's JSON channel in this mode.
    result = inchat.ingest()
    assert core.merge_result(cfg, event, result, announce=False) is True

    g = graph.load_project(str(project))
    assert "Delta encoding" in [n["name"] for n in g["nodes"].values()
                                if not n.get("placeholder")]
    assert pending.load(session)["edits"] == []


def test_merge_result_never_writes_to_stdout_when_quiet(cfg, project, home, capsys):
    """In-chat mode parses stdout as JSON, so anything else there corrupts it."""
    from learnlance.events import CodeEvent

    event = CodeEvent("cursor", "after_agent_turn", cwd=str(project), session="q")
    core.merge_result(cfg, event, ANSWER, announce=False)
    assert capsys.readouterr().out == ""


def test_merge_result_survives_a_console_that_cannot_encode_emoji(cfg, project, home,
                                                                 monkeypatch):
    """The hook path doesn't go through cli.main(), so it never gets the UTF-8
    reconfigure — an encoding error here used to abort after the graph was already
    updated, leaving the ask counter and marker dirty."""
    from learnlance.events import CodeEvent

    def exploding_print(*a, **k):
        raise UnicodeEncodeError("charmap", "x", 0, 1, "nope")

    monkeypatch.setattr("builtins.print", exploding_print)
    event = CodeEvent("kiro", "after_agent_turn", cwd=str(project), session="e")
    assert core.merge_result(cfg, event, ANSWER, announce=True) is True


# --------------------------------------------------------------------------- #
# Installed configuration
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,installer,path_of", [
    ("kiro", "install_kiro_hook",
     lambda p: install._kiro_hooks_dir(p) / install.KIRO_HOOK_FILENAME),
    ("cursor", "install_cursor_hook", lambda p: install.cursor_hooks_path(p)),
    ("copilot", "install_copilot_hook", lambda p: install.copilot_hooks_path(p)),
    ("gemini", "install_gemini_hook", lambda p: install.gemini_settings_path(p)),
    ("antigravity", "install_antigravity_hook",
     lambda p: install.antigravity_hooks_path(p)),
])
def test_in_chat_is_recorded_on_the_end_of_turn_hook_only(name, installer, path_of,
                                                          project, home):
    getattr(install, installer)(str(project), True)
    body = path_of(str(project)).read_text(encoding="utf-8")
    assert "--in-chat" in body
    # Capture is identical either way, so only one hook should carry the flag.
    assert body.count("--in-chat") == 1


def test_kiro_gets_an_agent_action_hook_only_in_chat_mode(project, home):
    path = install._kiro_hooks_dir(str(project)) / install.KIRO_HOOK_FILENAME

    install.install_kiro_hook(str(project), False)
    kinds = [h["action"]["type"] for h in json.loads(path.read_text("utf-8"))["hooks"]]
    assert kinds == ["command", "command"]

    install.install_kiro_hook(str(project), True)
    kinds = [h["action"]["type"] for h in json.loads(path.read_text("utf-8"))["hooks"]]
    assert kinds == ["command", "command", "agent"]


def test_cursor_sets_its_own_loop_limit_as_a_second_defence(project, home):
    install.install_cursor_hook(str(project), True)
    spec = json.loads(install.cursor_hooks_path(str(project)).read_text("utf-8"))
    ours = [e for e in spec["hooks"]["stop"] if "learnlance" in json.dumps(e)]
    assert ours[0]["loop_limit"] == 1
