# learnlance — Multi-Harness Architecture

learnlance is no longer "a Claude Code hook." It's a **universal post-agent code
processing framework**: any AI coding harness (or plain git) feeds a normalized
event into one core engine.

```
   Claude Code ─┐
   Kiro IDE ────┤
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

> **The layering rule.** Adapters translate external agent protocols into
> `CodeEvent`s. They must not contain LearnLance's analysis logic — no model calls,
> no graph writes, no judgement about what's worth learning. This is enforced, not
> just documented: `test_adapters_do_not_import_analysis_or_storage_layers` parses
> `adapters.py` and fails if it imports `insights`, `graph`, `viz`, `core` or
> `inchat`.

## Capability matrix — and what "supported" actually means

Every integration in this repo was written from vendor documentation. **None has
been confirmed against a running harness.** Those are different claims, and
collapsing them into one tick is how a tool ends up confidently reporting a feature
that silently does nothing — the shape of every bug this codebase has had.

`capabilities.py` therefore records a confidence per capability, and `doctor`
reports two separate things: **configured** (the hook config is on disk — certain)
and **observed** (that hook has actually fired, read from the log — evidence).

| Harness | Hook | In-chat mechanism | Confidence |
|---|---|---|---|
| Claude Code | `Stop` (transcript) | — no follow-up field; uses the `claude` CLI | documented |
| Kiro | `PostToolUse` + `Stop` | `Stop` hook, `agent` action type | documented |
| Cursor | `afterFileEdit` + `stop` | `stop` → `followup_message` | documented |
| Copilot / VS Code | `PostToolUse` + `Stop` | `Stop` → `decision: "block"` | documented |
| Gemini CLI | `AfterTool` + `AfterAgent` | `AfterAgent` → `decision: "deny"` | documented, deprecating |
| Antigravity | `PostToolUse` + `Stop` | `Stop` → `decision: "block"` | **disputed** |
| git | `post-commit` | — no agent to ask | documented |

`disputed` means the docs describe it but there are credible reports it doesn't
work: Antigravity's `Stop`/`PostToolUse` reportedly never fire in the IDE, though
they do in Antigravity CLI.

Statuses `doctor` can report, in increasing order of evidence:

```
not configured
configured, unverified            hook written, harness has never invoked us
configured, unverified (reports say it may not fire)
hook fired, nothing learned yet   we were called, no analysis completed
asked, no answer yet              in-chat: prompt sent, nothing came back
working (verified)                an analysis reached the graph
working (in-chat verified)        a full ask -> answer cycle was seen
```

### Why there's no `ChatBridge` class per harness

The obvious next abstraction is a `chat/` package with one class per harness. It
isn't there because `inchat.followup_output(source, prompt)` already *is* that
bridge, and each implementation is one dict literal — the "class" would hold no
state and no behaviour beyond the mapping. When a harness needs genuinely different
mechanics the seam already exists: Kiro doesn't fit the follow-up-field model at
all, so it returns `None` and its installer writes a separate `agent`-action hook.

What no abstraction can fix: **there is no way for external Python to reach an
IDE's private chat session.** The host must expose a mechanism. All five here work
because each publishes an end-of-turn field that submits a prompt — not because
learnlance found a way in.

## Tests

`pytest` — 185 tests, no network, each against an isolated `LEARNLANCE_HOME`.

The suite exists to pin down failures that were all silent in the same way: the
hook ran, exited 0, captured nothing, and left `doctor` looking healthy. Each file
below carries the specific regression it locks down, in its module docstring.

| File | Locks down |
|---|---|
| `test_events.py` | The `CodeEvent` contract, including worker JSON round-trip |
| `test_adapters.py` | Routing, per-harness capture, and the layering rule |
| `test_buffer.py` | Read-then-commit: a turn's work is never lost |
| `test_cli.py` | `--source`/`--in-chat` forwarding, backend resolution |
| `test_setup.py` | `setup` re-runnable, per-project, and merges shared configs |
| `test_vs_code_copilot.py` | The three ways VS Code differs from Copilot CLI |
| `test_in_chat.py` | In-chat mode, and that the ask loop terminates |
| `test_capabilities.py` | The capability table, evidence parsing, and sandboxing |

Two properties are worth calling out because they'd be easy to drop:

- **Real subprocesses, not mocks, for the LLM boundary.** `conftest.fake_llm` is an
  actual executable. The quoting bug that silently disabled every backend lived in
  argv construction, which a monkeypatched `insights.generate` would never touch.
- **Every "swallowed error" path asserts the observable consequence.** Two bugs this
  codebase had were caused by a defensive `try/except` hiding a real failure, so the
  tests check that in-chat mode actually applies, that a failing CLI actually keeps
  the buffer, and that malformed model output actually raises.
- **The `home` fixture redirects `Path.home()`, not just `config.HOME`.** Some
  installers target user-level config — Gemini falls back to
  `~/.gemini/settings.json` — via `Path.home()`. Before that patch the suite
  rewrote the developer's real Gemini settings on every run.
  `test_capabilities.py` asserts the sandbox holds.

## Implemented today

Every adapter learns from the code the agent **actually wrote**, read out of its
tool calls. Only the `git` fallback works from a diff, and only because a commit
is all it has.

| Adapter | Captures on | Analyzes on | Config written by `install` |
|---|---|---|---|
| `claude` | (whole transcript) | `Stop` | `~/.claude/settings.json` |
| `kiro` | `PostToolUse` | `Stop` | `.kiro/hooks/learnlance.json` (`--kiro`) |
| `cursor` | `afterFileEdit` | `stop` | `.cursor/hooks.json` (`--cursor`) |
| `copilot` | `PostToolUse` | `Stop` | `.github/hooks/learnlance.json` (`--copilot`) |
| `gemini` | `AfterTool` | `AfterAgent` | `.gemini/settings.json` (`--gemini`) |
| `antigravity` | `PostToolUse` | `Stop` | `.agents/hooks.json` (`--antigravity`) |
| `git` | — | `post-commit` | `.git/hooks/post-commit` (`--git`) |
| `generic` | — | caller assembles it | — (tests / embedding) |

The `copilot` row covers **three surfaces**: Copilot CLI, the Copilot cloud agent,
and Copilot Chat in VS Code. They read the same file and accept the same payloads,
so they're one adapter — see the Copilot section below for why that's one
registration rather than two.

## Getting installed

`pip install` cannot attach anything to an IDE. Wheels don't execute code on
install (deliberately — it's a supply-chain attack vector), and even if they did,
`pip` may be running in a venv, a Docker layer or CI, with no way to know which
project you meant. Four of the six hooks are per-project, so there is nothing
sensible to configure at install time.

So the contract is two commands:

```
pip install learnlance-univ
learnlance setup            # or: learnlance setup --in-chat
```

The distribution is `learnlance-univ`; the import package and the command are both
`learnlance`, as with `pillow`/`PIL`. Don't install the older `learnlance`
distribution alongside it — both provide the same `learnlance` module and console
script, so whichever is installed second silently overwrites the other.

`setup` detects the agents you have, writes their hook configs for *this* project,
and reports where the analysis will come from. Run it again in each project you
want tracked.

Three related paths, which are easy to confuse:

- **`setup`** — explicit, always acts, per project. What users should run.
- **auto-setup** — the same detection, run implicitly on first use, gated by a
  once-ever flag so it never writes into every directory you happen to invoke
  learnlance from. That gate is exactly why `setup` must be able to bypass it.
- **`install --<harness>`** — configure one harness by hand, skipping detection.

Detection looks for each tool's own config directory. A directory in `$HOME`
(`~/.kiro`, `~/.cursor`) means *this user has this tool*, so it gets configured in
every project `setup` runs in — that's what "works whichever agent you're using"
requires. It's the weakest part of the design: a config directory proves the tool
was once present, not that it's installed now, which is why `setup` says "found its
config dir" rather than claiming the tool is live.

Two markers deserve spelling out because they'd otherwise be missed:

- **Cursor** keeps its user config in a platform-specific location — `~/.cursor`,
  `~/Library/Application Support/Cursor` (macOS), `~/.config/cursor` (Linux), or
  `%APPDATA%\Cursor` (Windows). Detection checks all of them, not just `~/.cursor`.
- **Copilot** is one harness entry but two surfaces: Copilot CLI leaves
  `~/.copilot`, while VS Code Copilot Chat leaves an extension directory under
  `~/.vscode/extensions/github.copilot*`. Both count, so a VS Code-only user is
  still detected and their hook installed. `doctor` names whichever surface(s) it
  found.

The **git** adapter stays as the fallback for anything with no hook API. It
triggers on commit, so it captures code no matter what wrote it.

## Two shapes of harness

This is the main thing to understand before writing an adapter.

**Transcript-style (Claude Code).** One hook call hands you the whole turn. The
adapter reads the transcript, pulls every `Write`/`Edit`/`MultiEdit` tool call out
of it, and returns a finished event. Stateless and simple.

**Tool-at-a-time (Kiro, Cursor, Copilot, Gemini).** The harness notifies you once
per tool call and gives you nothing at the end. A single edit isn't worth
analyzing, so the adapter works in two phases:

```
capture event (a write tool)  ──►  buffer the edit      ──►  skip
capture event (a write tool)  ──►  buffer the edit      ──►  skip
end-of-turn event             ──►  drain buffer ─► blob ──►  analyze
```

`pending.py` holds that buffer — one small JSON file per session under
`~/.learnlance/pending/`, drained and deleted when the end-of-turn event fires.
Both shapes therefore hand the core the same `[{file, code, action}]` material, and
both build their blob with `transcript.build_input_blob`, so the LLM sees an
identical format regardless of which tool produced the code.

`BufferedToolAdapter` implements that flow once. A subclass only declares what its
harness calls things:

```python
class GeminiAdapter(BufferedToolAdapter):
    source = "gemini"
    write_tools = {                      # tool -> (content fields, action label)
        "write_file": (("content", "text"), "created"),
        "replace":    (("new_string", "newString"), "edited"),
    }
    end_events = ("afteragent", "sessionend")
```

Content fields are a tuple of *candidates* because not every harness documents its
tool arguments precisely; an unexpected parameter name degrades to "no code in
this call" rather than a crash. Override `extract_edits` when a harness reports
edits per *event* instead of per *tool* — Cursor's `afterFileEdit` carries
`edits: [{old_string, new_string}]`, so it's the one that does.

**Why not `git diff`?** Because it answers a different question. A diff shows what
changed in the *files*; we want what the *agent wrote*. A diff mixes in the user's
own edits, misses code that was written and then refactored within the same
session, and reports nothing at all until something is staged or committed. The
tool calls are the ground truth, so we read those.

### Consequences worth knowing
- **Capture must not depend on the LLM.** Buffering happens inline in
  `run_hook`, with no backend check, so edits keep accumulating even before
  `claude` is reachable. The backend is checked in `core.process_event`, right
  where it's used. Gating capture on it silently loses a session's work.
- **Only analysis is backgrounded.** `run_hook` builds the event inline (cheap,
  and it can't be lost to a worker that never got scheduled) and hands off to a
  detached worker only when there's something to analyze. Spawning a process per
  `fs_write` would cost ~100 ms of startup for microseconds of work.
- **`--source` pins the adapter.** Kiro's stdin JSON is passed through untouched
  and `learnlance hook --source kiro` says which adapter to use. Reshaping JSON
  inside a one-line shell command is fragile and platform-specific; a flag isn't.
  Anything launching the hook must forward it — the standalone
  `learnlance_hook.py` launcher parses it explicitly for this reason.
- **The tool list lives in one place.** `adapters.KIRO_WRITE_TOOLS` maps each
  write tool to the field holding its new content, and `install.py` builds the
  hook's `matcher` from those same keys, so the tools we ask to be notified about
  can't drift from the ones we know how to read.

## Where the analysis happens

Naming a concept from code is a judgement call, so it needs a model. There are two
ways to reach one, and they trade off differently.

### CLI mode (default)

Shell out to an LLM CLI the user is already logged into — `insights.resolve_backend`
auto-detects `claude`, `gemini`, `copilot`, `cursor-agent` or `ollama`, and
`--llm-cmd` accepts anything else that reads a prompt on stdin. Invisible, works
identically on every harness, and can run in a detached worker so the session never
waits. Costs the user a separate install.

### In-chat mode (`install --in-chat`)

Ask the agent that just wrote the code to name the concepts itself. No CLI, no
second subscription. Every harness exposes an end-of-turn field that submits a
prompt as another turn:

| Harness | Field on the end-of-turn hook | Its own loop guard |
|---|---|---|
| Cursor | `{"followup_message": …}` | `loop_count` / `loop_limit` (default 5) |
| Copilot | `{"decision":"block","reason":…}` | 8-continuation cap, `stop_hook_active` |
| Gemini | `{"decision":"deny","reason":…}` | `stop_hook_active` |
| Antigravity | `{"decision":"block","reason":…}` | (undocumented) |
| Kiro | — command hooks have no such field | (none) |

The agent writes its answer as JSON into `~/.learnlance/inbox/`, and the *next*
end-of-turn hook ingests it and merges it into the graph — so a turn's concepts
land one turn later than in CLI mode.

**Why the inbox is a directory.** Kiro can only reach the model through its `agent`
action, which carries a *static* prompt from the config file — it can't be told the
session id, so it can't be given a per-session filename. A directory the agent
names files in freely is the only shape that works for all five.

**Termination is the thing to get right.** An end-of-turn hook that asks for another
turn will loop forever if the guard is wrong, and that wedges a real session. Three
defences, in order:

1. `inchat.should_ask` refuses if the harness reports this turn is already a retry
   (`stop_hook_active`, `loop_count`) — cooperating with each vendor's guard rather
   than racing it.
2. Our own cap of **one** ask per turn, tracked in `state.json`.
3. For Kiro's static prompt, where neither applies: the prompt is written as a
   conditional on a marker file (`~/.learnlance/analyze-me`) that only the command
   hook creates. That converts "hope the model notices it's repeating" into a
   deterministic check.

**Two consequences worth knowing.** In in-chat mode the hook's stdout is a JSON
channel to the harness, so nothing else may be written to it — `merge_result` takes
`announce=False` on that path, and `core._say` never raises regardless. And the
agent writing into the inbox is itself a tool call, so `adapters._is_learnlance_file`
drops any edit inside our own storage, or we'd analyze our own output next turn.

### Why not the harness's model directly, without a turn?

Because hook APIs hand your script data and take a *decision* back; none exposes an
inference endpoint. Cursor and Copilot do have `type: "prompt"` hooks, but Cursor's
returns only `{ok, reason}` and Copilot's fire on `sessionStart` — neither can
return a concept graph. The follow-up-turn route above is the only general one.

## Per-harness reference (verified against vendor docs, Aug 2026)

### Kiro
- **Config:** `.kiro/hooks/learnlance.json`. `{"version":"v1","hooks":[{name,
  trigger, matcher, action:{type,command}}]}`.
- **Capture:** `PostToolUse`, matcher `fs_write|str_replace|fs_append`.
  New content is in `text` / `newStr`.
- **Analyze:** `Stop`. **stdin:** `session_id`, `cwd` (also accepts camelCase).

### Cursor
- **Config:** `.cursor/hooks.json` (project) or `~/.cursor/hooks.json`. **Flat**
  entries under `{"version":1,"hooks":{<event>:[…]}}`; `timeout` in **seconds**.
  A shared file — merge, don't overwrite.
- **Capture:** `afterFileEdit`, which exists for "accounting of agent-written
  code". Gives `file_path` + `edits:[{old_string,new_string}]` — deltas, so one
  event can yield several edits.
- **Analyze:** `stop`. **stdin:** `conversation_id` (the session), `generation_id`,
  `workspace_roots` (a **list** — no `cwd`), `hook_event_name`, `transcript_path`.

### GitHub Copilot — CLI, cloud agent, and VS Code Copilot Chat
All three read `.github/hooks/*.json` and accept the same payloads, so one adapter
and one registration serve them. Getting that registration right is fiddly:

- **Event names are PascalCase deliberately.** VS Code's end-of-turn event is
  `Stop`; Copilot CLI's is `agentStop`. VS Code maps camelCase config to
  PascalCase, so `agentStop` becomes `AgentStop` — which is not a VS Code event,
  and the analyze hook silently never fires there. PascalCase is native to VS Code
  and documented by Copilot as its VS Code-compatible format, so it works on both.
  Registering *both* spellings is the obvious-looking fix and is wrong: Copilot CLI
  would then fire twice per event.
- **stdin follows the event casing.** PascalCase gives VS Code-compatible
  snake_case (`session_id`, `tool_name`, `tool_input`); camelCase gives
  `sessionId`, `toolName`, `toolArgs`. `_first` accepts either.
- **Tool names differ per surface**, so `write_tools` is the union of all three:
  `create`/`edit`/`str_replace_editor`/`apply_patch` (CLI), `Write`/`Edit`
  (reported under Claude's names in PascalCase mode), and
  `create_file`/`createFile`/`replace_string_in_file`/`insert_edit_into_file`/
  `editFiles` (VS Code). Tool *input* props are camelCase in VS Code
  (`tool_input.filePath`) and snake_case in Claude's format.
- **VS Code ignores matchers entirely.** The hook fires for every tool call
  regardless of what the matcher says, so the adapter's own table has to do the
  filtering. It already did, which is why this degraded to "captures nothing"
  rather than crashing.
- **Config fields:** `timeoutSec`, default 30. Entries take `bash` / `powershell`,
  or `command` as a cross-platform fallback that Copilot copies to both and VS Code
  falls back to — we use `command`, so one entry covers every OS.
- **Also worth knowing:** VS Code reads `.claude/settings.json` by default too, so
  a Claude Code hook may already be firing there — with Claude's tool names, which
  this adapter knows.

### Gemini CLI
- **Config:** `.gemini/settings.json` (project) or `~/.gemini/settings.json`, with
  **two-level** nesting: event → `[{matcher, hooks:[{type,command,…}]}]`.
  `timeout` in **milliseconds**, default 60000. A shared file — merge.
- **Capture:** `AfterTool` (**not** `PostToolUse`), matcher is a regex on the tool
  name. Write tools are `write_file` and `replace`.
- **Analyze:** `AfterAgent`, which fires once per turn after the final response —
  the same granularity as Claude's `Stop`. `SessionEnd` exists but the CLI does
  not wait for it, so it's a poor place to do work.
- **stdin:** `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
  `tool_name`, `tool_input`, `tool_response`. `AfterAgent` adds `prompt` and
  `prompt_response`.
- **Gotcha:** the user's request only appears on `AfterAgent`, never on the
  `AfterTool` calls — so the end-of-turn payload has to be consulted for it, not
  just the buffer.
- **Deprecation:** Google is retiring Gemini CLI in favour of Antigravity CLI. This
  adapter still works, but new users land on Antigravity.

### Antigravity
- **Config:** `.agents/hooks.json` (workspace) or `~/.gemini/config/hooks.json`
  (global — it's Gemini CLI's successor). Top level is a map of **named blocks**,
  not events: `{"learnlance": {"enabled": true, "PostToolUse": [...], "Stop": [...]}}`.
  Inside a block the nesting is Gemini's (`[{matcher, hooks:[…]}]`) but the event
  names are Claude's.
- **`enabled` defaults to false.** A perfectly shaped config sits inert until it's
  set, which is the most likely reason a correct-looking install does nothing. Our
  installer always writes `enabled: true`.
- **Capture:** `PostToolUse` on Gemini's tool names (`write_file`, `replace`).
  Arguments arrive under `toolCall.args` in some versions, hence the adapter's
  `read_tool`/`read_tool_input` overrides.
- **Analyze:** `Stop`. Decision field is `decision`/`reason`, not Cursor's
  `permissionDecision`.
- **⚠ Verify before trusting it:** there are open reports that `Stop` and
  `PostToolUse` never fire in the **Antigravity IDE** (they do in Antigravity CLI),
  and that hook config changes need a full application restart rather than a new
  chat. This adapter is written to the documented contract but is the least
  confirmed of the six. `learnlance doctor` after a real turn is the check.

### Cross-harness gotchas the adapters must absorb
- **No harness hands you a diff, and you don't want one.** You reconstruct file
  paths/content from `tool_input`/`tool_response` (or Cursor's `edits[]`) — see
  "Why not `git diff`?" above. The `git` adapter is the only one that works from a
  real diff, and only because a commit is all it has.
- **Nothing arrives at end-of-session.** Tool-at-a-time harnesses give you the
  edits as they happen or not at all, so buffer them (`pending.py`).
- **Field casing differs**, and Copilot ships two payload shapes at once. Every
  read goes through `_first(payload, "session_id", "sessionId", …)` so an adapter
  accepts whichever arrives.
- **"Working directory" isn't always `cwd`.** Cursor sends `workspace_roots`, a
  *list*; Kiro may send `workspaceRoot`. Adapters override `read_cwd` for this.
- **Session identity isn't always `session_id`.** Cursor's stable-across-turns id
  is `conversation_id` (`generation_id` changes every message, so it's the wrong
  key to buffer against).
- **Config nesting differs:** matcher-group→`hooks[]` (Claude, Gemini) vs flat
  array (Cursor, Copilot).
- **Timeout units differ:** seconds (Claude, Cursor, Copilot `timeoutSec`) vs ms (Gemini).
- **Some config files are shared.** `.cursor/hooks.json` and Gemini's
  `settings.json` hold other tools' settings, so installers merge into them and
  only ever touch entries containing our marker. Kiro's and Copilot's hook files
  are ours alone and can be written whole.

## Explicitly out of scope
- **Feedback injection into the agent** (`additionalContext` → "fix this vuln"). The
  architecture *supports* it — and in-chat mode uses the same machinery to ask for
  analysis — but learnlance stays a passive *learning* tool, not a linter or
  reviewer, so it never tells the agent to change the code.
- **Filesystem watcher fallback** (`watch`): possible for editors with no hooks, but
  it can't tell AI edits from human edits and can't dedupe cleanly — the git hook is
  the better universal fallback.
