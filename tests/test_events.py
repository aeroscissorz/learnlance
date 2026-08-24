"""The CodeEvent contract.

This is the boundary the whole design rests on: adapters produce it, the core
consumes it, and neither knows anything else about the other.
"""
from dataclasses import asdict, fields

from learnlance.events import CodeEvent


def test_only_source_and_event_are_required():
    e = CodeEvent("kiro", "after_agent_turn")
    assert (e.cwd, e.session, e.files, e.blob, e.user_prompt, e.skip_reason) == (
        "", "unknown", [], "", "", None)


def test_mutable_defaults_are_not_shared():
    """A plain `= []` default would make every event share one list."""
    a, b = CodeEvent("kiro", "x"), CodeEvent("kiro", "x")
    a.files.append("only-mine.py")
    assert b.files == []


def test_round_trips_through_json_for_the_worker():
    """hook._spawn_worker serialises an event to a file for a detached process,
    so every field has to survive asdict -> json -> CodeEvent."""
    import json

    original = CodeEvent("cursor", "after_agent_turn", cwd="/p",
                         session="s1", files=["a.py"], blob="code",
                         user_prompt="do it", skip_reason=None)
    restored = CodeEvent(**json.loads(json.dumps(asdict(original))))
    assert restored == original


def test_field_set_is_stable():
    """Adding a field is fine; renaming one silently breaks worker round-trips and
    every adapter at once, so it should require updating this test."""
    assert {f.name for f in fields(CodeEvent)} == {
        "source", "event", "cwd", "session", "files", "blob",
        "user_prompt", "skip_reason",
    }
