"""Turn a chunk of generated code into learnable concepts.

Two backends:
  * "cli" (default) — shell out to the `claude` CLI you're already logged into.
    No API key required; it rides your existing Claude Code subscription auth.
  * "api" — a direct Anthropic Messages API call (stdlib urllib, no SDK).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from . import config

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

SYSTEM = (
    "You are a senior engineer acting as a tutor. You are given code that an AI "
    "coding assistant just generated or edited for a user, plus the user's request. "
    "Your job is to surface the transferable concepts, techniques, patterns, or "
    "domain ideas a developer could LEARN from this change — the kind of thing worth "
    "adding to a personal knowledge graph. Ignore trivial boilerplate. Prefer named, "
    "googleable concepts (e.g. 'delta encoding', 'debouncing', 'idempotency keys', "
    "'topological sort', 'CORS preflight') over vague labels. Only include concepts "
    "genuinely present in the change. Explanations must be concrete and beginner-"
    "friendly. Reply with ONLY a JSON object, no prose, no markdown fences."
)

SCHEMA_HINT = """Return JSON in exactly this shape:
{
  "did": "one sentence: what was accomplished, in plain language",
  "topics": [
    {
      "name": "Delta encoding",
      "category": "algorithm|data-structure|language-feature|pattern|api|security|testing|tooling|architecture|math|domain|other",
      "level": "beginner|intermediate|advanced",
      "explanation": "2-4 sentences a newcomer can understand",
      "why_here": "one sentence: how it showed up in THIS change",
      "tags": ["compression", "diffing", "data-sync"],
      "related": ["Data synchronization", "Diffing algorithms"]
    }
  ]
}
Rules:
- Include at most {max_topics} topics, most important first.
- "tags": 2-5 SHORT lowercase kebab-case connective keywords (broad themes this
  concept belongs to, e.g. "concurrency", "caching", "http", "state-management").
  Reuse the SAME tag wording for the same theme every time so concepts across
  different sessions cluster together. Tags are how the knowledge graph links
  concepts, so choose them to maximize meaningful connections.
- "related": 2-4 adjacent concepts. Include at least one BROADER umbrella concept
  (the family this belongs to) so specific concepts ladder up to shared parents.
- If nothing is genuinely worth learning, return {"did": "...", "topics": []}."""


def _post(api_key: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _build_prompt(cfg: dict, code_blob: str) -> str:
    max_topics = int(cfg.get("max_topics_per_turn", 5))
    return (
        SCHEMA_HINT.replace("{max_topics}", str(max_topics))
        + "\n\nHere is what the assistant just produced:\n\n"
        + code_blob
    )


def _finalize(result: dict, cfg: dict) -> dict:
    max_topics = int(cfg.get("max_topics_per_turn", 5))
    result["topics"] = (result.get("topics", []) or [])[:max_topics]
    result.setdefault("did", "")
    return result


# --------------------------------------------------------------------------- #
# Backend dispatch
# --------------------------------------------------------------------------- #
def generate(cfg: dict, api_key: str, code_blob: str) -> dict:
    """Route to the configured backend. Returns {"did": str, "topics": [...]}."""
    if cfg.get("backend", "cli") == "api":
        return extract_insights(cfg, api_key, code_blob)
    return extract_insights_cli(cfg, code_blob)


# --------------------------------------------------------------------------- #
# CLI backend — no API key, uses your logged-in `claude`
# --------------------------------------------------------------------------- #
def resolve_claude_bin(cfg: dict) -> str | None:
    b = (cfg.get("claude_bin") or "").strip()
    if b:
        return b
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def extract_insights_cli(cfg: dict, code_blob: str) -> dict:
    claude = resolve_claude_bin(cfg)
    if not claude:
        raise RuntimeError("`claude` CLI not found on PATH; set claude_bin in config")

    prompt = _build_prompt(cfg, code_blob)
    # -p = headless print mode; prompt is piped in via stdin (avoids arg-length /
    # quoting limits, especially the ~8k cmd.exe cap on Windows).
    args = [claude, "-p", "--output-format", "text"]
    if cfg.get("cli_model"):
        args += ["--model", cfg["cli_model"]]

    # On Windows a .cmd/.bat shim must be run through cmd.exe.
    if os.name == "nt" and claude.lower().endswith((".cmd", ".bat")):
        args = ["cmd", "/c"] + args

    env = dict(os.environ)
    env[config.REENTRY_FLAG] = "1"  # stop the nested claude's hook from recursing

    proc = subprocess.run(
        args, input=prompt, capture_output=True, text=True,
        env=env, timeout=240,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {(proc.stderr or '')[:300]}")
    result = _extract_json(proc.stdout)
    return _finalize(result, cfg)


# --------------------------------------------------------------------------- #
# API backend — direct Anthropic call (needs a key)
# --------------------------------------------------------------------------- #
def extract_insights(cfg: dict, api_key: str, code_blob: str) -> dict:
    """Returns {"did": str, "topics": [...]}. Raises on hard API/parse failure."""
    prompt = _build_prompt(cfg, code_blob)
    payload = {
        "model": cfg.get("model"),
        "max_tokens": 1400,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = _post(api_key, payload)
    text = ""
    for block in resp.get("content", []) or []:
        if block.get("type") == "text":
            text += block.get("text", "")
    return _finalize(_extract_json(text), cfg)
