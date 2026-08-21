# learnlance — Design Document

**Version:** 0.1.0
**Status:** implemented & installed
**Author:** generated with Claude Code
**Last updated:** 2026-08-21

---

## 1. What it is (in one paragraph)

**learnlance** is a CLI-level learning companion for **Claude Code**. Every time
Claude Code finishes a turn in which it *generated or edited code*, learnlance
reads what was produced, asks an LLM *"what transferable concepts could a
developer learn from this?"*, and records those concepts as nodes in a persistent
**knowledge graph**. Related concepts are automatically wired together — even
across different sessions and projects — so over time you get a single connected,
browsable map of everything you've picked up while coding. It requires **no API
key**: it reuses the `claude` CLI you're already logged into.

> Example: you ask Claude to "only send changed rows." Claude writes a `delta()`
> function. learnlance surfaces: *"You used **delta encoding** — here's what it is,
> here's how it showed up in your code"* and adds a **Delta encoding** node linked
> to **Diffing** and **Data synchronization**.

---

## 2. Goals & non-goals

### Goals
- **Passive learning.** Zero effort — insights appear as a side effect of normal work.
- **No new credentials.** Ride the existing Claude Code subscription auth.
- **Never intrude.** Must never slow down, block, or break a Claude Code session.
- **A connected graph, not a list.** Concepts must link by genuine relatedness so
  the graph reads as one web of knowledge, not disconnected islands.
- **Local & inspectable.** All data is plain JSON on disk the user owns.
- **Zero runtime dependencies.** Must run reliably in whatever environment Claude
  Code launches a hook in.

### Non-goals
- Not a spaced-repetition / quiz system (though the graph could feed one later).
- Not a code-review or correctness tool — it teaches, it doesn't judge.
- Not a cloud service — nothing leaves the machine except the LLM prompt.

---

## 3. How it triggers — the Claude Code hook

learnlance installs itself as a **`Stop` hook** in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
        "command": "\"<python>\" \"<repo>\\learnlance_hook.py\"" } ] }
    ]
  }
}
```

Claude Code fires the `Stop` hook whenever the main agent finishes responding,
passing a JSON payload on **stdin**:

```json
{
  "session_id": "abc123",
  "transcript_path": "C:\\...\\<session>.jsonl",
  "cwd": "C:\\path\\to\\project",
  "hook_event_name": "Stop"
}
```

The `transcript_path` points at the full session transcript (JSONL), which is
learnlance's source of truth for "what did Claude just do."

The command points at `learnlance_hook.py`, a thin launcher that puts the package
on `sys.path` and calls the hook — this means **no `pip install` is required**.

---

## 4. Architecture & data flow

```
Claude Code finishes a turn
        │  (Stop hook, JSON on stdin)
        ▼
┌─────────────────────┐
│ learnlance_hook.py   │  launcher (abs paths, no pip needed)
└─────────┬───────────┘
          ▼
┌─────────────────────┐   re-entry guard: if LEARNLANCE_ACTIVE set → exit
│ hook.run_hook()     │   backend readiness check (claude bin / api key)
└─────────┬───────────┘
          │  spawns DETACHED background process, then exits 0 immediately
          ▼
┌─────────────────────┐
│ hook.process()      │  (background worker — Claude Code no longer waiting)
└─────────┬───────────┘
          ▼
   transcript.py     ── parse JSONL, collect code edited SINCE last run
          │             (Write/Edit/MultiEdit/NotebookEdit tool uses)
          ▼
   [skip if no substantive code]   ── no LLM call, no cost
          ▼
   insights.py       ── ask the LLM: concepts + explanations + tags + related
          │             backend = `claude -p` (default) OR Anthropic API
          ▼
   graph.py          ── merge concepts into the knowledge graph:
          │             nodes, tags, weighted/typed edges, placeholder merge
          ▼
   viz.py            ── regenerate the self-contained interactive HTML
          │
          ▼
   ~/.learnlance/     ── graph.json · graph.html · insights/<session>.md · log
```

### Why a detached background worker?
The LLM call takes seconds. If the hook did it inline, every Claude Code turn
would pause until analysis finished. Instead `run_hook()` writes the payload to a
temp file, launches a **detached** process (`DETACHED_PROCESS` on Windows,
`start_new_session` on POSIX), and returns exit 0 immediately. The user's session
never waits.

---

## 5. Components

| Module | Responsibility |
|--------|----------------|
| `learnlance_hook.py` | Standalone launcher wired into settings.json (no pip needed). |
| `config.py` | Paths under `~/.learnlance/`, defaults, config load/save, logging. |
| `hook.py` | Stop-hook entry, re-entry guard, detached spawn, the `process()` pipeline. |
| `transcript.py` | Parse the JSONL transcript; extract code generated since last run. |
| `insights.py` | Turn code into concepts via `claude` CLI (default) or Anthropic API. |
| `graph.py` | Persistent knowledge graph: nodes, tags, edges, connectivity logic. |
| `viz.py` | Render the offline, self-contained interactive HTML graph. |
| `install.py` | Install/uninstall the hook in `~/.claude/settings.json` (idempotent). |
| `cli.py` | User commands: `install`, `show`, `list`, `stats`, `config`, … |

---

## 6. Data model (`~/.learnlance/graph.json`)

```jsonc
{
  "nodes": {
    "delta-encoding": {
      "id": "delta-encoding",
      "name": "Delta encoding",
      "category": "algorithm",            // one of a fixed vocabulary
      "level": "intermediate",
      "explanation": "Send only what changed since the last version…",
      "count": 3,                          // times encountered
      "first_seen": "2026-08-21T17:01:13",
      "last_seen":  "2026-08-21T18:20:00",
      "tags": ["compression", "diffing", "data-sync"],
      "examples": [                        // last 8 encounters
        { "did": "…", "why_here": "…", "files": ["sync/delta.py"],
          "when": "…", "session": "…" }
      ]
    },
    "diffing": { "…": "…", "placeholder": true }   // named-but-not-yet-learned
  },
  "edges": [
    { "source": "delta-encoding", "target": "diffing",
      "type": "related", "weight": 2, "tags": [] }
  ],
  "tag_index": { "diffing": ["delta-encoding", "…"] },  // tag → node ids
  "sessions": { "<session_id>": { "cwd": "…", "topics": ["…"], "first": "…", "last": "…" } },
  "meta": { "turns": 12 }
}
```

- **Node** = a concept. `placeholder: true` marks a concept that was *named as
  related* but not yet actually learned; it has no explanation until learned.
- **Edge** = a relationship. One edge per unordered pair; see §7.
- **tag_index** = reverse index enabling cross-session linking in O(bucket size).

Per-session human-readable recaps are also written to
`~/.learnlance/insights/<session>.md`.

---

## 7. Connectivity model — "tag them relatively so the graph connects"

The central design problem: naïvely, concepts from different sessions never touch,
so the graph fragments into islands. learnlance solves this with **three edge types**
and **placeholder merging**.

### Edge types (one edge per pair; strongest type wins, weight accumulates)

| Type | Priority | Formed when |
|------|:--------:|-------------|
| `co-occurs`  | 3 (strongest) | Two concepts are learned in the **same turn**. |
| `related`    | 2 | The LLM explicitly names concept B as adjacent/parent of A. |
| `shared-tag` | 1 | A new concept shares a **tag** with an existing concept from **any** past session. |

Each edge carries a `weight` that increments every time the relationship recurs,
and (for `shared-tag`) the tag(s) responsible. The visualization maps weight →
line thickness and type → line color.

### Tags are the connective tissue
Every concept gets 2–5 short, reusable kebab-case tags (`concurrency`, `caching`,
`http`, …). The LLM is instructed to **reuse the same tag wording for the same
theme every time**, so concepts about the same idea land in the same tag bucket
and get wired together — even months apart, in different repos.

To avoid a "hairball," a new concept links to at most **3** existing neighbours per
tag, preferring the most-reinforced (highest `count`) ones.

### Placeholder merging fuses clusters
When the LLM names a `related` concept that doesn't exist yet, learnlance adds a
lightweight **placeholder** node and links to it. Later, when you actually learn
that concept, the placeholder is **upgraded in place** (same node id = slug of the
name) — instantly joining two previously separate clusters at that shared node.

**Result (verified):** concepts learned in entirely separate sessions form a single
fully-connected component, purely from relatedness.

---

## 8. Insight generation (the LLM step)

### Backends
- **`cli` (default, no API key):** shell out to the `claude` CLI in headless print
  mode: `claude -p --output-format text`. The prompt is piped via **stdin** (avoids
  Windows' ~8 KB command-line limit). This uses the user's existing Claude Code
  subscription auth — nothing extra to configure.
- **`api` (opt-in):** a direct Anthropic Messages API call via stdlib `urllib`
  (no SDK), requiring an API key.

Both go through one dispatcher, `insights.generate()`.

### Prompt contract
The model is asked to return **strict JSON only**:

```jsonc
{
  "did": "one plain-language sentence: what was accomplished",
  "topics": [{
    "name": "Delta encoding",
    "category": "algorithm|data-structure|pattern|security|…",
    "level": "beginner|intermediate|advanced",
    "explanation": "2–4 beginner-friendly sentences",
    "why_here": "how it showed up in THIS change",
    "tags": ["compression", "diffing"],       // reusable connective keywords
    "related": ["Data synchronization"]         // ≥1 broader umbrella concept
  }]
}
```

Robust parsing strips markdown fences and extracts the outermost `{…}` before
`json.loads`, so minor model formatting drift doesn't break the pipeline.

---

## 9. Reliability & safety (design invariants)

These are the rules the implementation is built around:

1. **Never break the session.** The entire `process()` pipeline is wrapped in
   try/except; any failure is written to `~/.learnlance/learnlance.log` and swallowed.
   The hook always exits 0.
2. **Never block the session.** All real work runs in a detached background process.
3. **No infinite recursion.** The `cli` backend launches a headless `claude`, which
   *itself* fires the Stop hook. learnlance sets an env flag `LEARNLANCE_ACTIVE=1` on
   that child; the nested hook sees it and bails immediately. (Verified: 0 recursive
   spawns.)
4. **No wasted cost.** Turns with no substantive generated code make **no LLM call**.
   A per-session cursor (`state.json`, keyed by the last processed transcript entry
   `uuid`) guarantees the same turn is never analyzed twice.
5. **Bounded input.** The code sent to the LLM is capped (`max_input_chars`, default
   14 KB) and truncated per-file, so large diffs stay within limits.
6. **Fail visibly, locally.** Auth expiry, missing `claude` binary, etc. are logged
   with a clear reason; the user can always read the log.

---

## 10. Configuration (`~/.learnlance/config.json`)

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `true` | Master on/off without uninstalling the hook. |
| `backend` | `"cli"` | `cli` (logged-in claude, no key) or `api` (needs key). |
| `claude_bin` | `""` | Path to `claude` (`""` ⇒ auto-detect on PATH). |
| `cli_model` | `""` | Optional model alias for the cli backend. |
| `model` | `claude-haiku-4-5-…` | Model id for the `api` backend. |
| `api_key` | `""` | Falls back to `ANTHROPIC_API_KEY` env. |
| `max_topics_per_turn` | `5` | Cap concepts extracted per turn. |
| `background` | `true` | Run analysis detached (vs. inline). |
| `min_chars` | `40` | Skip trivial edits below this size. |
| `max_input_chars` | `14000` | Cap code sent to the LLM. |

Set via e.g. `learnlance config --backend api --set-key sk-ant-…`.

---

## 11. CLI surface

```
learnlance install        # wire the Stop hook into ~/.claude/settings.json
learnlance uninstall      # remove it
learnlance show           # render + open the interactive HTML graph
learnlance list [-v]      # list learned concepts (‑v adds explanations)
learnlance stats          # counts, broken down by category
learnlance config …       # view/change settings
learnlance hook           # (internal) Stop-hook entry point
learnlance _worker <job>  # (internal) detached background worker
```

---

## 12. Visualization

`viz.py` emits a **single self-contained HTML file** (`~/.learnlance/graph.html`)
with an embedded, dependency-free force-directed layout — no CDNs, works offline
forever. Features: drag nodes, scroll to zoom, pan, search, click a node for its
explanation / tags / link count / where you met it. Node color = category, node
size = encounter count, edge color = relation type, edge thickness = weight.

---

## 13. Failure modes & handling

| Situation | Behaviour |
|-----------|-----------|
| `claude` login expired (401) | Logged; turn skipped; session unaffected. Fix: re-auth `claude`. |
| `claude` not on PATH | Logged at hook start; skipped. Fix: `config --claude-bin`. |
| LLM returns non-JSON | Fence-stripping + outer-brace extraction; if still bad, logged & skipped. |
| Transcript unreadable / empty | Skipped quietly. |
| Detached spawn fails | Falls back to inline processing. |
| Same turn seen twice | Cursor in `state.json` prevents reprocessing. |

---

## 14. Extensibility (future work)

- **Concept mastery / decay:** weight nodes by recency & frequency; suggest reviews.
- **Non-edit turns:** also mine conceptual Q&A answers, not just code edits.
- **`learnlance serve`:** live-refresh the graph in the browser as you work.
- **Export:** Mermaid / Obsidian / Anki export from the same `graph.json`.
- **Tag normalization:** cluster near-duplicate tags (e.g. `auth` vs `authentication`).
- **Per-project graphs:** optional scoping by `cwd`.

---

## 15. File layout

```
learnlance/
├─ learnlance_hook.py         # launcher referenced by the hook
├─ pyproject.toml            # optional pip install (console_script: learnlance)
├─ README.md                 # quick start
├─ DESIGN.md                 # this document
└─ learnlance/
   ├─ __main__.py            # python -m learnlance
   ├─ cli.py                 # command dispatch
   ├─ config.py              # paths, defaults, logging
   ├─ hook.py                # Stop-hook entry + detached worker + pipeline
   ├─ transcript.py          # JSONL parsing → generated code
   ├─ insights.py            # LLM backends (cli / api)
   ├─ graph.py               # knowledge graph + connectivity
   ├─ viz.py                 # self-contained HTML renderer
   └─ install.py             # settings.json install/uninstall

runtime data (created on use): ~/.learnlance/
   ├─ config.json  graph.json  graph.html  state.json  learnlance.log
   └─ insights/<session>.md
```
