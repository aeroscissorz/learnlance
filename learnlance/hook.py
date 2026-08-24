"""Hook entry point. Reads the harness's hook JSON from stdin, turns it into a
`CodeEvent` inline, and (by default) hands the slow LLM work to a detached
background process so the host session never waits.

The split matters: adapters are cheap and some harnesses report a single edit per
call, so event-building happens inline where it can't be lost. Only events that
actually need analyzing are handed off.

Rule #1: this must NEVER break the user's editing session. Everything is wrapped
so we always exit 0, no matter what goes wrong.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from . import adapters, config, core, inchat
from .events import CodeEvent


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _try_autosetup() -> None:
    """Run auto-setup if needed — called on first hook invocation too."""
    try:
        from . import autosetup
        if autosetup.needs_setup():
            autosetup.run()
    except Exception:
        pass


def _load_state() -> dict:
    if config.STATE_PATH.exists():
        try:
            return json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    config.ensure_home()
    config.STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def run_hook(source: str | None = None, in_chat: bool = False) -> None:
    """Invoked as `learnlance hook` from a harness's hook config.

    `source` (from `--source`) pins which adapter handles the payload. Harnesses
    whose stdin JSON we can't reshape in a one-line shell command (Kiro) declare
    themselves this way instead, so their payload passes through untouched.

    `in_chat` (from `--in-chat`) asks the agent to do the analysis rather than
    shelling out to an LLM CLI. See inchat.py.
    """
    # Re-entry guard: the CLI backend launches a headless `claude`, which fires
    # its own Stop hook. Bail immediately so we never recurse.
    if os.environ.get(config.REENTRY_FLAG):
        return

    # On first invocation, auto-configure any missing hooks.
    _try_autosetup()

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}  # unparseable stdin; a pinned --source may still be usable

    if not isinstance(payload, dict):
        payload = {}
    if source:
        payload["source"] = source

    cfg = config.load_config()
    if not cfg.get("enabled", True):
        return

    # Adapters run inline: turning a payload into an event is cheap, and for
    # harnesses that report one edit at a time (Kiro) it's just an append to a
    # buffer. Doing it here means an edit can't be lost to a worker that never
    # got scheduled, and we avoid paying process-spawn cost per keystroke-ish
    # tool call. Note there's deliberately no backend check on this path — edits
    # must keep accumulating even before `claude` is reachable.
    event = build_event(cfg, payload)
    if event is None or event.skip_reason:
        if event is not None:
            config.log(f"[{_now()}] {event.source}:{event.session[:8]}: {event.skip_reason}")
        return

    # In-chat mode never calls an LLM itself — it asks the agent, and prints the
    # harness's "run another turn" JSON on stdout. Must stay in this process.
    if in_chat:
        _handle_in_chat(cfg, event, payload)
        return

    # Only the LLM call is expensive, so only that gets handed off.
    if cfg.get("background", True):
        _spawn_worker(event)
    else:
        core.process_event(cfg, event)


def _handle_in_chat(cfg: dict, event: CodeEvent, payload: dict) -> None:
    """End of a turn, with the agent doing the analysis instead of a CLI.

    Either the agent already answered (ingest it), or we spend one turn asking.
    Anything printed on stdout is the harness's instruction to run that turn.
    """
    tag = f"{event.source}:{event.session[:8]}"

    # Did the previous ask get answered? If so this turn is just bookkeeping.
    result = inchat.ingest()
    if result is not None:
        # announce=False: this hook's stdout is a JSON channel to the harness.
        core.merge_result(cfg, event, result, announce=False)
        state = _load_state()
        inchat.clear_asks(state, event.session)
        _save_state(state)
        # Belt and braces: the agent is told to remove the marker itself, but if
        # it forgot, a stale marker would make it analyze again next turn.
        inchat.close_request()
        config.log(f"[{_now()}] {tag}: ingested {len(result['topics'])} concept(s) "
                   f"from the agent")
        return

    state = _load_state()
    ask, why_not = inchat.should_ask(payload, state, event.session)
    if not ask:
        # Leave the buffer alone: a later turn can still pick it up.
        config.log(f"[{_now()}] {tag}: not asking ({why_not})")
        inchat.close_request()
        return

    inchat.record_ask(state, event.session)
    _save_state(state)

    out = inchat.followup_output(event.source, inchat.build_prompt(cfg))
    if out is None:
        # Kiro reaches the agent through a separate `agent`-action hook whose
        # prompt is static, so the only thing this hook can do is satisfy the
        # condition that prompt checks for.
        inchat.open_request(event.files)
        config.log(f"[{_now()}] {tag}: opened an analyze request for the "
                   f"agent-action hook ({len(event.files)} file(s))")
        return

    config.log(f"[{_now()}] {tag}: asking the agent to analyze "
               f"{len(event.files)} file(s)")
    sys.stdout.write(json.dumps(out))


def build_event(cfg: dict, payload: dict) -> CodeEvent | None:
    """Route a payload to its adapter and return the resulting event."""
    try:
        adapter = adapters.detect(payload)
        if adapter is None:
            config.log(f"[{_now()}] no adapter for payload keys={list(payload)[:6]}")
            return None
        state = _load_state()
        event = adapter.to_event(payload, state, cfg)
        _save_state(state)  # persist the resume cursor even if we skip
        return event
    except Exception as e:
        config.log(f"[{_now()}] ERROR building event: {e!r}")
        return None


def _spawn_worker(event: CodeEvent) -> None:
    """Write the event to a temp file and launch a detached worker process."""
    try:
        config.ensure_home()
        job = config.WORK_DIR / f"job-{uuid.uuid4().hex}.json"
        job.write_text(json.dumps(asdict(event)), encoding="utf-8")
        cmd = [sys.executable, "-m", "learnlance", "_worker", str(job)]
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(Path(__file__).resolve().parent.parent),
        }
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:  # fall back to inline so we still learn something
        config.log(f"[{_now()}] spawn failed ({e}); running inline")
        core.process_event(config.load_config(), event)


def run_worker(job_path: str) -> None:
    """Analyze a single serialized event, then clean up its job file."""
    try:
        data = json.loads(Path(job_path).read_text(encoding="utf-8"))
        event = CodeEvent(**data)
    except Exception as e:
        config.log(f"[{_now()}] worker could not read job {job_path}: {e!r}")
        return
    try:
        core.process_event(config.load_config(), event)
    except Exception as e:  # never surface a failure to the user's session
        config.log(f"[{_now()}] ERROR in worker: {e!r}")
    finally:
        try:
            Path(job_path).unlink()
        except Exception:
            pass


def process(payload: dict) -> None:
    """Handle a payload start-to-finish, inline. Kept for embedding and tests."""
    try:
        cfg = config.load_config()
        core.process_event(cfg, build_event(cfg, payload))
    except Exception as e:  # never surface a failure to the user's session
        config.log(f"[{_now()}] ERROR in process: {e!r}")
