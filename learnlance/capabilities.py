"""What each harness can actually do, and how sure we are.

Every integration here was written against vendor documentation. None of it has
been confirmed against a running harness. Those are very different claims, and
conflating them is how a tool ends up reporting a confident tick next to something
that silently does nothing — which is precisely the failure mode of every bug this
codebase has had.

So each capability carries a `Confidence`, and `learnlance doctor` reports two
separate things:

  * **configured** — we wrote the hook config. Checked on disk. Certain.
  * **observed**   — that hook has actually fired at least once. Read from the log.

Only the second is evidence. A capability stays `DOCUMENTED` until a real turn
proves it, at which point the log says so and `doctor` upgrades it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from . import config


class Confidence(str, Enum):
    #: Implemented from the vendor's own documentation, never run for real.
    DOCUMENTED = "documented"
    #: Docs describe it, but there are credible reports it doesn't work.
    DISPUTED = "disputed"
    #: The harness has no mechanism for this at all.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Capability:
    #: Events the hook config registers.
    hook: str
    #: How the agent's own model is reached, or "" when it can't be.
    in_chat: str
    hook_confidence: Confidence = Confidence.DOCUMENTED
    in_chat_confidence: Confidence = Confidence.DOCUMENTED
    note: str = ""


# Sources for every line here are in ARCHITECTURE.md's per-harness reference.
CAPABILITIES: dict[str, Capability] = {
    "claude": Capability(
        hook="Stop (reads the session transcript)",
        in_chat="",
        in_chat_confidence=Confidence.UNSUPPORTED,
        note="Transcript-style: one call yields the whole turn. No documented way "
             "for a hook to submit a follow-up turn, so in-chat isn't offered — "
             "the `claude` CLI is the backend instead.",
    ),
    "kiro": Capability(
        hook="PostToolUse + Stop",
        in_chat="Stop hook with the `agent` action type",
        note="The only harness whose in-chat prompt must be static, since the "
             "`agent` action carries a fixed string from the config. Guarded by a "
             "marker file so it can't re-fire on the turn it created. No loop "
             "signal is exposed, so our own guard is the only one.",
    ),
    "cursor": Capability(
        hook="afterFileEdit + stop",
        in_chat="stop -> {\"followup_message\": ...}",
        note="afterFileEdit is documented for 'accounting of agent-written code'. "
             "loop_count/loop_limit back up our own one-ask cap.",
    ),
    "copilot": Capability(
        hook="PostToolUse + Stop",
        in_chat="Stop -> {\"decision\": \"block\", \"reason\": ...}",
        note="Covers Copilot CLI, the cloud agent, and VS Code Copilot Chat — one "
             "config file for all three. Copilot caps runaway blocks at 8.",
    ),
    "gemini": Capability(
        hook="AfterTool + AfterAgent",
        in_chat="AfterAgent -> {\"decision\": \"deny\", \"reason\": ...}",
        note="Google is retiring Gemini CLI in favour of Antigravity CLI, so this "
             "is on a deprecation path. stop_hook_active backs up our cap.",
    ),
    "antigravity": Capability(
        hook="PostToolUse + Stop",
        in_chat="Stop -> {\"decision\": \"block\", \"reason\": ...}",
        hook_confidence=Confidence.DISPUTED,
        in_chat_confidence=Confidence.DISPUTED,
        note="Open reports say Stop/PostToolUse never fire in the Antigravity IDE, "
             "though they do in Antigravity CLI. Its `enabled` flag also defaults "
             "to false. The least confirmed of the six.",
    ),
    "git": Capability(
        hook="post-commit",
        in_chat="",
        in_chat_confidence=Confidence.UNSUPPORTED,
        note="The universal fallback. A commit hook has no agent to ask, and works "
             "from a diff rather than tool calls — so it can't tell your edits from "
             "the agent's. Use it only where a harness exposes no hooks.",
    ),
}


def supports_in_chat(source: str) -> bool:
    cap = CAPABILITIES.get(source)
    return bool(cap and cap.in_chat
                and cap.in_chat_confidence is not Confidence.UNSUPPORTED)


# --------------------------------------------------------------------------- #
# Evidence: what has actually happened on this machine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Evidence:
    """Read from the log, which every hook invocation writes to."""
    fired: bool = False      # a hook for this harness ran
    learned: bool = False    # an analysis completed and reached the graph
    asked: bool = False      # in-chat: we asked the agent to analyze
    answered: bool = False   # in-chat: the agent's answer was ingested

    @property
    def in_chat_works(self) -> bool:
        """Only true once a full ask -> answer cycle has been seen."""
        return self.asked and self.answered


def _log_text() -> str:
    try:
        return config.LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def evidence(source: str, log: str | None = None) -> Evidence:
    """What the log shows this harness has actually done.

    Hook lines are tagged `<source>:<session>` by hook.py and core.py, which is
    what makes this derivable without a separate telemetry file.
    """
    text = _log_text() if log is None else log
    if not text:
        return Evidence()

    tag = re.compile(rf"\b{re.escape(source)}:\S*\s*:?")
    fired = learned = asked = answered = False
    for line in text.splitlines():
        if not tag.search(line):
            continue
        fired = True
        if "learned/reinforced" in line:
            learned = True
        if "asking the agent" in line or "opened an analyze request" in line:
            asked = True
        if "ingested" in line and "from the agent" in line:
            answered = True
    return Evidence(fired, learned, asked, answered)


def status(source: str, configured: bool, in_chat_mode: bool,
           log: str | None = None) -> str:
    """A one-word status honest about which of the two claims is being made."""
    if not configured:
        return "not configured"
    ev = evidence(source, log)
    cap = CAPABILITIES.get(source)
    disputed = cap and cap.hook_confidence is Confidence.DISPUTED

    if in_chat_mode:
        if ev.in_chat_works:
            return "working (in-chat verified)"
        if ev.asked:
            return "asked, no answer yet"
    if ev.learned:
        return "working (verified)"
    if ev.fired:
        return "hook fired, nothing learned yet"
    return "configured, unverified" + (" (reports say it may not fire)"
                                       if disputed else "")
