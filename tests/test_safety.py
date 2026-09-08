"""Regression tests for the config-safety and consent bugs.

Each of these reproduced a real defect in 0.2.7:
  * a BOM'd or malformed config file was silently replaced, destroying every
    setting the user had (model, permissions, mcpServers, ...);
  * `uninstall` was undone by the implicit auto-setup on the very next command;
  * the git hook was written with CRLF, so `#!/bin/sh` became `#!/bin/sh\r`;
  * autosetup announced itself on stdout, which is a harness's JSON channel.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from learnlance import autosetup, cli, config, install


def test_windows_hook_command_uses_powershell_call_operator(monkeypatch):
    """Quoted Windows executables need `&` when Copilot invokes PowerShell."""
    monkeypatch.setattr(install.os, "name", "nt")
    monkeypatch.setattr(install.shutil, "which",
                        lambda name: r"C:\workspaces\outbound test\venv\Scripts\learnlance.EXE")
    # Simulate a site-packages install, where no source launcher sits alongside
    # the package — otherwise the source-checkout path would win first.
    monkeypatch.setattr(install, "__file__",
                        r"C:\fake\site-packages\learnlance\install.py")

    assert install.hook_command() == (
        r'& "C:\workspaces\outbound test\venv\Scripts\learnlance.EXE" hook')

USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "mcpServers": {"foo": {"command": "bar"}},
}


def _claude_settings(user_home):
    p = user_home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# never destroy a config we cannot read
# --------------------------------------------------------------------------- #
def test_install_preserves_settings_written_with_a_bom(home, user_home):
    """Notepad and PowerShell `>` write a UTF-8 BOM; json.loads chokes on it."""
    p = _claude_settings(user_home)
    p.write_text(json.dumps(USER_SETTINGS), encoding="utf-8-sig")

    install.install_hook()

    after = json.loads(p.read_text(encoding="utf-8-sig"))
    for key, value in USER_SETTINGS.items():
        assert after[key] == value, f"{key} was destroyed"
    assert after["hooks"]["Stop"], "hook should still have been installed"


def test_install_refuses_to_overwrite_unparseable_settings(home, user_home):
    p = _claude_settings(user_home)
    p.write_text("{ this is not json", encoding="utf-8")

    msg = install.install_hook()

    assert "Refusing" in msg
    assert p.read_text(encoding="utf-8") == "{ this is not json", "file was modified"


def test_uninstall_refuses_to_overwrite_unparseable_settings(home, user_home):
    p = _claude_settings(user_home)
    p.write_text("]]not json[[", encoding="utf-8")

    msg = install.uninstall_hook()

    assert "Refusing" in msg
    assert p.read_text(encoding="utf-8") == "]]not json[[", "file was modified"


def test_uninstall_leaves_foreign_settings_untouched(home, user_home):
    p = _claude_settings(user_home)
    p.write_text(json.dumps(USER_SETTINGS), encoding="utf-8")

    install.uninstall_hook()  # nothing of ours in there

    assert json.loads(p.read_text(encoding="utf-8")) == USER_SETTINGS


def test_install_survives_a_junk_stop_entry(home, user_home):
    """A non-dict inside hooks.Stop used to raise AttributeError."""
    p = _claude_settings(user_home)
    p.write_text(json.dumps({"hooks": {"Stop": ["garbage", 42]}}), encoding="utf-8")

    install.install_hook()

    stop = json.loads(p.read_text(encoding="utf-8"))["hooks"]["Stop"]
    assert "garbage" in stop and 42 in stop, "foreign entries were dropped"
    assert any(install._group_is_ours(g) for g in stop)


# --------------------------------------------------------------------------- #
# only claim hooks that are actually ours
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("command", [
    "C:/dev/learnlance-univ/.cursor/hooks/format.sh",  # path contains our name
    "C:/repos/learnlance/scripts/lint.sh",
    "npx prettier --write",
])
def test_foreign_hooks_are_not_mistaken_for_ours(command):
    assert not install._is_ours({"command": command})


@pytest.mark.parametrize("command", [
    '"C:\\Program Files\\Python313\\Scripts\\learnlance.EXE" hook --source codex',
    "learnlance hook",
    "/usr/bin/learnlance hook --in-chat",
    "python -m learnlance hook",
    "C:/x/learnlance_hook.py",
])
def test_our_own_hooks_are_recognized(command):
    assert install._is_ours({"command": command})


# --------------------------------------------------------------------------- #
# consent: an explicit uninstall must stick
# --------------------------------------------------------------------------- #
def test_uninstall_stops_autosetup_from_reinstalling(home, user_home, project):
    autosetup.run(str(project), force=True)
    assert autosetup.claude_hook_present()

    autosetup.set_opted_out(True)  # what `learnlance uninstall` records
    install.uninstall_hook()
    assert not autosetup.claude_hook_present()

    assert autosetup.run(str(project)) == [], "implicit run ignored the opt-out"
    assert not autosetup.claude_hook_present()


def test_explicit_setup_clears_the_opt_out(home, user_home, project):
    autosetup.set_opted_out(True)
    autosetup.run(str(project), force=True)  # `setup` / `install`
    assert not autosetup.opted_out()
    assert autosetup.claude_hook_present()


def test_autosetup_is_idempotent_across_repeated_runs(home, user_home, project):
    """The git branch used to reinstall on every invocation, because git has no
    in-chat mode for the mode-comparison to ever match."""
    (project / ".git").mkdir()
    autosetup.run(str(project), force=True, in_chat=True)
    assert autosetup.run(str(project), in_chat=True) == []
    assert autosetup.run(str(project), in_chat=True) == []


# --------------------------------------------------------------------------- #
# the hook's stdout is a data channel — keep announcements off it
# --------------------------------------------------------------------------- #
def test_read_only_commands_announce_on_stderr_not_stdout(home, user_home, project,
                                                          monkeypatch, capsys):
    monkeypatch.chdir(project)
    (project / ".git").mkdir()
    cli.main(["stats"])
    out = capsys.readouterr()
    assert "configured hooks" not in out.out, "announcement polluted stdout"


@pytest.mark.parametrize("cmd", ["hook", "_worker", "install", "uninstall", "setup"])
def test_autosetup_is_skipped_for_commands_that_must_not_trigger_it(cmd):
    assert cmd in cli._NO_AUTOSETUP


# --------------------------------------------------------------------------- #
# cross-platform: git runs the hook through sh, which needs LF
# --------------------------------------------------------------------------- #
def test_git_hook_is_written_with_lf_endings(home, project):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    subprocess.run(["git", "init", "-q", str(project)],
                   capture_output=True, check=True)

    install.install_git_hook(str(project))

    hooks = install._hooks_dir(str(project))
    assert hooks is not None
    body = (hooks / "post-commit").read_bytes()
    assert b"\r\n" not in body, "CRLF makes the shebang '#!/bin/sh\\r'"
    assert body.startswith(b"#!/bin/sh\n")
