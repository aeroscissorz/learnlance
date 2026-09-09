# learnlance 🧠🔍

Turn what AI coding agents build into a growing personal knowledge graph.

[Demo](#demo) · [Install](#install) · [Contribute](#contributing)

```
┌───────────────────────────────────────────────┐
│                                               │
│                YOUR LEARNING GRAPH            │
│                                               │
│    Delta encoding ──── Data synchronization   │
│          │                   │                │
│          └─── Compression ────┘                │
│                  │                            │
│                  └──────── Diffing            │
│                                               │
│    Debouncing ───── Idempotency keys          │
│                                               │
│    Topological sort ─── Graph algorithms      │
│                                               │
└───────────────────────────────────────────────┘
```

You use AI to write code.

Learnlance watches what gets built and turns that work into concepts you
can actually understand and remember.

**AI writes the code. Learnlance helps you learn from it.**

## Demo

<!-- Drop in a real GIF or video of the graph being generated, e.g.:
     ![Learnlance demo](docs/demo.gif)
-->

learnlance watches your agent — **Claude Code, OpenAI Codex, Cursor, GitHub
Copilot (CLI / cloud / VS Code), Command Code, Kiro, Gemini CLI, Antigravity**,
or plain `git commit` — and after every turn that writes or edits code, it
quietly:

1. pulls out the code the agent just wrote,
2. asks an LLM *"what transferable concepts could a developer learn from this?"* —
   e.g. *"you used a delta function — here's what delta encoding is"*,
3. merges those concepts into a **persistent knowledge graph**, and
4. regenerates an interactive HTML graph you can open any time.

Over time you get a browsable map of everything you've picked up while coding —
new nodes light up as *🌱 new topics learned*.

**No API key required.** By default it reuses an LLM CLI you're already logged
into (`claude`, `gemini`, `copilot`, `cursor-agent`, or `ollama`). Or use
`--in-chat` and the agent analyzes its own work — no separate CLI at all.

## Why it won't slow you down or break your session

- Analysis runs in a **detached background process** — your session never waits.
- Every hook is wrapped so a failure is logged and swallowed; it can't interrupt
  your coding session.
- Turns with no substantive code make **no LLM call** (no cost, no noise).

## Install

```bash
pip install learnlance
learnlance setup
```

`setup` detects the agents you have and writes their hooks for the current
project. By default it analyzes your work with a separate LLM CLI (`claude`,
`gemini`, `copilot`, `cursor-agent`, or `ollama`).

**Using a chat agent** (Copilot Chat in VS Code, Cursor, Kiro, Command Code,
Antigravity, or Gemini)? Let the agent analyze its own work in the chat instead —
no separate CLI to install:

```bash
learnlance setup --in-chat
```

Run `setup` again in each project you want tracked, then reload your editor and
code as usual.

Check what's actually installed and firing:

```bash
learnlance doctor
```

## Getting Started

New to LearnLance?

Follow the [Getting Started Guide](docs/getting-started.md) for a
step-by-step walkthrough from installation and setup to your first
knowledge graph.

### Install one agent by hand

`learnlance install` with no flags is the same as `learnlance setup` — it detects
your agents and configures them all. Pass a flag to configure just one agent,
skipping detection:

```bash
learnlance install --cursor        # Cursor only
learnlance install --codex         # OpenAI Codex only
learnlance install --copilot       # Copilot CLI / cloud / VS Code Chat
learnlance install --commandcode   # Command Code
learnlance install --kiro          # Kiro
learnlance install --gemini        # Gemini CLI
learnlance install --antigravity   # Antigravity
learnlance install --git           # git post-commit (universal fallback)
```

Run `learnlance install --help` for the full list and options.

## Supported agents

| Agent | Captures on | Analyzes on |
|-------|-------------|-------------|
| Claude Code | (whole transcript) | `Stop` |
| OpenAI Codex | `PostToolUse` | `Stop` |
| Cursor | `afterFileEdit` | `stop` |
| GitHub Copilot — CLI, cloud, VS Code | `PostToolUse` | `Stop` |
| Command Code | `PostToolUse` | `Stop` |
| Kiro | `PostToolUse` | `Stop` |
| Gemini CLI | `AfterTool` | `AfterAgent` |
| Antigravity | `PostToolUse` | `Stop` |
| git | — | `post-commit` |

Each integration is written from the vendor's hook docs. `learnlance doctor`
reports what's configured on disk versus what has actually fired.

## Use it

```bash
learnlance show      # render + open the knowledge graph in your browser
learnlance list -v   # list learned concepts, with explanations
learnlance stats     # quick counts by category
learnlance help      # every available command
```

The `show` view opens with a live loading spinner, then two declutter controls:
*show related concepts* (reveal dimmed umbrella nodes) and a *min link strength*
slider (hide one-off links).

### Add a concept the agent missed

```bash
learnlance add "debouncing"                 # search the current dir for the topic
learnlance add "topological sort" --path ./src
learnlance add "event sourcing" --force     # add even if it's not in the code
```

### Clear the graph

```bash
learnlance clear "delta encoding"   # remove one concept (+ orphaned related nodes)
learnlance clear                    # wipe the graph (asks first; -y to skip)
```

Per-session recaps are written to `~/.learnlance/insights/<session>.md`.

## Configuration

```bash
learnlance config                            # show current settings
learnlance config --llm-cmd "ollama run llama3"   # use any CLI that reads stdin
learnlance config --cli-model haiku          # model alias for the CLI backend
learnlance config --max-topics 3             # fewer concepts per turn
learnlance config --background off           # run analysis inline (blocks)
learnlance config --disable                  # pause without uninstalling hooks
learnlance config --enable                   # re-enable
```

Everything lives under `~/.learnlance/`. Each project keeps its own graph at
`projects/<project>-<hash>/graph.json`; `graph.html` is the multi-project
dashboard you can open to switch between projects. `insights/` holds per-session
recaps and `learnlance.log` holds diagnostics.

## Uninstall

```bash
learnlance uninstall                 # Claude Code (default)
learnlance uninstall --commandcode   # one specific agent
```

## How it works

learnlance normalizes every agent's hook payload into one `CodeEvent`, then runs
a harness-blind pipeline: insights → knowledge graph → HTML → recap. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

| File | Role |
|------|------|
| `adapters.py` | Translates each agent's hook payload into a `CodeEvent` |
| `install.py` | Writes each agent's hook config (per project) |
| `autosetup.py` | Detects your agents and installs missing hooks |
| `hook.py` | Hook entrypoint; spawns the detached worker |
| `core.py` | The harness-blind learning pipeline |
| `insights.py` | Generates insights via an LLM CLI (or in-chat) |
| `transcript.py` | Parses Claude Code's JSONL transcript |
| `pending.py` | Buffers mid-session edits from tool-at-a-time agents |
| `graph.py` | Merges concepts into the knowledge graph |
| `viz.py` | Renders the offline, self-contained HTML graph |
| `codesearch.py` | Finds where a topic lives in your code (`add`) |

Zero third-party dependencies by design — hooks must run reliably wherever an
agent launches them.

## Contributing

LearnLance is actively looking for contributors. You don't need to understand the
entire codebase to contribute.

### Areas to help

- A coding-agent integration (adapter + installer)
- Knowledge graph algorithms
- Concept extraction
- Graph visualization
- CLI/UX
- Testing
- Documentation
- New learning workflows

### Good first contributions

- Add support for another coding agent
- Improve graph visualization
- Add tests for transcript parsing
- Improve Windows compatibility
- Add a CLI command
- Improve concept deduplication
- Improve graph accessibility
- Add documentation or examples

## License

MIT — see [LICENSE](LICENSE).
