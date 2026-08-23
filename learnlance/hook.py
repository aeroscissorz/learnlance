"""Stop-hook entry point. Reads the hook JSON from stdin, and (by default) hands
the slow API work to a detached background process so Claude Code never waits.

Rule #1: this must NEVER break the user's Claude Code session. Everything is
wrapped so we always exit 0, no matter what goes wrong.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from . import adapters, config, core, insights


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


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


def run_hook() -> None:
    """Invoked as `learnlance hook` from Claude Code's Stop hook."""
    # Re-entry guard: the CLI backend launches a headless `claude`, which fires
    # its own Stop hook. Bail immediately so we never recurse.
    if os.environ.get(config.REENTRY_FLAG):
        return

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return  # nothing usable on stdin

    cfg = config.load_config()
    if not cfg.get("enabled", True):
        return

    if not _backend_ready(cfg):
        return

    if cfg.get("background", True):
        _spawn_worker(payload)
    else:
        process(payload)


def _backend_ready(cfg: dict) -> bool:
    """Check the configured backend can actually run; log why if not."""
    backend = cfg.get("backend", "cli")
    if backend == "api":
        if not config.get_api_key(cfg):
            config.log(f"[{_now()}] skipped: backend=api but no API key configured")
            return False
        return True
    # cli backend
    if not insights.resolve_claude_bin(cfg):
        config.log(f"[{_now()}] skipped: backend=cli but `claude` not found on PATH")
        return False
    return True


def _spawn_worker(payload: dict) -> None:
    """Write the payload to a temp file and launch a detached worker process."""
    try:
        config.ensure_home()
        job = config.WORK_DIR / f"job-{uuid.uuid4().hex}.json"
        job.write_text(json.dumps(payload), encoding="utf-8")
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
        process(payload)


def run_worker(job_path: str) -> None:
    try:
        payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
    except Exception:
        return
    try:
        process(payload)
    finally:
        try:
            Path(job_path).unlink()
        except Exception:
            pass


def process(payload: dict) -> None:
    """Route an incoming hook payload through the right adapter, then the core
    engine. Harness-agnostic — Claude, git, etc. all land here."""
    try:
        cfg = config.load_config()
        if not _backend_ready(cfg):
            return
        api_key = config.get_api_key(cfg)

        adapter = adapters.detect(payload)
        if adapter is None:
            config.log(f"[{_now()}] no adapter for payload keys={list(payload)[:6]}")
            return

        state = _load_state()
        event = adapter.to_event(payload, state, cfg)
        _save_state(state)  # persist the resume cursor even if we skip

        core.process_event(cfg, api_key, event)
    except Exception as e:  # never surface a failure to the user's session
        config.log(f"[{_now()}] ERROR in process: {e!r}")
