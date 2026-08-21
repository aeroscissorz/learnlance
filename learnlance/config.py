"""Configuration + on-disk paths for learnlance.

Everything lives under ~/.learnlance (override with LEARNLANCE_HOME).
No third-party dependencies anywhere in this package on purpose: the hook must
run reliably in whatever environment Claude Code launches it in.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("LEARNLANCE_HOME", str(Path.home() / ".learnlance")))
CONFIG_PATH = HOME / "config.json"
GRAPH_PATH = HOME / "graph.json"
HTML_PATH = HOME / "graph.html"
STATE_PATH = HOME / "state.json"
INSIGHTS_DIR = HOME / "insights"
WORK_DIR = HOME / "work"
LOG_PATH = HOME / "learnlance.log"

DEFAULTS = {
    "enabled": True,
    # How insights are generated:
    #   "cli" -> shell out to the `claude` CLI you're already logged into
    #            (NO API key needed — uses your Claude Code subscription auth).
    #   "api" -> direct Anthropic API call (needs an API key).
    "backend": "cli",
    "claude_bin": "",  # path to the `claude` executable ("" => auto-detect on PATH)
    "cli_model": "",  # optional model alias for the CLI (e.g. "haiku"); "" => default
    # Used only by the "api" backend:
    "model": "claude-haiku-4-5-20251001",
    "api_key": "",  # empty => fall back to ANTHROPIC_API_KEY env var
    "max_topics_per_turn": 5,
    "background": True,  # run the work detached so Claude Code stays snappy
    "min_chars": 40,  # skip trivial edits (renames, one-liners) to save calls
    "max_input_chars": 14000,  # cap the code we send per turn
}

# Env flag set on any `claude` process we spawn, so the Stop hook it fires can
# recognize it's a learnlance-triggered call and bail instead of recursing.
REENTRY_FLAG = "LEARNLANCE_ACTIVE"


def ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    ensure_home()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_api_key(cfg: dict) -> str:
    return (cfg.get("api_key") or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()


def log(msg: str) -> None:
    try:
        ensure_home()
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(msg.rstrip() + "\n")
    except Exception:
        pass
