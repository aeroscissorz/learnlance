"""Harness adapters: translate each tool's hook payload into a `CodeEvent`.

Adding support for a new harness = adding one adapter here + one installer in
install.py. The core engine (core.py) is never touched.

THE RULE FOR THIS MODULE
    Adapters translate external agent protocols into `CodeEvent`s.
    They must not contain LearnLance's analysis logic.

An adapter may read a payload, buffer an edit, and describe what happened. It may
not decide what is worth learning, call a model, or touch the graph. That keeps
each harness's quirks isolated here and the engine free of them — and it's enforced
by `tests/test_adapters.py::test_adapters_do_not_import_analysis_or_storage_layers`,
which fails if this file starts importing `insights`, `graph`, `viz`, `core` or
`inchat`.

Every adapter learns from the code the agent *actually wrote*, read out of its
tool calls. None of them diff the working tree; see ARCHITECTURE.md for why.

Two shapes of harness show up here:

  * **Transcript-style** (Claude Code) hands over the whole turn in one call, so
    the adapter reads every edit out of the transcript and is done.

  * **Tool-at-a-time** (Kiro, Cursor, Copilot, Gemini) notifies you once per tool
    call and gives you nothing at the end. `BufferedToolAdapter` implements that
    pattern once: each edit is appended to a per-session buffer (pending.py), and
    the harness's end-of-turn event drains it into a single event.

Either way the core receives the same `[{file, code, action}]` material, built
into an identical blob by `transcript.build_input_blob`.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from abc import ABC, abstractmethod

from . import config, pending, transcript
from .events import CodeEvent


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _first(payload: dict, *names: str, default=None):
    """Read the first present, non-empty key. Harnesses disagree on casing:
    `session_id` vs `sessionId`, `cwd` vs `workspaceRoot`, and so on."""
    for n in names:
        if n in payload and payload[n] not in (None, ""):
            return payload[n]
    return default


class HookAdapter(ABC):
    source = "generic"

    @abstractmethod
    def to_event(self, payload: dict, state: dict, cfg: dict) -> CodeEvent | None:
        """Return a CodeEvent (possibly with skip_reason), or None if the payload
        isn't usable. May update `state` in place to track a resume cursor."""
        ...


# --------------------------------------------------------------------------- #
# Claude Code — transcript-style
# --------------------------------------------------------------------------- #
class ClaudeAdapter(HookAdapter):
    source = "claude"

    def to_event(self, payload, state, cfg):
        tpath = payload.get("transcript_path", "")
        session = payload.get("session_id") or "unknown"
        cwd = payload.get("cwd", "")
        if not tpath:
            return None

        since = state.get(session, {}).get("last_uuid")
        entries = transcript.read_entries(tpath)
        if not entries:
            return None
        gen = transcript.collect_new_generation(entries, since)
        # Advance the cursor so the same turn is never reprocessed.
        state[session] = {"last_uuid": gen["last_uuid"], "updated": _now()}

        edits = gen["edits"]
        files = sorted({e["file"] for e in edits})
        total = sum(len(e["code"]) for e in edits)
        if not edits or total < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, "after_agent_turn", cwd, session, files,
                             skip_reason="no substantive code this turn")
        blob = transcript.build_input_blob(gen, int(cfg.get("max_input_chars", 14000)))
        return CodeEvent(self.source, "after_agent_turn", cwd, session, files, blob,
                         user_prompt=gen.get("user_prompt", ""))


# --------------------------------------------------------------------------- #
# Shared base for tool-at-a-time harnesses
# --------------------------------------------------------------------------- #
# Where a file path might live in a tool's arguments, across harnesses.
_PATH_FIELDS = ("path", "file_path", "filePath", "notebook_path", "absolute_path")


def _is_learnlance_file(path: str) -> bool:
    """True for paths inside our own storage.

    In-chat mode has the agent write its analysis into `~/.learnlance/inbox/`.
    That write is itself a tool call, so without this filter we'd buffer our own
    output as though it were code the user wrote, and then analyze it next turn.
    """
    if not path:
        return False
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(str(config.HOME))
        )
    except Exception:
        return False


class BufferedToolAdapter(HookAdapter):
    """Buffer edits as the agent makes them; analyze when the turn ends.

    A subclass declares *what* its harness calls things; this class owns the
    two-phase flow. Subclasses set:

      `write_tools`  tool name -> (candidate content fields, action label)
      `end_events`   lowercased event names meaning "the turn is over"
      `session_keys` / `cwd_keys`  payload keys to read those from

    and may override `extract_edits` when the harness reports edits per *event*
    rather than per *tool* (Cursor does).
    """

    write_tools: dict[str, tuple[tuple[str, ...], str]] = {}
    end_events: tuple[str, ...] = ()
    session_keys: tuple[str, ...] = ("session_id", "sessionId")
    cwd_keys: tuple[str, ...] = ("cwd",)
    prompt_keys: tuple[str, ...] = ("prompt", "user_prompt", "userPrompt")

    # -- payload readers (override as needed) -------------------------------- #
    def read_session(self, payload: dict) -> str:
        return str(_first(payload, *self.session_keys, default="unknown"))

    def read_cwd(self, payload: dict) -> str:
        return _first(payload, *self.cwd_keys, default="") or ""

    def read_event_name(self, payload: dict) -> str:
        return str(_first(payload, "hook_event_name", "hookEventName", "trigger",
                          default="")).lower()

    def read_tool(self, payload: dict) -> str:
        return _first(payload, "tool_name", "toolName", default="") or ""

    def read_tool_input(self, payload: dict) -> dict:
        got = _first(payload, "tool_input", "toolInput", "toolArgs", "tool_args",
                     default={})
        return got if isinstance(got, dict) else {}

    def read_prompt(self, payload: dict) -> str:
        return _first(payload, *self.prompt_keys, default="") or ""

    # -- edit extraction ----------------------------------------------------- #
    def extract_edits(self, payload: dict, event_name: str) -> list[dict]:
        """Return `[{file, code, action}]` for a payload, or [] if it carries none.

        Default: look the tool up in `write_tools` and pull the first candidate
        content field that's present.
        """
        tool = self.read_tool(payload)
        spec = self.write_tools.get(tool)
        if spec is None:
            return []
        fields, action = spec
        args = self.read_tool_input(payload)
        code = _first(args, *fields, default="") or ""
        if not isinstance(code, str) or not code:
            return []
        path = _first(args, *_PATH_FIELDS, default="?")
        return [{"file": str(path), "code": code, "action": action}]

    # -- the two-phase flow -------------------------------------------------- #
    def to_event(self, payload, state, cfg):
        session = self.read_session(payload)
        cwd = self.read_cwd(payload)
        event_name = self.read_event_name(payload)

        edits = [e for e in self.extract_edits(payload, event_name)
                 if not _is_learnlance_file(e.get("file", ""))]
        if edits:
            return self._record(session, cwd, edits, self.read_prompt(payload))

        if event_name in self.end_events or not event_name:
            # Some harnesses only reveal the user's request on the end-of-turn
            # payload (Gemini's AfterAgent), so read it here too rather than
            # relying solely on what capture-time payloads happened to carry.
            return self._finish(cfg, session, cwd, self.read_prompt(payload))

        return CodeEvent(self.source, event_name or "tool_use", cwd, session,
                         skip_reason=f"{event_name or 'payload'} carried no new code")

    def _record(self, session, cwd, edits, prompt):
        n = 0
        for e in edits:
            n = pending.add_edit(session, e, cwd=cwd, prompt=prompt)
        files = sorted({e["file"] for e in edits})
        return CodeEvent(self.source, "tool_use", cwd, session, files,
                         skip_reason=f"buffered {len(edits)} edit(s), {n} pending")

    def _finish(self, cfg, session, cwd, prompt=""):
        # Read without deleting. core.process_event clears the buffer once the
        # concepts are actually in the graph, so a turn that can't be analyzed
        # (no LLM installed, CLI failure) is retried rather than lost.
        buffered = pending.load(session)
        edits = buffered.get("edits") or []
        cwd = cwd or buffered.get("cwd", "")
        prompt = prompt or buffered.get("prompt", "")
        files = sorted({e["file"] for e in edits})

        if not edits:
            return CodeEvent(self.source, "after_agent_turn", cwd, session,
                             skip_reason="no code edits captured this session")

        total = sum(len(e.get("code", "")) for e in edits)
        if total < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, "after_agent_turn", cwd, session, files,
                             skip_reason="no substantive code this session")

        # Identical blob format to the Claude path.
        gen = {"edits": edits, "user_prompt": prompt}
        blob = transcript.build_input_blob(gen, int(cfg.get("max_input_chars", 14000)))
        return CodeEvent(self.source, "after_agent_turn", cwd, session, files, blob,
                         user_prompt=prompt)


# --------------------------------------------------------------------------- #
# Kiro
# --------------------------------------------------------------------------- #
class KiroAdapter(BufferedToolAdapter):
    """Kiro: `PostToolUse` on the write tools, drained on `Stop`."""

    source = "kiro"
    write_tools = {
        "fs_write": (("text",), "created"),
        "str_replace": (("newStr",), "edited"),
        "fs_append": (("text",), "appended"),
    }
    end_events = ("stop",)
    cwd_keys = ("cwd", "workspaceRoot", "workspace_root")


# --------------------------------------------------------------------------- #
# Cursor
# --------------------------------------------------------------------------- #
class CursorAdapter(BufferedToolAdapter):
    """Cursor: `afterFileEdit`, drained on `stop`.

    `afterFileEdit` exists precisely for "accounting of agent-written code". It
    reports the edit as deltas — `file_path` plus `edits: [{old_string,
    new_string}]` — so the edits come off the event, not off a tool name.
    """

    source = "cursor"
    end_events = ("stop",)
    session_keys = ("conversation_id", "conversationId", "session_id", "sessionId")

    def read_cwd(self, payload):
        # Cursor reports a list of workspace roots (multiroot workspaces).
        roots = _first(payload, "workspace_roots", "workspaceRoots", default=None)
        if isinstance(roots, list) and roots:
            return str(roots[0])
        return _first(payload, "cwd", default="") or ""

    def extract_edits(self, payload, event_name):
        if event_name not in ("afterfileedit", "aftertabfileedit"):
            return super().extract_edits(payload, event_name)
        path = str(_first(payload, *_PATH_FIELDS, default="?"))
        out = []
        for e in payload.get("edits") or []:
            if not isinstance(e, dict):
                continue
            code = e.get("new_string") or e.get("newString") or e.get("new_line") or ""
            if isinstance(code, str) and code:
                out.append({"file": path, "code": code, "action": "edited"})
        return out


# --------------------------------------------------------------------------- #
# GitHub Copilot
# --------------------------------------------------------------------------- #
class CopilotAdapter(BufferedToolAdapter):
    """GitHub Copilot, covering three surfaces that share one config format:
    Copilot CLI, the Copilot cloud agent, and Copilot Chat in VS Code.

    They're one adapter because they read the same `.github/hooks/*.json` and
    accept the same payloads — but each reports tool names differently, so the
    table below is the union of all three:

      Copilot CLI runtime   `create`, `edit`, `str_replace_editor`, `apply_patch`
      Claude-format config  `Write`, `Edit`  (PascalCase events report these)
      VS Code               `create_file`, `replace_string_in_file`, `editFiles`, …

    Registering a tool we can't read is harmless — the lookup misses and the call
    is skipped — but missing one means silently capturing nothing, which is why
    this errs toward breadth. That matters most on VS Code, which ignores hook
    matchers entirely and so fires us for every tool call.
    """

    source = "copilot"
    _CREATE = ("content", "text", "file_text", "code")
    _EDIT = ("new_string", "newStr", "newString", "content", "code")
    write_tools = {
        # Copilot CLI / cloud agent
        "create": (_CREATE, "created"),
        "edit": (_EDIT, "edited"),
        "str_replace_editor": (_EDIT, "edited"),
        "apply_patch": (("patch", "input", "content"), "edited"),
        # Claude tool names, reported when the event is configured PascalCase
        "Write": (_CREATE, "created"),
        "Edit": (_EDIT, "edited"),
        "MultiEdit": (_EDIT, "edited"),
        # VS Code Copilot Chat
        "create_file": (_CREATE, "created"),
        "createFile": (_CREATE, "created"),
        "replace_string_in_file": (_EDIT, "edited"),
        "insert_edit_into_file": (_EDIT, "edited"),
        "editFiles": (_EDIT, "edited"),
    }
    # `Stop` is VS Code's end-of-turn event and Copilot's PascalCase alias for
    # `agentStop`; both spellings are accepted so one registration serves both.
    end_events = ("stop", "agentstop")


# --------------------------------------------------------------------------- #
# Gemini CLI
# --------------------------------------------------------------------------- #
class GeminiAdapter(BufferedToolAdapter):
    """Gemini CLI: `AfterTool` on the write tools, drained on `AfterAgent`.

    `AfterAgent` fires once per turn after the final response, which matches the
    per-turn granularity of Claude's `Stop`.

    Note: Google is retiring Gemini CLI in favour of Antigravity CLI, so this
    adapter is on a deprecation path — see AntigravityAdapter below.
    """

    source = "gemini"
    write_tools = {
        "write_file": (("content", "text"), "created"),
        "replace": (("new_string", "newString"), "edited"),
        "edit": (("new_string", "newString", "content"), "edited"),
    }
    end_events = ("afteragent", "sessionend")


# --------------------------------------------------------------------------- #
# Google Antigravity
# --------------------------------------------------------------------------- #
class AntigravityAdapter(BufferedToolAdapter):
    """Antigravity: `PostToolUse` on the write tools, drained on `Stop`.

    Antigravity borrows Claude's event names (`PreToolUse`/`PostToolUse`/`Stop`)
    but keeps Gemini's tool names and Gemini's two-level config nesting — it's the
    successor to Gemini CLI. Tool arguments arrive under `toolCall.args` in some
    versions, so that shape is unwrapped as well.
    """

    source = "antigravity"
    write_tools = {
        "write_file": (("content", "text"), "created"),
        "replace": (("new_string", "newString"), "edited"),
        "edit": (("new_string", "newString", "content"), "edited"),
        "create_file": (("content", "text"), "created"),
    }
    end_events = ("stop", "afteragent")

    def read_tool(self, payload):
        call = payload.get("toolCall")
        if isinstance(call, dict) and call.get("name"):
            return call["name"]
        return super().read_tool(payload)

    def read_tool_input(self, payload):
        call = payload.get("toolCall")
        if isinstance(call, dict) and isinstance(call.get("args"), dict):
            return call["args"]
        return super().read_tool_input(payload)


# --------------------------------------------------------------------------- #
# git (universal — works with ANY editor/agent, triggers on commit)
# --------------------------------------------------------------------------- #
def _git(cwd: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=30)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


class GitAdapter(HookAdapter):
    """The one adapter that legitimately works from a diff — a commit is all it
    has. Use it as the fallback for harnesses with no hook API."""

    source = "git"

    def to_event(self, payload, state, cfg):
        cwd = payload.get("cwd", "")
        commit = payload.get("commit", "HEAD")
        if not cwd:
            return None
        sha = _git(cwd, "rev-parse", commit).strip()
        if not sha:
            return None
        session = f"git:{cwd}"
        since = state.get(session, {}).get("last_commit")
        state[session] = {"last_commit": sha, "updated": _now()}
        if since == sha:
            return CodeEvent(self.source, "after_commit", cwd, session,
                             skip_reason="commit already processed")

        files = [f for f in _git(cwd, "diff-tree", "--no-commit-id",
                                 "--name-only", "-r", sha).splitlines() if f.strip()]
        diff = _git(cwd, "show", sha, "--unified=0", "--format=", "--no-color")
        added = [ln[1:] for ln in diff.splitlines()
                 if ln.startswith("+") and not ln.startswith("+++")]
        msg = _git(cwd, "log", "-1", "--format=%s", sha).strip()

        code = "\n".join(added)
        if len(code) < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, "after_commit", cwd, session, files,
                             skip_reason="no substantive code in commit")
        header = f"COMMIT: {msg}\n\n" if msg else ""
        blob = (header + code)[: int(cfg.get("max_input_chars", 14000))]
        return CodeEvent(self.source, "after_commit", cwd, session, files, blob,
                         user_prompt=msg)


# --------------------------------------------------------------------------- #
# generic passthrough — caller pre-assembles the event
# --------------------------------------------------------------------------- #
class GenericAdapter(HookAdapter):
    source = "generic"

    def to_event(self, payload, state, cfg):
        blob = payload.get("blob", "")
        files = payload.get("files", []) or []
        cwd = payload.get("cwd", "")
        session = payload.get("session") or "generic"
        if len(blob) < int(cfg.get("min_chars", 40)):
            return CodeEvent(self.source, payload.get("event", "generic"), cwd,
                             session, files, skip_reason="blob too small")
        return CodeEvent(self.source, payload.get("event", "generic"), cwd,
                         session, files, blob, user_prompt=payload.get("user_prompt", ""))


_ADAPTERS = {
    "claude": ClaudeAdapter,
    "kiro": KiroAdapter,
    "cursor": CursorAdapter,
    "copilot": CopilotAdapter,
    "gemini": GeminiAdapter,
    "antigravity": AntigravityAdapter,
    "git": GitAdapter,
    "generic": GenericAdapter,
}

# Harnesses whose installer pins `--source`, i.e. everything buffered.
BUFFERED_SOURCES = ("kiro", "cursor", "copilot", "gemini", "antigravity")


def write_tools_for(source: str) -> tuple[str, ...]:
    """Tool names a harness should notify us about. Installers build their hook
    matcher from this, so the matcher can't drift from what we can read."""
    cls = _ADAPTERS.get(source)
    tools = getattr(cls, "write_tools", None) or {}
    return tuple(tools)


def detect(payload: dict) -> HookAdapter | None:
    """Pick the right adapter for an incoming hook payload."""
    src = payload.get("source")
    if src in _ADAPTERS:
        return _ADAPTERS[src]()
    # Claude Code payloads are recognized by their transcript_path field.
    if payload.get("transcript_path"):
        return ClaudeAdapter()
    return None
