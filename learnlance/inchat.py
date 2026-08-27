"""In-chat analysis: let the agent analyze its own work, with no separate LLM.

The normal path shells out to an LLM CLI. That means installing one, and paying
for it separately from the subscription already running the agent. In-chat mode
avoids both by asking the agent that just wrote the code to name the concepts
itself, using a mechanism every harness exposes: an end-of-turn hook can return a
prompt that the harness submits as another turn.

    Cursor       stop        -> {"followup_message": …}
    Copilot      agentStop   -> {"decision": "block",  "reason": …}
    Gemini CLI   AfterAgent  -> {"decision": "deny",   "reason": …}
    Antigravity  Stop        -> {"decision": "block",  "reason": …}
    Kiro         Stop        -> a second hook with the `agent` action type

The agent writes its answer as JSON into `~/.learnlance/inbox/`, and the next
end-of-turn hook picks it up and merges it into the graph. An inbox *directory*
rather than a per-session file is what lets Kiro participate: its `agent` action
carries a static prompt that can't be told the session id, so the filename has to
be the agent's choice.

The cost, and why this isn't the default: it happens in your conversation. You see
the agent do bookkeeping at the end of each turn, and it spends some of your
context. The CLI path stays the default for being invisible.
"""
from __future__ import annotations

import datetime as _dt
import json

from . import config

# One extra turn per turn of real work. Two would mean the analysis turn itself
# triggers an analysis turn, forever.
_MAX_ASKS = 1


def inbox_dir():
    d = config.HOME / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Request marker — the loop guard for static prompts
# --------------------------------------------------------------------------- #
# Kiro reaches the agent through its `agent` action, which carries a *static*
# prompt from the config file. Static means we can't stop it firing every turn,
# including the turn it created — an unbounded loop. So the prompt is written as a
# conditional: act only if this marker exists, and delete it when done. The
# command hook is what creates the marker, which puts the loop back under our
# control rather than relying on the model to notice it's repeating itself.
def request_path():
    return config.HOME / "analyze-me"


def open_request(files: list[str]) -> None:
    try:
        config.ensure_home()
        request_path().write_text(
            "\n".join(files[:40]) or "(no files recorded)", encoding="utf-8")
    except Exception as e:
        config.log(f"[inchat] could not open request: {e!r}")


def close_request() -> None:
    try:
        p = request_path()
        if p.exists():
            p.unlink()
    except Exception as e:
        config.log(f"[inchat] could not close request: {e!r}")


def request_open() -> bool:
    return request_path().exists()


PROMPT = """\
[learnlance] Before you finish: record what was learnable in the code you just \
wrote, so it can be added to a personal knowledge graph.

Write ONLY a JSON file to this exact path (create the directory if needed):

    {path}

with this shape:

{{
  "did": "one sentence: what was accomplished, in plain language",
  "topics": [
    {{
      "name": "Delta encoding",
      "category": "algorithm|data-structure|language-feature|pattern|api|security|testing|tooling|architecture|math|domain|other",
      "level": "beginner|intermediate|advanced",
      "explanation": "2-4 sentences a newcomer could understand",
      "why_here": "one sentence: how it showed up in THIS change",
      "tags": ["compression", "diffing"],
      "related": ["Data synchronization", "Diffing algorithms"]
    }}
  ]
}}

Rules:
- At most {max_topics} topics, most important first.
- Name transferable, googleable concepts ("idempotency keys", "topological sort"),
  not vague labels and not a description of this codebase.
- Only concepts genuinely present in what you just wrote. Skip boilerplate.
- "tags": 2-5 short lowercase kebab-case themes. Reuse the same wording for the
  same theme every time — tags are what link concepts together in the graph.
- "related": 2-4 adjacent concepts, including one broader umbrella concept.
- If nothing was genuinely worth learning, write {{"did": "...", "topics": []}}.

Write the file and stop. No commentary, no summary of this instruction.
"""


def build_prompt(cfg: dict) -> str:
    target = inbox_dir() / "concepts-<n>.json"
    return PROMPT.format(path=target,
                         max_topics=int(cfg.get("max_topics_per_turn", 5)))


def static_prompt(cfg: dict) -> str:
    """The prompt for harnesses that can only carry a fixed string (Kiro).

    Guarded on the request marker so it's inert on turns we didn't ask for —
    without that, a static end-of-turn prompt would re-trigger on the very turn it
    produced and never stop.
    """
    return (f"[learnlance] First check whether this file exists:\n\n"
            f"    {request_path()}\n\n"
            f"If it does NOT exist, do nothing at all and finish — no output, no "
            f"comment. If it DOES exist, then:\n\n"
            f"{build_prompt(cfg)}\n"
            f"Finally delete {request_path()} so this does not repeat.")


# --------------------------------------------------------------------------- #
# Reading back what the agent wrote
# --------------------------------------------------------------------------- #
def _valid(data) -> bool:
    return (isinstance(data, dict)
            and isinstance(data.get("topics"), list)
            and all(isinstance(t, dict) and t.get("name") for t in data["topics"]))


def ingest() -> dict | None:
    """Merge every JSON file in the inbox into one insights dict, then remove them.

    Returns None when the inbox is empty or holds nothing usable. Files that don't
    parse are deleted too — leaving them would make every subsequent turn retry the
    same broken output forever.
    """
    files = sorted(inbox_dir().glob("*.json"))
    if not files:
        return None

    dids, topics = [], []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            config.log(f"[inchat] discarding unparseable {f.name}: {e!r}")
            data = None
        if _valid(data):
            if data.get("did"):
                dids.append(str(data["did"]))
            topics.extend(data["topics"])
        elif data is not None:
            config.log(f"[inchat] discarding {f.name}: wrong shape")
        try:
            f.unlink()
        except Exception:
            pass

    if not topics:
        return None
    return {"did": " ".join(dids)[:400], "topics": topics}


# --------------------------------------------------------------------------- #
# Loop control
# --------------------------------------------------------------------------- #
def _already_retrying(payload: dict) -> bool:
    """True if the harness says this turn was itself forced by a previous hook.

    Copilot and Gemini both expose `stop_hook_active`; Cursor counts its automatic
    follow-ups in `loop_count`. Respecting these means we cooperate with each
    harness's own runaway guard instead of racing it.
    """
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return True
    for key in ("loop_count", "loopCount"):
        try:
            if int(payload.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def asks_so_far(state: dict, session: str) -> int:
    return int(state.get(f"inchat:{session}", {}).get("asks", 0))


def record_ask(state: dict, session: str) -> None:
    state[f"inchat:{session}"] = {
        "asks": asks_so_far(state, session) + 1,
        "updated": _dt.datetime.now().isoformat(timespec="seconds"),
    }


def clear_asks(state: dict, session: str) -> None:
    state.pop(f"inchat:{session}", None)


def should_ask(payload: dict, state: dict, session: str) -> tuple[bool, str]:
    """Decide whether to spend another turn asking. Returns (ask, why_not)."""
    if _already_retrying(payload):
        return False, "harness reports this turn is already a retry"
    n = asks_so_far(state, session)
    if n >= _MAX_ASKS:
        return False, f"already asked {n}x this turn, not looping"
    return True, ""


# --------------------------------------------------------------------------- #
# Per-harness output that makes the harness run one more turn
# --------------------------------------------------------------------------- #
def followup_output(source: str, prompt: str) -> dict | None:
    """The stdout JSON that asks `source` to submit `prompt` as another turn.

    Kiro is absent on purpose: its command hooks have no such field, so its
    installer writes a separate `agent`-action hook carrying the prompt instead.
    """
    if source == "cursor":
        return {"followup_message": prompt}
    if source in ("copilot", "antigravity", "codex"):
        return {"decision": "block", "reason": prompt}
    if source == "gemini":
        return {"decision": "deny", "reason": prompt}
    return None


SUPPORTED = ("cursor", "copilot", "gemini", "antigravity", "kiro", "codex")
