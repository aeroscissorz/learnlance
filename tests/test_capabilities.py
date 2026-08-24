"""The capability table, and honesty about what's been verified.

Also guards test isolation itself: the suite once rewrote the developer's real
`~/.gemini/settings.json`, because Gemini's installer targets user-level config via
`Path.home()` rather than the patched `config.HOME`.
"""
from __future__ import annotations

import json

import pytest

from learnlance import autosetup, capabilities, inchat, install
from learnlance.capabilities import Confidence


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
def test_the_home_fixture_redirects_path_home(home, user_home):
    from pathlib import Path

    assert Path.home() == user_home
    assert str(user_home) not in str(Path.home().parent.parent.parent.parent)


def test_user_level_installs_land_in_the_fake_home(home, user_home, project):
    """Gemini with no project-local config writes to `~`. That must be the fake one."""
    target = install.gemini_settings_path(None)
    assert str(target).startswith(str(user_home)), (
        "a user-level install escaped the test sandbox")

    install.install_gemini_hook(None)
    assert target.exists()
    assert "learnlance" in target.read_text(encoding="utf-8")


def test_autosetup_on_an_arbitrary_project_stays_in_the_sandbox(home, user_home,
                                                                tmp_path):
    """The specific leak: detection finds a harness via $HOME, then installs to a
    user-level path outside the project."""
    (user_home / ".gemini").mkdir(parents=True, exist_ok=True)
    proj = tmp_path / "someproject"
    proj.mkdir()

    autosetup.run(str(proj), force=True)

    written = install.gemini_settings_path(None)
    assert str(written).startswith(str(user_home))


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #
def test_every_adapter_with_an_installer_declares_capabilities():
    for name in autosetup.HARNESSES:
        assert name in capabilities.CAPABILITIES, f"{name} has no declared capability"


def test_claude_is_declared_too():
    """It has no installer flag (its hook is user-level) but still needs a row."""
    assert "claude" in capabilities.CAPABILITIES


def test_in_chat_support_matches_the_declared_capability():
    """`inchat.SUPPORTED` and the table must agree, or doctor lies one way or the
    other about whether a harness can be asked."""
    declared = {n for n in capabilities.CAPABILITIES
                if capabilities.supports_in_chat(n)}
    assert declared == set(inchat.SUPPORTED)


def test_harnesses_without_an_agent_do_not_claim_in_chat():
    for name in ("git", "claude"):
        cap = capabilities.CAPABILITIES[name]
        assert cap.in_chat == ""
        assert cap.in_chat_confidence is Confidence.UNSUPPORTED
        assert not capabilities.supports_in_chat(name)


def test_every_in_chat_harness_names_its_mechanism():
    """A capability with no stated mechanism can't be reviewed against the docs."""
    for name in inchat.SUPPORTED:
        cap = capabilities.CAPABILITIES[name]
        assert cap.in_chat and len(cap.in_chat) > 10, name


def test_nothing_claims_to_be_verified():
    """Every integration is doc-derived. If a capability is ever marked verified it
    should be because a real harness proved it, not because someone felt confident."""
    assert not any(
        c.hook_confidence not in (Confidence.DOCUMENTED, Confidence.DISPUTED,
                                  Confidence.UNSUPPORTED)
        for c in capabilities.CAPABILITIES.values())


def test_antigravity_is_flagged_as_disputed():
    """There are open reports its hooks don't fire in the IDE."""
    cap = capabilities.CAPABILITIES["antigravity"]
    assert cap.hook_confidence is Confidence.DISPUTED
    assert "may not fire" in cap.note or "never fire" in cap.note


def test_gemini_deprecation_is_recorded():
    assert "Antigravity" in capabilities.CAPABILITIES["gemini"].note


# --------------------------------------------------------------------------- #
# Evidence from the log
# --------------------------------------------------------------------------- #
def test_no_log_means_no_evidence(home):
    ev = capabilities.evidence("kiro")
    assert (ev.fired, ev.learned, ev.asked, ev.answered) == (False, False, False, False)


def test_a_hook_invocation_counts_as_fired():
    log = "[2026-08-23T10:00:00] kiro:abc12345: buffered 1 edit(s), 1 pending\n"
    assert capabilities.evidence("kiro", log).fired is True
    assert capabilities.evidence("cursor", log).fired is False


def test_a_completed_analysis_counts_as_learned():
    log = ("[t] cursor:sess1234: learnlance: did a thing - learned/reinforced: "
           "Delta encoding\n")
    ev = capabilities.evidence("cursor", log)
    assert ev.fired and ev.learned


def test_in_chat_needs_both_an_ask_and_an_answer():
    asked = "[t] gemini:s1: asking the agent to analyze 2 file(s)\n"
    ev = capabilities.evidence("gemini", asked)
    assert ev.asked and not ev.answered and not ev.in_chat_works

    full = asked + "[t] gemini:s1: ingested 3 concept(s) from the agent\n"
    assert capabilities.evidence("gemini", full).in_chat_works is True


def test_kiros_marker_route_also_counts_as_asking():
    log = "[t] kiro:s1: opened an analyze request for the agent-action hook (2 file(s))\n"
    assert capabilities.evidence("kiro", log).asked is True


def test_one_harness_does_not_borrow_anothers_evidence():
    log = "[t] kiro:s1: learnlance: x - learned/reinforced: Y\n"
    assert capabilities.evidence("kiro", log).learned is True
    for other in ("cursor", "copilot", "gemini", "antigravity"):
        assert capabilities.evidence(other, log).fired is False


# --------------------------------------------------------------------------- #
# Status wording
# --------------------------------------------------------------------------- #
def test_unconfigured_is_reported_plainly():
    assert capabilities.status("kiro", False, False, "") == "not configured"


def test_configured_but_never_run_is_not_claimed_as_working():
    got = capabilities.status("cursor", True, False, "")
    assert got == "configured, unverified"
    assert "working" not in got


def test_a_disputed_harness_says_so_until_proven():
    got = capabilities.status("antigravity", True, False, "")
    assert "unverified" in got and "may not fire" in got


def test_a_fired_hook_is_distinguished_from_a_completed_analysis():
    log = "[t] kiro:s1: buffered 1 edit(s), 1 pending\n"
    assert capabilities.status("kiro", True, False, log) == \
        "hook fired, nothing learned yet"


def test_evidence_upgrades_the_status():
    log = "[t] kiro:s1: learnlance: x - learned/reinforced: Delta encoding\n"
    assert capabilities.status("kiro", True, False, log) == "working (verified)"


def test_in_chat_status_reflects_the_ask_answer_cycle():
    asked = "[t] cursor:s1: asking the agent to analyze 1 file(s)\n"
    assert capabilities.status("cursor", True, True, asked) == "asked, no answer yet"

    answered = asked + "[t] cursor:s1: ingested 1 concept(s) from the agent\n"
    assert capabilities.status("cursor", True, True, answered) == \
        "working (in-chat verified)"


def test_doctor_never_prints_a_bare_verified_claim_without_evidence(capsys, home,
                                                                   project,
                                                                   monkeypatch):
    from learnlance import cli

    (project / ".kiro").mkdir()
    install.install_kiro_hook(str(project))
    monkeypatch.chdir(project)

    args = cli.build_parser().parse_args(["doctor"])
    args.func(args)
    out = capsys.readouterr().out
    assert "configured, unverified" in out
    assert "working (verified)" not in out
