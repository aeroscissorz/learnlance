"""The CLI surface, and the argument forwarding the hooks depend on.

The regression here was severe and silent: the standalone launcher called
`run_hook()` with no arguments, dropping `--source`. Every Kiro payload then
failed to route and the hook did nothing at all, while `doctor` still looked fine.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from learnlance import cli, config, inchat, insights

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "learnlance_hook.py"


# --------------------------------------------------------------------------- #
# Parser wiring
# --------------------------------------------------------------------------- #
def test_hook_forwards_source_and_in_chat(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.hook, "run_hook",
                        lambda source=None, in_chat=False: seen.update(
                            source=source, in_chat=in_chat))

    cli.build_parser().parse_args(["hook", "--source", "kiro", "--in-chat"]).func(
        cli.build_parser().parse_args(["hook", "--source", "kiro", "--in-chat"]))
    assert seen == {"source": "kiro", "in_chat": True}


def test_hook_defaults_are_conservative(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.hook, "run_hook",
                        lambda source=None, in_chat=False: seen.update(
                            source=source, in_chat=in_chat))
    args = cli.build_parser().parse_args(["hook"])
    args.func(args)
    assert seen == {"source": None, "in_chat": False}


@pytest.mark.parametrize("cmd", ["setup", "install", "uninstall", "config", "show",
                                 "list", "stats", "clear", "add", "doctor", "help",
                                 "hook"])
def test_every_command_is_registered(cmd):
    args = cli.build_parser().parse_args([cmd] + (["x"] if cmd == "add" else []))
    assert callable(getattr(args, "func", None))


def test_install_exposes_every_harness():
    args = cli.build_parser().parse_args(["install"])
    for name in cli._INSTALLERS:
        assert hasattr(args, name), f"install is missing --{name}"


def test_setup_accepts_in_chat_and_path():
    args = cli.build_parser().parse_args(["setup", "--in-chat", "--path", "/tmp/p"])
    assert args.in_chat is True and args.path == "/tmp/p"


def test_in_chat_is_refused_for_harnesses_that_cannot_do_it(capsys, project, home):
    """git has no agent to ask, so the flag combination is rejected rather than
    quietly installing a normal hook."""
    args = cli.build_parser().parse_args(
        ["install", "--git", "--in-chat", "--path", str(project)])
    args.func(args)
    out = capsys.readouterr().out
    assert "isn't available for" in out and "git" in out


# --------------------------------------------------------------------------- #
# The standalone launcher — used when the console script isn't on PATH
# --------------------------------------------------------------------------- #
def _run_launcher(payload, *args, home_dir):
    env = {**dict(__import__("os").environ), "LEARNLANCE_HOME": str(home_dir)}
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        input=json.dumps(payload), text=True, capture_output=True,
        env=env, timeout=120, cwd=str(REPO))


def test_launcher_forwards_source(tmp_path):
    """Without this the payload can't be routed and the hook silently does nothing."""
    h = tmp_path / "h"
    payload = {"hook_event_name": "PostToolUse", "session_id": "L1",
               "cwd": str(tmp_path), "tool_name": "fs_write",
               "tool_input": {"path": "a.py", "text": "def f():\n    return 1\n" * 4}}
    proc = _run_launcher(payload, "--source", "kiro", home_dir=h)
    assert proc.returncode == 0, proc.stderr

    buf = h / "pending" / "L1.json"
    assert buf.exists(), (
        f"--source was dropped before run_hook; stderr={proc.stderr[:400]}")
    assert json.loads(buf.read_text("utf-8"))["edits"][0]["file"] == "a.py"


def test_launcher_forwards_source_with_equals_form(tmp_path):
    h = tmp_path / "h"
    payload = {"hook_event_name": "PostToolUse", "session_id": "L2",
               "cwd": str(tmp_path), "tool_name": "fs_write",
               "tool_input": {"path": "b.py", "text": "def g():\n    return 2\n" * 4}}
    proc = _run_launcher(payload, "--source=kiro", home_dir=h)
    assert proc.returncode == 0, proc.stderr
    assert (h / "pending" / "L2.json").exists()


def test_launcher_exits_zero_on_unusable_input(tmp_path):
    """Rule one: a hook must never break the user's session."""
    h = tmp_path / "h"
    proc = subprocess.run([sys.executable, str(LAUNCHER), "--source", "kiro"],
                          input="this is not json", text=True, capture_output=True,
                          env={**dict(__import__("os").environ),
                               "LEARNLANCE_HOME": str(h)},
                          timeout=120, cwd=str(REPO))
    assert proc.returncode == 0


def test_the_installed_hook_command_carries_the_source(project, home):
    """What the installer writes must be what the launcher can parse."""
    from learnlance import install

    install.install_kiro_hook(str(project))
    spec = json.loads((install._kiro_hooks_dir(str(project))
                       / install.KIRO_HOOK_FILENAME).read_text("utf-8"))
    for hook in spec["hooks"]:
        if hook["action"]["type"] == "command":
            assert "--source kiro" in hook["action"]["command"]


# --------------------------------------------------------------------------- #
# Backend resolution
# --------------------------------------------------------------------------- #
def test_no_backend_configured_or_installed(monkeypatch):
    monkeypatch.setattr(insights, "_which", lambda name: None)
    assert insights.resolve_backend({"llm_cmd": "", "claude_bin": ""}) is None
    assert insights.backend_label({"llm_cmd": "", "claude_bin": ""}) == ""


def test_llm_cmd_wins_and_is_split():
    argv = insights.resolve_backend({"llm_cmd": "ollama run llama3"})
    assert Path(argv[0]).name.startswith("ollama") and argv[1:] == ["run", "llama3"]


def test_a_quoted_path_with_spaces_is_one_argument():
    """shlex.split(posix=False) keeps the quote characters, and an argv[0] with
    literal quotes is not an executable — this silently disabled the backend."""
    argv = insights.resolve_backend(
        {"llm_cmd": '"C:\\Program Files\\ai\\llm.exe" --quiet'})
    assert argv == ["C:\\Program Files\\ai\\llm.exe", "--quiet"]


def test_windows_backslashes_survive_the_split():
    argv = insights.resolve_backend({"llm_cmd": r"C:\tools\llm.exe -p"})
    assert argv == [r"C:\tools\llm.exe", "-p"]


def test_legacy_claude_bin_still_works():
    argv = insights.resolve_backend({"claude_bin": r"C:\tools\claude.cmd"})
    assert argv == [r"C:\tools\claude.cmd", "-p", "--output-format", "text"]


def test_auto_detection_prefers_the_first_known_cli(monkeypatch):
    monkeypatch.setattr(insights, "_which",
                        lambda name: f"/usr/bin/{name}" if name == "gemini" else None)
    argv = insights.resolve_backend({})
    assert argv[0] == "/usr/bin/gemini"


def test_backend_label_is_readable():
    assert insights.backend_label({"llm_cmd": "ollama run llama3"}) == "ollama run llama3"


def test_a_real_cli_is_invoked_over_stdin(cfg, fake_llm):
    """Exercises argv handling, stdin piping and JSON parsing for real."""
    cfg["llm_cmd"] = fake_llm
    got = insights.generate(cfg, "FILE app/delta.py (created):\ndef delta(): ...")
    assert got["topics"][0]["name"] == "Delta encoding"
    assert got["topics"][0]["why_here"] == "saw delta"


def test_json_wrapped_in_prose_or_fences_is_still_parsed():
    """Real models add commentary and markdown fences around their JSON."""
    for text in ('```json\n{"did":"x","topics":[]}\n```',
                 'Sure! Here you go:\n{"did":"x","topics":[]}\nHope that helps.'):
        assert insights._extract_json(text) == {"did": "x", "topics": []}


def test_unparseable_model_output_raises_rather_than_silently_succeeding(cfg):
    """A silent empty result would look identical to "nothing learnable" and clear
    the buffer, losing the turn."""
    cfg["llm_cmd"] = f'"{sys.executable}" -c "print(\'no json here\')"'
    with pytest.raises(Exception):
        insights.generate(cfg, "some code")


# --------------------------------------------------------------------------- #
# Commands that read the graph
# --------------------------------------------------------------------------- #
def test_doctor_runs_and_reports_each_harness(capsys, home, project, monkeypatch):
    monkeypatch.chdir(project)
    args = cli.build_parser().parse_args(["doctor"])
    args.func(args)
    out = capsys.readouterr().out
    assert "learnlance doctor" in out
    for label in ("Claude Code", "Kiro", "Cursor", "Gemini CLI", "Antigravity"):
        assert label in out


def test_doctor_reports_in_chat_as_a_working_backend(capsys, home, project,
                                                     monkeypatch):
    from learnlance import install

    (project / ".kiro").mkdir()
    install.install_kiro_hook(str(project), True)
    monkeypatch.setattr(insights, "_which", lambda name: None)
    monkeypatch.chdir(project)

    args = cli.build_parser().parse_args(["doctor"])
    args.func(args)
    out = capsys.readouterr().out
    assert "in-chat" in out and "no CLI needed" in out


def test_list_and_stats_work_on_an_empty_graph(capsys, home, project, monkeypatch):
    monkeypatch.chdir(project)
    for cmd in (["list"], ["stats"]):
        args = cli.build_parser().parse_args(cmd)
        args.func(args)
    out = capsys.readouterr().out
    assert "Nothing learned yet" in out and "Concepts learned" in out


def test_show_writes_html_without_opening_a_browser(home, project, monkeypatch):
    monkeypatch.chdir(project)
    args = cli.build_parser().parse_args(["show", "--no-open"])
    args.func(args)
    assert config.HTML_PATH.exists()
