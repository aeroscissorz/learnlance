"""Turn a chunk of generated code into learnable concepts.

Analysis is done by shelling out to an LLM **CLI** you're already logged into, so
there's no API key to manage. Any CLI works as long as it reads a prompt on stdin
and writes the answer to stdout, which every one below does.

Why a CLI and not the host IDE's own model: hook APIs hand your script data and
accept a decision back — none of them expose an inference endpoint. But the
harnesses mostly ship CLIs that are already authenticated, so shelling out to one
uses the subscription the user already has. See ARCHITECTURE.md.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess

from . import config

# Known CLIs, in the order we'll auto-detect them, with the flags that put each
# into "read one prompt, print the answer, exit" mode.
KNOWN_BACKENDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude", ("-p", "--output-format", "text")),
    ("gemini", ("-p",)),
    ("copilot", ("-p",)),
    ("cursor-agent", ("-p",)),
    ("ollama", ("run", "llama3")),
)

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
# Backend resolution — any logged-in LLM CLI, no API key
# --------------------------------------------------------------------------- #
def _which(name: str) -> str | None:
    """Find an executable, allowing for Windows' .cmd/.exe shims."""
    for cand in (name, f"{name}.cmd", f"{name}.exe", f"{name}.bat"):
        found = shutil.which(cand)
        if found:
            return found
    return None


def _split_cmd(raw: str) -> list[str]:
    """Split a configured command line into argv.

    `posix=False` so that backslashes in Windows paths survive the split — but
    that mode also leaves the quote characters attached to each token, and an
    argv[0] of `"C:\\...\\python.exe"` with literal quotes is not an executable
    anything can find. So strip a matched surrounding pair afterwards.
    """
    out = []
    for part in shlex.split(raw, posix=False):
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1]
        out.append(part)
    return out


def resolve_backend(cfg: dict) -> list[str] | None:
    """Return the argv that runs the configured LLM CLI, or None if there is none.

    Priority:
      1. `llm_cmd` — an explicit command line, e.g. "ollama run llama3".
      2. `claude_bin` — kept working for configs written before this was general.
      3. Auto-detection across KNOWN_BACKENDS, first one on PATH wins.
    """
    raw = (cfg.get("llm_cmd") or "").strip()
    if raw:
        argv = _split_cmd(raw)
        if argv:
            exe = _which(argv[0]) or argv[0]
            return [exe, *argv[1:]]

    legacy = (cfg.get("claude_bin") or "").strip()
    if legacy:
        return [legacy, "-p", "--output-format", "text"]

    for name, flags in KNOWN_BACKENDS:
        found = _which(name)
        if found:
            return [found, *flags]
    return None


def backend_label(cfg: dict) -> str:
    """Short name of the backend in use, for `doctor`."""
    argv = resolve_backend(cfg)
    if not argv:
        return ""
    stem = os.path.basename(argv[0])
    for ext in (".cmd", ".exe", ".bat"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
    tail = " ".join(a for a in argv[1:] if not a.startswith("-"))
    return f"{stem} {tail}".strip()


def _run_cli(cfg: dict, prompt: str) -> str:
    """Send `prompt` to the configured LLM CLI, return its raw text output."""
    argv = resolve_backend(cfg)
    if not argv:
        raise RuntimeError("no LLM CLI found; set one with `learnlance config --llm-cmd`")

    args = list(argv)
    if cfg.get("cli_model"):
        args += ["--model", cfg["cli_model"]]

    # On Windows a .cmd/.bat shim has to be run through cmd.exe.
    if os.name == "nt" and args[0].lower().endswith((".cmd", ".bat")):
        args = ["cmd", "/c"] + args

    env = dict(os.environ)
    # Stop a nested agent's own Stop hook from recursing back into learnlance.
    env[config.REENTRY_FLAG] = "1"

    # The prompt goes in on stdin, never as an argument — it's far too big for
    # the ~8k command-line cap on Windows.
    proc = subprocess.run(
        args, input=prompt, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=env, timeout=240,
    )
    if proc.returncode != 0:
        exe = os.path.basename(args[0])
        raise RuntimeError(f"{exe} exited {proc.returncode}: {(proc.stderr or '')[:300]}")
    return proc.stdout


def generate(cfg: dict, code_blob: str) -> dict:
    """Extract learnable insights from code. Returns {"did": str, "topics": [...]}."""
    result = _extract_json(_run_cli(cfg, _build_prompt(cfg, code_blob)))
    return _finalize(result, cfg)


# --------------------------------------------------------------------------- #
# Manual add — `learnlance add <topic>`
# --------------------------------------------------------------------------- #
def _build_add_prompt(topic: str, code_blob: str) -> str:
    return (
        SCHEMA_HINT.replace("{max_topics}", "3")
        + f'\n\nThe developer wants to add the concept "{topic}" to their '
          "knowledge graph — they believe it appears in their codebase but it "
          "was missed. Using the code excerpts below, identify how "
          f'"{topic}" (and at most a couple of tightly-related sub-concepts that '
          "are genuinely present) show up, and return them. Put the concept that "
          "most directly matches the developer's request FIRST, and make its "
          '"why_here" cite where in the code it appears. If the concept truly is '
          "not present in the code, return an empty topics list.\n\n"
          "Here are the relevant code excerpts:\n\n"
        + (code_blob or f"(no direct code matches were found for \"{topic}\")")
    )


def add_concept(cfg: dict, topic: str, code_blob: str) -> dict:
    """Ask the CLI to describe `topic` as it appears in the given code.

    Returns {"did": str, "topics": [...]} just like the hook's extractor.
    """
    prompt = _build_add_prompt(topic, code_blob)
    text = _run_cli(cfg, prompt)
    result = _extract_json(text)
    result["topics"] = (result.get("topics", []) or [])[:3]
    result.setdefault("did", f"Manually added '{topic}'.")
    return result
