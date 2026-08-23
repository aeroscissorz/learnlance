"""The harness-agnostic learning engine.

Takes a normalized `CodeEvent` (from any adapter) and runs the pipeline:
  insights -> knowledge graph -> HTML -> per-session recap.
Knows nothing about Claude, git, or any specific tool.
"""
from __future__ import annotations

import datetime as _dt

from . import config, graph, insights, viz
from .events import CodeEvent


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def process_event(cfg: dict, api_key: str, event: CodeEvent | None) -> None:
    """Run one CodeEvent through the learning pipeline. Never raises."""
    try:
        if event is None:
            return
        tag = f"{event.source}:{event.session[:8]}"
        if event.skip_reason:
            config.log(f"[{_now()}] {tag}: {event.skip_reason}")
            return

        result = insights.generate(cfg, api_key, event.blob)
        if not result.get("topics"):
            config.log(f"[{_now()}] {tag}: nothing learnable")
            return

        g = graph.load()
        new_names = graph.update(g, result, {
            "when": _now(), "session": event.session,
            "cwd": event.cwd, "files": event.files,
        })
        graph.save(g)
        viz.render_html(g)
        write_recap(event.session, result, new_names, event.cwd)

        names = ", ".join(t["name"] for t in result["topics"])
        print(f"🧠 learnlance: {result.get('did', '')} — learned/reinforced: {names}")
    except Exception as e:  # never surface a failure to the user's session
        config.log(f"[{_now()}] ERROR in process_event: {e!r}")


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
