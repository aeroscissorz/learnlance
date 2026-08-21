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

from . import config, graph, insights, transcript, viz


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
    """The actual work: parse transcript -> insights -> graph -> html."""
    try:
        cfg = config.load_config()
        if not _backend_ready(cfg):
            return
        api_key = config.get_api_key(cfg)

        tpath = payload.get("transcript_path", "")
        session = payload.get("session_id", "") or "unknown"
        cwd = payload.get("cwd", "")
        if not tpath:
            return

        state = _load_state()
        sstate = state.get(session, {})
        since = sstate.get("last_uuid")

        entries = transcript.read_entries(tpath)
        if not entries:
            return
        gen = transcript.collect_new_generation(entries, since)

        # Always advance the cursor so we never reprocess the same turn.
        state[session] = {"last_uuid": gen["last_uuid"], "updated": _now()}
        _save_state(state)

        edits = gen["edits"]
        total_chars = sum(len(e["code"]) for e in edits)
        if not edits or total_chars < int(cfg.get("min_chars", 40)):
            config.log(f"[{_now()}] {session[:8]}: no substantive code this turn, skipped")
            return

        blob = transcript.build_input_blob(gen, int(cfg.get("max_input_chars", 14000)))
        result = insights.generate(cfg, api_key, blob)
        if not result.get("topics"):
            config.log(f"[{_now()}] {session[:8]}: nothing learnable")
            return

        g = graph.load()
        files = sorted({e["file"] for e in edits})
        new_names = graph.update(
            g, result,
            {"when": _now(), "session": session, "cwd": cwd, "files": files},
        )
        graph.save(g)
        viz.render_html(g)
        _write_recap(session, result, new_names, cwd)

        # Best-effort: shown in Claude Code transcript view.
        names = ", ".join(t["name"] for t in result["topics"])
        print(f"🧠 learnlance: {result.get('did','')} — learned/reinforced: {names}")
    except Exception as e:  # never surface a failure to the user's session
        config.log(f"[{_now()}] ERROR in process: {e!r}")


def _write_recap(session: str, result: dict, new_names: list, cwd: str) -> None:
    try:
        md = config.INSIGHTS_DIR / f"{session}.md"
        lines = []
        if not md.exists():
            lines.append(f"# Learning recap — session `{session[:12]}`\n")
            if cwd:
                lines.append(f"_Project: {cwd}_\n")
        lines.append(f"\n## {_now()}\n")
        lines.append(f"**What happened:** {result.get('did','')}\n")
        for t in result["topics"]:
            tag = " 🌱 *new*" if t["name"] in new_names else ""
            lines.append(f"\n### {t['name']}{tag}  \n`{t.get('category','')}` · `{t.get('level','')}`\n")
            lines.append(f"{t.get('explanation','')}\n")
            if t.get("why_here"):
                lines.append(f"> _Here:_ {t['why_here']}\n")
        with md.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception as e:
        config.log(f"[{_now()}] recap write failed: {e!r}")
