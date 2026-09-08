"""learnlance command-line interface."""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import webbrowser

from . import (autosetup, capabilities, codesearch, config, graph, hook, inchat,
               insights, install, viz)
from .spinner import Spinner


# Harnesses selectable with a flag. Claude Code is the flagless default since
# its hook is user-level rather than per-project.
_INSTALLERS = {
    "kiro": ("install_kiro_hook", "uninstall_kiro_hook"),
    "cursor": ("install_cursor_hook", "uninstall_cursor_hook"),
    "copilot": ("install_copilot_hook", "uninstall_copilot_hook"),
    "gemini": ("install_gemini_hook", "uninstall_gemini_hook"),
    "antigravity": ("install_antigravity_hook", "uninstall_antigravity_hook"),
    "codex": ("install_codex_hook", "uninstall_codex_hook"),
    "commandcode": ("install_commandcode_hook", "uninstall_commandcode_hook"),
    "git": ("install_git_hook", "uninstall_git_hook"),
}


def _selected_harnesses(args) -> list[str]:
    return [name for name in _INSTALLERS if getattr(args, name, False)]


def _cmd_install(args):
    chosen = _selected_harnesses(args)
    in_chat = getattr(args, "in_chat", False)
    # An explicit install is consent: undo any previous `uninstall` opt-out.
    autosetup.set_opted_out(False)
    if chosen:
        project = os.path.abspath(args.path or os.getcwd())
        unsupported = [n for n in chosen if in_chat and n not in inchat.SUPPORTED]
        if unsupported:
            # Apply --in-chat where it works rather than refusing the whole batch.
            print(f"--in-chat isn't available for: {', '.join(unsupported)}"
                  f" — installing those in normal mode.")
        for name in chosen:
            fn = getattr(install, _INSTALLERS[name][0])
            print(fn(project, in_chat) if name in inchat.SUPPORTED else fn(project))
        print("\nlearnlance will now learn from the code these agents write.")
        if in_chat:
            print("No LLM CLI needed — the agent analyzes its own work in-chat.")
        return

    print(install.install_hook())
    cfg = config.load_config()
    label = insights.backend_label(cfg)
    if label:
        print(f"\nAnalysis backend: {label} — no API key needed.")
    else:
        print("\n⚠  No LLM CLI found, so nothing can be analyzed yet.")
        print("     Install one of: " + ", ".join(n for n, _ in insights.KNOWN_BACKENDS))
        print("     or point at any CLI: learnlance config --llm-cmd \"ollama run llama3\"")
        print("     Edits are still captured meanwhile, and analyzed once one exists.")
    print("\nDone. New sessions will now build your knowledge graph.")


def _cmd_uninstall(args):
    chosen = _selected_harnesses(args)
    if chosen:
        project = os.path.abspath(args.path or os.getcwd())
        for name in chosen:
            print(getattr(install, _INSTALLERS[name][1])(project))
        return
    # A bare `uninstall` means "stop doing this": record it, or the implicit
    # auto-setup would re-install everything on the very next command.
    autosetup.set_opted_out(True)
    print(install.uninstall_hook())
    print("\nAuto-setup disabled. Run `learnlance setup` (or `install`) to re-enable.")


def _cmd_setup(args):
    """The one command to run after `pip install`.

    Separate from the implicit auto-setup because that runs once ever, while four
    of the six hooks are per-project — so this must work every time it's called,
    in every project you want tracked.
    """
    here = os.path.abspath(args.path or os.getcwd())
    in_chat = getattr(args, "in_chat", False)
    verbose = getattr(args, "verbose", False)

    actions = autosetup.run(here, force=True, in_chat=in_chat)
    configured = [autosetup.HARNESSES[n]["label"] for n in autosetup.HARNESSES
                  if autosetup.hook_present(n, here)]
    if autosetup.claude_hook_present():
        configured.insert(0, "Claude Code")

    print(f"learnlance is set up for {os.path.basename(here)}.\n")
    if configured:
        print("  Watching: " + ", ".join(configured))
    else:
        print("  No agents found here. `learnlance install --help` lists them.")

    cfg = config.load_config()
    label = insights.backend_label(cfg)
    live_in_chat = [n for n in inchat.SUPPORTED if autosetup.hook_in_chat(n, here)]
    if live_in_chat:
        print("  Analysis: your agent does it in-chat — nothing else to install.")
    elif label:
        print(f"  Analysis: {label}")
    else:
        print("  Analysis: none yet, so edits are held rather than analyzed. Either:")
        print("              learnlance setup --in-chat")
        print("              learnlance config --llm-cmd \"ollama run llama3\"")

    print("\n  Reload your editor, then just code.")
    print("  `learnlance doctor` shows what's actually firing, "
          "`learnlance show` opens the graph.")

    # Everything below is diagnostic detail: which harness uses which mechanism,
    # and where confidence is low. Useful when something isn't working, noise
    # otherwise, so it stays behind --verbose rather than greeting every install.
    if not verbose:
        return

    print("\n  --- detail ---")
    found = autosetup.detect_harnesses(here)
    for name, spec in autosetup.HARNESSES.items():
        # A tool's config directory is the only signal available without assuming
        # it's on PATH — an IDE often isn't. So this means "we can configure it",
        # not "it is definitely installed and in use".
        cap = capabilities.CAPABILITIES.get(name)
        bits = ["config dir found" if name in found else "no config dir"]
        if autosetup.hook_in_chat(name, here) and cap and cap.in_chat:
            bits.append(f"asked via {cap.in_chat}")
        if cap and cap.hook_confidence is capabilities.Confidence.DISPUTED:
            bits.append("reports say its hooks may not fire")
        print(f"  {spec['label']:18} {'; '.join(bits)}")
    if actions:
        print(f"\n  Reconfigured this run: {', '.join(actions)}")
    print("\n  These integrations follow each vendor's documented hook API but are")
    print("  unconfirmed on this machine until one actually fires.")


def _cmd_doctor(args):
    import importlib.metadata as _md
    import platform
    import shutil as _sh

    cfg = config.load_config()
    ok = lambda b: "✓" if b else "✗"

    try:
        ver = _md.version("learnlance-univ")  # distribution name, not the import pkg
    except Exception:
        from . import __version__ as ver  # running from source

    git_ok = bool(_sh.which("git"))
    backend = insights.backend_label(cfg)
    here = os.getcwd()

    g = graph.load_project(here)
    concepts = sum(1 for n in g.get("nodes", {}).values() if not n.get("placeholder"))

    in_chat_hooks = [n for n in inchat.SUPPORTED if autosetup.hook_in_chat(n, here)]
    log = capabilities._log_text()

    print("learnlance doctor\n")
    print(f"  learnlance        {ok(True)} {ver}")
    print(f"  python            {ok(True)} {platform.python_version()}")
    print(f"  git               {ok(git_ok)}")
    if backend:
        print(f"  LLM backend       {ok(True)} {backend}")
    elif in_chat_hooks:
        print(f"  LLM backend       {ok(True)} in-chat via "
              f"{', '.join(in_chat_hooks)} (no CLI needed)")
    else:
        print(f"  LLM backend       {ok(False)} none found "
              f"({', '.join(n for n, _ in insights.KNOWN_BACKENDS)})")
        print(f"                      → learnlance config --llm-cmd \"ollama run llama3\"")
        print(f"                      → or: learnlance install --kiro --in-chat")

    # Two separate claims: what's on disk, and what has actually run. Only the
    # second is evidence, so they're never collapsed into one tick.
    print()
    print(f"  integrations (project: {os.path.basename(here)})")
    claude_on = autosetup.claude_hook_present()
    print(f"    {'Claude Code':15} {ok(claude_on)} "
          f"{capabilities.status('claude', claude_on, False, log)}")
    # Copilot CLI and VS Code Copilot Chat share one hook registration, so they're
    # one row — but the label says which surface(s) are actually present.
    surfaces = autosetup.copilot_surfaces()
    copilot_label = ("Copilot (" + ", ".join(surfaces) + ")"
                     if surfaces else "Copilot / VS Code")
    for name, label in (("kiro", "Kiro"), ("cursor", "Cursor"),
                        ("copilot", copilot_label), ("gemini", "Gemini CLI"),
                        ("antigravity", "Antigravity"), ("codex", "OpenAI Codex"),
                        ("commandcode", "Command Code"), ("git", "git commit")):
        present = autosetup.hook_present(name, here)
        state = capabilities.status(name, present,
                                    autosetup.hook_in_chat(name, here), log)
        line = f"    {label:15} {ok(present)} {state}"
        if not present:
            line += f"   → learnlance install --{name}"
        print(line)

    unverified = [n for n in capabilities.CAPABILITIES
                  if autosetup.hook_present(n, here)
                  and not capabilities.evidence(n, log).fired]
    if unverified:
        print()
        print("  'configured, unverified' means the hook config is written but that")
        print("  harness has never invoked us. Reload the editor, make one edit, then")
        print("  re-run doctor — it upgrades to 'working' once the log proves it.")

    print()
    print(f"  concepts learned  {concepts}")


def _cmd_config(args):
    cfg = config.load_config()
    changed = False
    if args.llm_cmd is not None:
        cfg["llm_cmd"] = args.llm_cmd
        changed = True
    if args.claude_bin is not None:
        cfg["claude_bin"] = args.claude_bin
        changed = True
    if args.cli_model is not None:
        cfg["cli_model"] = args.cli_model
        changed = True
    if args.enable:
        cfg["enabled"] = True
        changed = True
    if args.disable:
        cfg["enabled"] = False
        changed = True
    if args.background is not None:
        cfg["background"] = args.background == "on"
        changed = True
    if args.max_topics is not None:
        cfg["max_topics_per_turn"] = args.max_topics
        changed = True
    if changed:
        config.save_config(cfg)
        print("Saved config.")
    print(f"\nConfig ({config.CONFIG_PATH}):")
    for k, v in cfg.items():
        print(f"  {k}: {v}")


def _cmd_show(args):
    cwd = os.path.abspath(args.project or os.getcwd())
    config.register_project(cwd)
    with Spinner("loading"):
        g = graph.load_project(cwd)
        path = viz.render_project_html(g, cwd)
    print(f"Graph written to {path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())


def _cmd_list(args):
    cwd = os.path.abspath(args.project or os.getcwd())
    g = graph.load_project(cwd)
    nodes = [n for n in g.get("nodes", {}).values() if not n.get("placeholder")]
    if not nodes:
        print(f"Nothing learned yet for {os.path.basename(cwd)}. "
              "Install the hook and let an AI tool write some code.")
        return
    nodes.sort(key=lambda n: (-n.get("count", 0), n["name"].lower()))
    print(f"{os.path.basename(cwd)}: {len(nodes)} concepts learned:\n")
    for n in nodes:
        print(f"  • {n['name']}  [{n.get('category','')}·{n.get('level','')}]  seen {n.get('count',0)}×")
        if args.verbose and n.get("explanation"):
            print(f"      {n['explanation']}")


def _cmd_stats(args):
    cwd = os.path.abspath(args.project or os.getcwd())
    g = graph.load_project(cwd)
    nodes = g.get("nodes", {})
    real = [n for n in nodes.values() if not n.get("placeholder")]
    cats: dict[str, int] = {}
    for n in real:
        cats[n.get("category", "other")] = cats.get(n.get("category", "other"), 0) + 1
    print(f"Project          : {os.path.basename(cwd)}")
    print(f"Concepts learned : {len(real)}")
    print(f"Related links    : {len(g.get('edges', []))}")
    print(f"Turns analyzed   : {g.get('meta', {}).get('turns', 0)}")
    print(f"Sessions         : {len(g.get('sessions', {}))}")
    if cats:
        print("\nBy category:")
        for c, n in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"  {c:18} {n}")
    # Also show summary of all projects
    registry = config.load_projects_registry()
    if len(registry) > 1:
        print(f"\n({len(registry)} projects tracked total — use `learnlance show` to browse all)")


def _confirm(prompt: str) -> bool:
    """Ask a y/N question. Returns False for non-interactive / EOF / anything
    that isn't an explicit yes, so we never destroy data by accident."""
    try:
        if not sys.stdin.isatty():
            return False
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _cmd_clear(args):
    cwd = os.path.abspath(args.project or os.getcwd())
    g = graph.load_project(cwd)
    if args.concept:
        query = " ".join(args.concept).strip()
        matches = graph.find_concepts(g, query)
        if not matches:
            print(f"No concept matching '{query}'. Try `learnlance list` to see names.")
            return
        if len(matches) > 1:
            print(f"'{query}' matches {len(matches)} concepts — be more specific:")
            for nid in matches:
                print(f"  • {g['nodes'][nid]['name']}")
            return
        nid = matches[0]
        name = g["nodes"][nid]["name"]
        if not args.yes and not _confirm(f"Remove concept '{name}' from the graph?"):
            print("Aborted.")
            return
        graph.remove_node(g, nid)
        pruned = graph.prune_orphan_placeholders(g)
        graph.save_project(g, cwd)
        viz.render_project_html(g, cwd)
        extra = f" (also pruned {len(pruned)} orphaned related node(s))" if pruned else ""
        print(f"Removed '{name}'.{extra}")
        return

    # No concept given -> wipe the whole graph.
    real = sum(1 for n in g.get("nodes", {}).values() if not n.get("placeholder"))
    if not args.yes and not _confirm(
        f"Clear the ENTIRE learning graph for {os.path.basename(cwd)} ({real} concepts)? This can't be undone."
    ):
        print("Aborted.")
        return
    graph.save_project(graph.empty(), cwd)
    viz.render_project_html(graph.load_project(cwd), cwd)
    print(f"Learning graph cleared for {os.path.basename(cwd)}.")


def _cmd_add(args):
    cfg = config.load_config()
    if not insights.resolve_backend(cfg):
        print("No LLM CLI found. Install one of: "
              + ", ".join(n for n, _ in insights.KNOWN_BACKENDS))
        print("Or point at any CLI: learnlance config --llm-cmd \"ollama run llama3\"")
        return

    topic = " ".join(args.topic).strip()
    root = os.path.abspath(args.path or os.getcwd())

    blob, files = codesearch.gather(topic, root, int(cfg.get("max_input_chars", 14000)))
    if not blob and not args.force:
        print(f"No code references to '{topic}' found under {root}.")
        print("Use --force to add it from general knowledge anyway.")
        return
    if files:
        print(f"Found '{topic}' referenced in {len(files)} file(s); asking Claude…")

    try:
        with Spinner(f"analyzing '{topic}'"):
            result = insights.add_concept(cfg, topic, blob)
    except Exception as e:
        print(f"Could not analyze '{topic}': {e}")
        return

    if not result.get("topics"):
        print(f"Claude didn't find '{topic}' as a learnable concept in the code.")
        return

    config.register_project(root)
    g = graph.load_project(root)
    new_names = graph.update(g, result, {
        "when": _dt.datetime.now().isoformat(timespec="seconds"),
        "session": "manual", "cwd": root, "files": files,
    })
    graph.save_project(g, root)
    viz.render_project_html(g, root)
    added = ", ".join(t["name"] for t in result["topics"])
    tag = " (new)" if new_names else " (reinforced)"
    print(f"Added: {added}{tag}")


def _cmd_help(args):
    build_parser().print_help()


def _cmd_hook(args):
    hook.run_hook(getattr(args, "source", None), getattr(args, "in_chat", False),
                  getattr(args, "end", False))


def _cmd_worker(args):
    hook.run_worker(args.job)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="learnlance",
        description="Turn what AI agents build into a growing knowledge graph. "
                    "Run `learnlance setup` once per project to get started.")
    # metavar lists only the user-facing commands; internal ones (hook,
    # _worker) are added below without help= so they stay out of the listing.
    sub = p.add_subparsers(
        dest="cmd",
        metavar="{setup,install,uninstall,config,show,list,stats,clear,add,doctor,help}",
    )

    st_up = sub.add_parser("setup",
                           help="detect your agents and configure them (run this first)")
    st_up.add_argument("--in-chat", dest="in_chat", action="store_true",
                       help="let the agent analyze its own work — no LLM CLI to install")
    st_up.add_argument("-v", "--verbose", action="store_true",
                       help="also show per-agent mechanisms and confidence")
    st_up.add_argument("--path", metavar="DIR", help="project dir (default: current dir)")
    st_up.set_defaults(func=_cmd_setup)

    ins = sub.add_parser("install",
                         help="install hooks (Claude Code by default; or pick harnesses)")
    ins.add_argument("--kiro", action="store_true", help="Kiro (PostToolUse + Stop)")
    ins.add_argument("--cursor", action="store_true", help="Cursor (afterFileEdit + stop)")
    ins.add_argument("--copilot", action="store_true",
                     help="GitHub Copilot (postToolUse + agentStop)")
    ins.add_argument("--gemini", action="store_true",
                     help="Gemini CLI (AfterTool + AfterAgent)")
    ins.add_argument("--antigravity", action="store_true",
                     help="Google Antigravity (PostToolUse + Stop)")
    ins.add_argument("--codex", action="store_true",
                     help="OpenAI Codex (PostToolUse + Stop)")
    ins.add_argument("--commandcode", action="store_true",
                     help="Command Code (PostToolUse + Stop)")
    ins.add_argument("--git", action="store_true",
                     help="git post-commit — universal fallback for tools with no hooks")
    ins.add_argument("--in-chat", dest="in_chat", action="store_true",
                     help="let the agent analyze its own work (no LLM CLI needed); "
                          "the analysis happens visibly in your chat")
    ins.add_argument("--path", metavar="DIR", help="repo/project dir (default: current dir)")
    ins.set_defaults(func=_cmd_install)

    un = sub.add_parser("uninstall",
                        help="remove hooks (Claude Code by default; or pick harnesses)")
    un.add_argument("--kiro", action="store_true", help="remove the Kiro hooks")
    un.add_argument("--cursor", action="store_true", help="remove the Cursor hooks")
    un.add_argument("--copilot", action="store_true", help="remove the Copilot hooks")
    un.add_argument("--gemini", action="store_true", help="remove the Gemini CLI hooks")
    un.add_argument("--antigravity", action="store_true",
                     help="remove the Antigravity hooks")
    un.add_argument("--codex", action="store_true", help="remove the Codex hooks")
    un.add_argument("--commandcode", action="store_true",
                    help="remove the Command Code hooks")
    un.add_argument("--git", action="store_true", help="remove the git post-commit hook")
    un.add_argument("--path", metavar="DIR", help="repo/project dir (default: current dir)")
    un.set_defaults(func=_cmd_uninstall)

    c = sub.add_parser("config", help="view/set configuration")
    c.add_argument("--llm-cmd", dest="llm_cmd", metavar="CMD",
                   help='LLM CLI used for analysis, e.g. "ollama run llama3" '
                        '("" to go back to auto-detection)')
    c.add_argument("--claude-bin", dest="claude_bin", metavar="PATH",
                   help="legacy alias: explicit path to the claude executable")
    c.add_argument("--cli-model", dest="cli_model", metavar="MODEL",
                   help="optional model alias, e.g. haiku")
    c.add_argument("--enable", action="store_true")
    c.add_argument("--disable", action="store_true")
    c.add_argument("--background", choices=["on", "off"], help="run API work detached")
    c.add_argument("--max-topics", dest="max_topics", type=int)
    c.set_defaults(func=_cmd_config)

    s = sub.add_parser("show", help="render + open the HTML knowledge graph")
    s.add_argument("--no-open", action="store_true", help="just write the file")
    s.add_argument("--project", metavar="DIR", help="project dir (default: current dir)")
    s.set_defaults(func=_cmd_show)

    l = sub.add_parser("list", help="list learned concepts in the terminal")
    l.add_argument("-v", "--verbose", action="store_true")
    l.add_argument("--project", metavar="DIR", help="project dir (default: current dir)")
    l.set_defaults(func=_cmd_list)

    st = sub.add_parser("stats", help="summary counts")
    st.add_argument("--project", metavar="DIR", help="project dir (default: current dir)")
    st.set_defaults(func=_cmd_stats)

    cl = sub.add_parser("clear", help="clear the whole graph, or one concept")
    cl.add_argument("concept", nargs="*",
                    help="concept name to remove; omit to clear the entire graph")
    cl.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    cl.add_argument("--project", metavar="DIR", help="project dir (default: current dir)")
    cl.set_defaults(func=_cmd_clear)

    a = sub.add_parser("add", help="add a concept Claude missed (searches your code)")
    a.add_argument("topic", nargs="+", help="the concept to find and add, e.g. debouncing")
    a.add_argument("--path", metavar="DIR", help="codebase to search (default: current dir)")
    a.add_argument("--force", action="store_true",
                   help="add even if no code references are found")
    a.set_defaults(func=_cmd_add)

    sub.add_parser("doctor", help="check environment + which hooks are installed").set_defaults(func=_cmd_doctor)

    sub.add_parser("help", help="show this help message").set_defaults(func=_cmd_help)

    # Internal commands — no help= so they're omitted from the help listing.
    h = sub.add_parser("hook")
    h.add_argument("--source", metavar="NAME",
                   help="pin the adapter for this payload, e.g. kiro")
    h.add_argument("--in-chat", dest="in_chat", action="store_true",
                   help="ask the agent to analyze its own work instead of an LLM CLI")
    h.add_argument("--end", action="store_true",
                   help="this is the end-of-turn hook (drain the edit buffer)")
    h.set_defaults(func=_cmd_hook)
    w = sub.add_parser("_worker")
    w.add_argument("job")
    w.set_defaults(func=_cmd_worker)

    return p


# Commands that must never trigger the implicit auto-setup (see main()).
_NO_AUTOSETUP = frozenset({"hook", "_worker", "install", "uninstall", "setup"})


def main(argv=None) -> int:
    # Windows consoles often default to cp1252, which can't encode the ✓/•/🧠
    # glyphs we print — that raises UnicodeEncodeError and crashes the command.
    # Force UTF-8 (with replacement) so output never crashes on any console.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    # Auto-setup: detect which harnesses are in use here and install their
    # hooks, so moving to a new project needs no extra step. If no external LLM
    # CLI is configured, prefer the active agent's own in-chat analysis path.
    #
    # Deliberately AFTER arg parsing, and skipped for some commands:
    #   * hook/_worker — the hook path has its own gated autosetup, and anything
    #     printed here would land on the harness's stdout JSON channel.
    #   * install/setup — they run their own setup explicitly (force=True).
    #   * uninstall — re-installing what the user just removed is never right.
    # Output goes to stderr for the same stdout-channel reason. Never fails loudly.
    if getattr(args, "cmd", None) not in _NO_AUTOSETUP:
        try:
            auto_in_chat = not insights.resolve_backend(config.load_config())
            actions = autosetup.run(in_chat=auto_in_chat)
            if actions:
                print("learnlance: configured hooks for " + ", ".join(actions),
                      file=sys.stderr)
                print("  (run `learnlance doctor` to check status)\n",
                      file=sys.stderr)
        except Exception:
            pass  # never block the CLI over autosetup

    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
