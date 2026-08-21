# learnlance 🧠🔍

A learning companion for **Claude Code**. Every time Claude finishes a turn and
has generated or edited code, learnlance quietly:

1. reads the session transcript and pulls out the code that was just written,
2. asks Claude *"what concepts could a developer learn from this?"* —
   e.g. *"you used a **delta function**, here's what delta encoding is"*,
3. merges those concepts into a **persistent knowledge graph**, and
4. regenerates an interactive HTML graph you can open any time.

**No API key required.** By default it reuses the `claude` CLI you're already
logged into (your Claude Code subscription), so there's nothing extra to set up.

So instead of code just appearing, you build up a visual map of everything
you've picked up along the way — new nodes light up as *🌱 new topics learned*.

## Why it won't slow you down or break your session
- The API call runs in a **detached background process** — Claude Code never waits.
- The hook is wrapped so any failure is logged and swallowed; it can never
  interrupt your coding session.
- Turns with no substantive code make **no API call** (no cost, no noise).

## Install (no pip, no API key)

```bash
# just install the Claude Code Stop hook — it uses your logged-in `claude`
python -m learnlance install
```

Run this from the `learnlance/` project folder. That's it — start (or restart)
Claude Code and code as usual.

If `claude` isn't on your PATH, point learnlance at it:

```bash
python -m learnlance config --claude-bin "C:\path\to\claude.cmd"
```

> Prefer a global `learnlance` command? `pip install -e .` in this folder, then
> use `learnlance` instead of `python -m learnlance` everywhere.
>
> Prefer a direct API call instead of the CLI? `learnlance config --backend api
> --set-key sk-ant-...`

## Use it

```bash
learnlance show      # render + open the interactive knowledge graph in your browser
learnlance list -v   # list learned concepts (with explanations) in the terminal
learnlance stats     # quick counts, broken down by category
```

Per-session markdown recaps are written to `~/.learnlance/insights/<session>.md`.

## Configuration

```bash
learnlance config                      # show current settings
learnlance config --cli-model haiku    # make the cli backend use a faster model
learnlance config --max-topics 3       # fewer concepts per turn
learnlance config --background off      # run inline (blocks until analysis is done)
learnlance config --disable             # pause without uninstalling the hook
learnlance config --backend api --set-key sk-ant-...   # switch to the API backend
```

Everything lives under `~/.learnlance/`:
`graph.json` (the graph), `graph.html` (the visualization), `insights/`
(markdown recaps), `learnlance.log` (diagnostics).

## Uninstall

```bash
python -m learnlance uninstall
```

## How it works (internals)

| File | Role |
|------|------|
| `hook.py` | Stop-hook entry; spawns the detached worker |
| `transcript.py` | Parses Claude Code's JSONL transcript for generated code |
| `insights.py` | Generates insights — via the `claude` CLI (default) or the API |
| `graph.py` | Merges concepts into the persistent knowledge graph |
| `viz.py` | Renders the offline, self-contained HTML graph |
| `install.py` | Wires the hook into `~/.claude/settings.json` |

Zero third-party dependencies by design — the hook must run reliably wherever
Claude Code launches it.
