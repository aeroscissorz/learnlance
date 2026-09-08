"""`learnlance setup`, detection, and the installers' merge behaviour.

Two regressions locked down here:
  * `setup --in-chat` silently installed *without* in-chat mode, because the
    HARNESSES table wrapped installers in single-argument lambdas and the
    resulting TypeError was swallowed.
  * Shared config files (`.cursor/hooks.json`, Gemini's `settings.json`) must not
    lose other tools' entries.
"""
from __future__ import annotations

import inspect
import json

import pytest

from learnlance import autosetup, config, inchat, install


@pytest.fixture
def kiro_project(project):
    (project / ".kiro").mkdir()
    return project


# --------------------------------------------------------------------------- #
# The table itself
# --------------------------------------------------------------------------- #
def test_every_installer_accepts_in_chat():
    """The bug: single-argument lambdas made `in_chat` unpassable, and the
    TypeError was caught and hidden."""
    for name, spec in autosetup.HARNESSES.items():
        sig = inspect.signature(spec["install"])
        assert len(sig.parameters) == 2, (
            f"{name}'s installer must take (cwd, in_chat) so callers never have to "
            f"know which harnesses support which mode")


def test_every_harness_declares_the_full_spec():
    for name, spec in autosetup.HARNESSES.items():
        assert set(spec) == {"label", "detect", "config", "install"}, name


def test_in_chat_supported_harnesses_are_all_known():
    assert set(inchat.SUPPORTED) <= set(autosetup.HARNESSES)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def test_a_project_config_dir_is_detected(project, home, monkeypatch):
    monkeypatch.setattr(autosetup.Path, "home", staticmethod(lambda: project / "nohome"))
    assert "kiro" not in autosetup.detect_harnesses(str(project))
    (project / ".kiro").mkdir()
    assert "kiro" in autosetup.detect_harnesses(str(project))


def test_hook_present_is_false_before_install(kiro_project, home):
    assert autosetup.hook_present("kiro", str(kiro_project)) is False


def test_hook_present_and_in_chat_reflect_the_installed_file(kiro_project, home):
    cwd = str(kiro_project)
    install.install_kiro_hook(cwd, False)
    assert autosetup.hook_present("kiro", cwd) is True
    assert autosetup.hook_in_chat("kiro", cwd) is False

    install.install_kiro_hook(cwd, True)
    assert autosetup.hook_in_chat("kiro", cwd) is True


def test_an_unknown_harness_name_is_not_reported_as_present(project, home):
    assert autosetup.hook_present("nope", str(project)) is False
    assert autosetup.hook_in_chat("nope", str(project)) is False


# --------------------------------------------------------------------------- #
# setup is re-runnable and per-project
# --------------------------------------------------------------------------- #
def test_setup_works_even_after_the_once_ever_gate_has_tripped(kiro_project, home):
    autosetup.mark_done()
    assert autosetup.needs_setup() is False

    actions = autosetup.run(str(kiro_project), force=True)
    assert "Kiro" in actions
    assert autosetup.hook_present("kiro", str(kiro_project)) is True


def test_implicit_run_configures_new_projects_even_after_first_run(kiro_project, home):
    autosetup.mark_done()
    actions = autosetup.run(str(kiro_project))
    assert "Kiro" in actions
    assert autosetup.hook_present("kiro", str(kiro_project)) is True


def test_setup_enables_and_then_disables_in_chat(kiro_project, home):
    cwd = str(kiro_project)
    autosetup.run(cwd, force=True, in_chat=True)
    assert autosetup.hook_in_chat("kiro", cwd) is True

    autosetup.run(cwd, force=True, in_chat=False)
    assert autosetup.hook_in_chat("kiro", cwd) is False, (
        "re-running without the flag must revert the mode, not leave it stale")


def test_auto_setup_reconciles_an_existing_hook_to_in_chat(kiro_project, home):
    cwd = str(kiro_project)
    autosetup.run(cwd, force=True, in_chat=False)
    assert autosetup.hook_in_chat("kiro", cwd) is False
    autosetup.run(cwd, in_chat=True)
    assert autosetup.hook_in_chat("kiro", cwd) is True


def test_each_project_gets_its_own_hook_files(tmp_path, home):
    a, b = tmp_path / "a", tmp_path / "b"
    for p in (a, b):
        (p / ".kiro").mkdir(parents=True)
        autosetup.run(str(p), force=True)

    fa = install._kiro_hooks_dir(str(a)) / install.KIRO_HOOK_FILENAME
    fb = install._kiro_hooks_dir(str(b)) / install.KIRO_HOOK_FILENAME
    assert fa != fb and fa.exists() and fb.exists()
    assert str(fa).startswith(str(a)) and str(fb).startswith(str(b))


def test_re_running_setup_does_not_duplicate_entries(project, home):
    (project / ".cursor").mkdir()
    for _ in range(3):
        autosetup.run(str(project), force=True)
    spec = json.loads(install.cursor_hooks_path(str(project)).read_text("utf-8"))
    ours = [e for e in spec["hooks"]["afterFileEdit"] if "learnlance" in json.dumps(e)]
    assert len(ours) == 1


def test_setup_marks_itself_done_so_the_implicit_run_stops(kiro_project, home):
    autosetup.run(str(kiro_project), force=True)
    assert autosetup.needs_setup() is False


def test_one_failing_harness_does_not_abort_the_rest(kiro_project, home, monkeypatch):
    (kiro_project / ".cursor").mkdir()

    def boom(cwd, in_chat=False):
        raise RuntimeError("simulated failure")

    monkeypatch.setitem(autosetup.HARNESSES["kiro"], "install", boom)
    actions = autosetup.run(str(kiro_project), force=True)
    assert "Cursor" in actions and "Kiro" not in actions
    assert "simulated failure" in config.LOG_PATH.read_text("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Shared config files must be merged, not overwritten
# --------------------------------------------------------------------------- #
def test_cursor_install_preserves_other_tools_hooks(project, home):
    path = install.cursor_hooks_path(str(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "hooks": {"afterFileEdit": [{"command": "./other-tool.sh"}],
                  "beforeReadFile": [{"command": "./secrets-guard.sh"}]},
    }), encoding="utf-8")

    install.install_cursor_hook(str(project))
    spec = json.loads(path.read_text("utf-8"))
    assert any("other-tool.sh" in json.dumps(e) for e in spec["hooks"]["afterFileEdit"])
    assert "beforeReadFile" in spec["hooks"]

    install.uninstall_cursor_hook(str(project))
    spec = json.loads(path.read_text("utf-8"))
    assert any("other-tool.sh" in json.dumps(e) for e in spec["hooks"]["afterFileEdit"])
    assert not any("learnlance" in json.dumps(e)
                   for e in spec["hooks"].get("afterFileEdit", []))


def test_gemini_install_preserves_unrelated_settings(project, home):
    path = install.gemini_settings_path(str(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "theme": "dark",
        "hooks": {"AfterTool": [{"matcher": "read_.*",
                                 "hooks": [{"type": "command", "command": "./audit.sh"}]}]},
    }), encoding="utf-8")

    install.install_gemini_hook(str(project))
    spec = json.loads(path.read_text("utf-8"))
    assert spec["theme"] == "dark"
    assert any("audit.sh" in json.dumps(d) for d in spec["hooks"]["AfterTool"])

    ours = [d for d in spec["hooks"]["AfterTool"] if "learnlance" in json.dumps(d)]
    assert len(ours) == 1
    # Gemini nests twice and counts its timeout in milliseconds.
    assert isinstance(ours[0]["hooks"], list)
    assert ours[0]["hooks"][0]["timeout"] == 60000

    install.uninstall_gemini_hook(str(project))
    spec = json.loads(path.read_text("utf-8"))
    assert spec["theme"] == "dark"
    assert any("audit.sh" in json.dumps(d) for d in spec["hooks"]["AfterTool"])


def test_codex_install_and_uninstall_preserve_other_hooks(project, home):
    path = install.codex_hooks_path(str(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "description": "project policy",
        "hooks": {"PreToolUse": [{"matcher": "Bash",
                                    "hooks": [{"type": "command",
                                               "command": "./guard.py"}]}]},
    }), encoding="utf-8")

    install.install_codex_hook(str(project), in_chat=True)
    spec = json.loads(path.read_text("utf-8"))
    assert spec["description"] == "project policy"
    assert "guard.py" in json.dumps(spec["hooks"]["PreToolUse"])
    assert len(spec["hooks"]["PostToolUse"]) == 1
    assert len(spec["hooks"]["Stop"]) == 1
    # --end marks the end-of-turn hook so the phase is never inferred.
    assert "--source codex --end --in-chat" in json.dumps(spec["hooks"]["Stop"])

    install.uninstall_codex_hook(str(project))
    spec = json.loads(path.read_text("utf-8"))
    assert "PreToolUse" in spec["hooks"]
    assert "PostToolUse" not in spec["hooks"]
    assert "Stop" not in spec["hooks"]

def test_cursor_timeout_is_in_seconds(project, home):
    install.install_cursor_hook(str(project))
    spec = json.loads(install.cursor_hooks_path(str(project)).read_text("utf-8"))
    assert spec["hooks"]["afterFileEdit"][0]["timeout"] == 30


def test_antigravity_block_is_enabled(project, home):
    """Antigravity's `enabled` flag defaults to false, so a correctly shaped config
    sits inert unless the installer sets it."""
    install.install_antigravity_hook(str(project))
    spec = json.loads(install.antigravity_hooks_path(str(project)).read_text("utf-8"))
    assert spec[install.ANTIGRAVITY_BLOCK]["enabled"] is True


def test_a_foreign_hook_file_is_not_clobbered(project, home):
    """Files we don't own are refused rather than overwritten."""
    path = install._kiro_hooks_dir(str(project)) / install.KIRO_HOOK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version":"v1","hooks":[{"name":"someone else"}]}',
                    encoding="utf-8")
    msg = install.uninstall_kiro_hook(str(project))
    assert "doesn't appear to be ours" in msg
    assert path.exists()


def test_uninstall_is_safe_when_nothing_is_installed(project, home):
    for fn in (install.uninstall_kiro_hook, install.uninstall_cursor_hook,
               install.uninstall_copilot_hook, install.uninstall_antigravity_hook):
        assert "nothing to remove" in fn(str(project)).lower()
