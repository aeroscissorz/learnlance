# learnlance — Multi-Harness Architecture

learnlance is no longer "a Claude Code hook." It's a **universal post-agent code
processing framework**: any AI coding harness (or plain git) feeds a normalized
event into one core engine.

```
   Claude Code ─┐
   Cursor ──────┤
   Copilot ─────┼──►  Adapter  ──►  CodeEvent  ──►  Core engine  ──►  Knowledge graph
   Gemini CLI ──┤    (per-tool)     (uniform)      (harness-blind)     + HTML + recap
   git commit ──┤
   generic ─────┘
```

## The abstraction

`events.CodeEvent` — the single shape the core understands:

```python
CodeEvent(source, event, cwd, session, files, blob, user_prompt, skip_reason)
```

- **adapters.py** — one `HookAdapter` per harness. `to_event(payload, state, cfg)`
  parses that tool's hook JSON into a `CodeEvent`, and may record a resume cursor
  in `state` (so the same turn/commit is never processed twice).
- **core.py** — `process_event(cfg, api_key, event)` runs the pipeline
  (insights → graph → HTML → recap). Knows nothing about any harness.
- **install.py** — one installer per harness that writes the tool's hook config.
- **hook.py** — thin entry: `detect(payload) → adapter.to_event → core.process_event`,
  in a detached worker so the host session never waits.

Adding a harness = **one adapter + one installer**. The core never changes.

## Implemented today

| Adapter | Trigger | Config written by `install` | Status |
|---|---|---|---|
| `claude` | Claude Code `Stop` hook | `~/.claude/settings.json` | ✅ shipping |
| `git` | `post-commit` (any editor/agent) | `.git/hooks/post-commit` (`install --git`) | ✅ shipping |
| `generic` | caller pre-assembles the payload | — (for tests / embedding) | ✅ |

The **git** adapter is the universal answer to "does it work in Cursor/Copilot/
Antigravity?" — it triggers on commit, so it captures code no matter what wrote it.

## Roadmap adapters (hook APIs verified Aug 2026)

These are ready to implement; formats confirmed from official docs. Each needs a
`*Adapter.to_event` + an installer that writes the config below.

### Cursor
- **Event:** `afterFileEdit` (also `postToolUse`). Config: `.cursor/hooks.json`
  (project) or `~/.cursor/hooks.json`. **Flat** entries: `{ "command": "...", "matcher": "...", "timeout": <sec> }` under `{"version":1,"hooks":{...}}`.
- **stdin:** `conversation_id`, `generation_id`, `hook_event_name`, `workspace_roots`;
  `afterFileEdit` gives `file_path` + `edits:[{old_string,new_string}]` (deltas, not a diff).
- **Feedback:** `additional_context` (snake_case, top-level).

### GitHub Copilot
- **Event:** `postToolUse`. Config: `.github/hooks/*.json` (repo; the only source the
  cloud agent reads) or `~/.copilot/hooks/`. Entries use **`bash`/`powershell`** command
  fields + `timeoutSec`, not a single `command`.
- **stdin:** camelCase (`sessionId`, `cwd`, …) or VS Code-compatible (`hook_event_name`,
  `session_id`, `cwd`); tool name/input/result per event.
- **Feedback:** `additionalContext` is documented but currently buggy (open issues);
  `modifiedResult` works. learnlance doesn't need feedback, so this doesn't block us.

### Gemini CLI
- **Event:** `AfterTool` (matcher targets edit/write tools). **Not** `PostToolUse`.
  Config: `settings.json` with **two-level** nesting (matcher group → inner `hooks[]`),
  `timeout` in **ms**.
- **stdin:** `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `tool_name`,
  `tool_input`, `tool_response`.
- **Feedback:** `hookSpecificOutput.additionalContext`.

### Cross-harness gotchas the adapters must absorb
- **No harness hands you a diff.** You reconstruct file paths/content from
  `tool_input`/`tool_response` (or Cursor's `edits[]`). Our `git` adapter is the only
  one that gets a true diff (from the commit).
- **Field casing differs:** `additionalContext` (Claude/Gemini/Copilot) vs
  `additional_context` (Cursor).
- **Config nesting differs:** matcher-group→`hooks[]` (Claude, Gemini) vs flat
  array (Cursor, Copilot).
- **Timeout units differ:** seconds (Claude, Cursor, Copilot `timeoutSec`) vs ms (Gemini).

## Explicitly out of scope
- **Feedback injection into the agent** (`additionalContext` → "fix this vuln"). The
  architecture *supports* it, but learnlance is a passive *learning* tool, not a
  linter/reviewer, so we don't emit it.
- **Filesystem watcher fallback** (`watch`): possible for editors with no hooks, but
  it can't tell AI edits from human edits and can't dedupe cleanly — the git hook is
  the better universal fallback.
