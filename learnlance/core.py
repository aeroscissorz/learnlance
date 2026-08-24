"""The harness-agnostic learning engine.

Takes a normalized `CodeEvent` (from any adapter) and runs the pipeline:
  insights -> knowledge graph -> HTML -> per-session recap.
Knows nothing about Claude, git, or any specific tool.
"""
from __future__ import annotations

import datetime as _dt

from . import config, graph, insights, pending, viz
from .events import CodeEvent


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def process_event(cfg: dict, event: CodeEvent | None) -> None:
    """Run one CodeEvent through the learning pipeline. Never raises."""
    try:
        if event is None:
            return
        tag = f"{event.source}:{event.session[:8]}"
        if event.skip_reason:
            config.log(f"[{_now()}] {tag}: {event.skip_reason}")
            return

        # Checked here rather than at the hook entrypoint: adapters that merely
        # buffer an edit must keep working without a reachable backend. Returning
        # early leaves the session's buffer intact, so nothing is lost — the next
        # turn retries it once a backend exists.
        if not insights.resolve_backend(cfg):
            config.log(f"[{_now()}] {tag}: no LLM CLI found, keeping {len(event.files)} "
                       f"file(s) buffered to retry later")
            return

        result = insights.generate(cfg, event.blob)
        merge_result(cfg, event, result)
    except Exception as e:  # never surface a failure to the user's session
        config.log(f"[{_now()}] ERROR in process_event: {e!r}")


def _say(msg: str) -> None:
    """Print a summary without ever raising.

    Two hazards on the hook path, which doesn't go through `cli.main()` and so
    never gets its UTF-8 reconfigure: a Windows console is often cp1252 and can't
    encode the emoji, and `UnicodeEncodeError` here would abort the caller after
    the graph had already been updated.
    """
    try:
        print(f"\U0001f9e0 {msg}")
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass  # nothing left worth trying; the log already has it


def merge_result(cfg: dict, event: CodeEvent, result: dict,
                 announce: bool = True) -> bool:
    """Merge an insights dict into the project's graph. Returns True if anything
    landed.

    Split out from `process_event` because the concepts don't always come from an
    LLM CLI: in-chat mode has the agent produce them, and this is where that
    output rejoins the normal pipeline.
    """
    tag = f"{event.source}:{event.session[:8]}"
    if not result or not result.get("topics"):
        # Nothing learnable is a real answer, not a failure — drop the buffer so
        # the same code isn't re-analyzed every turn.
        pending.clear(event.session)
        config.log(f"[{_now()}] {tag}: nothing learnable")
        return False

    cwd = event.cwd or "."
    config.register_project(cwd)

    g = graph.load_project(cwd)
    new_names = graph.update(g, result, {
        "when": _now(), "session": event.session,
        "cwd": cwd, "files": event.files,
    })
    graph.save_project(g, cwd)
    viz.render_project_html(g, cwd)
    write_recap(event.session, result, new_names, cwd)

    # Safe to forget the buffered edits now that they're in the graph. A no-op for
    # transcript-style adapters, which never buffered anything.
    pending.clear(event.session)

    names = ", ".join(t["name"] for t in result["topics"])
    summary = f"learnlance: {result.get('did', '')} - learned/reinforced: {names}"
    config.log(f"[{_now()}] {tag}: {summary}")
    # `announce=False` in in-chat mode: stdout there carries the JSON the harness
    # parses, so anything else written to it would corrupt the response.
    if announce:
        _say(summary)
    return True


def write_recap(session: str, result: dict, new_names: list, cwd: str) -> None:
    try:
        md = config.INSIGHTS_DIR / f"{session.replace(':', '_').replace('/', '_')}.md"
        lines = []
        if not md.exists():
            lines.append(f"# Learning recap — `{session[:24]}`\n")
            if cwd:
                lines.append(f"_Project: {cwd}_\n")
        lines.append(f"\n## {_now()}\n")
        lines.append(f"**What happened:** {result.get('did', '')}\n")
        for t in result["topics"]:
            new = " 🌱 *new*" if t["name"] in new_names else ""
            lines.append(f"\n### {t['name']}{new}  \n`{t.get('category', '')}` · `{t.get('level', '')}`\n")
            lines.append(f"{t.get('explanation', '')}\n")
            if t.get("why_here"):
                lines.append(f"> _Here:_ {t['why_here']}\n")
        with md.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception as e:
        config.log(f"[{_now()}] recap write failed: {e!r}")
