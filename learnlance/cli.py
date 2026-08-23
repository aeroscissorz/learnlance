"""learnlance command-line interface."""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import webbrowser

from . import codesearch, config, graph, hook, insights, install, viz
from .spinner import Spinner


def _cmd_install(args):
    if getattr(args, "git", False):
        repo = os.path.abspath(args.path or os.getcwd())
        print(install.install_git_hook(repo))
        print("\nlearnlance will now learn from every commit in this repo — "
              "regardless of which editor or AI wrote the code.")
        return
    print(install.install_hook())
    cfg = config.load_config()
    backend = cfg.get("backend", "cli")
    if backend == "cli":
        if insights.resolve_claude_bin(cfg):
            print("\nBackend: cli — uses your logged-in `claude` (no API key needed).")
        else:
            print("\n⚠  Backend is 'cli' but `claude` wasn't found on PATH.")
            print("     learnlance config --claude-bin \"C:\\path\\to\\claude.cmd\"")
    else:
        if not config.get_api_key(cfg):
            print("\n⚠  Backend is 'api' but no API key is set:")
            print("     learnlance config --set-key sk-ant-...   (or export ANTHROPIC_API_KEY)")
    print("\nDone. New Claude Code sessions will now build your knowledge graph.")


def _cmd_uninstall(args):
    if getattr(args, "git", False):
        repo = os.path.abspath(args.path or os.getcwd())
        print(install.uninstall_git_hook(repo))
        return
    print(install.uninstall_hook())


def _cmd_doctor(args):
    import importlib.metadata as _md
    import platform
    import shutil as _sh

    cfg = config.load_config()
    ok = lambda b: "✓" if b else "✗"

    try:
        ver = _md.version("learnlance")
    except Exception:
        from . import __version__ as ver  # running from source

    git_ok = bool(_sh.which("git"))
    claude_ok = bool(insights.resolve_claude_bin(cfg))
    key_ok = bool(config.get_api_key(cfg))
    backend = cfg.get("backend", "cli")
    backend_ready = key_ok if backend == "api" else claude_ok

    # Claude Stop hook present?
    claude_hook = False
    try:
        sp = install.settings_path()
        if sp.exists():
            import json as _json
            data = _json.loads(sp.read_text(encoding="utf-8"))
            for grp in data.get("hooks", {}).get("Stop", []):
                if any(install.MARK in h.get("command", "") for h in grp.get("hooks", [])):
                    claude_hook = True
    except Exception:
        pass

    # git hook present in cwd?
    git_hook = False
    try:
        hd = install._hooks_dir(os.getcwd())
        pc = hd / "post-commit" if hd else None
        git_hook = bool(pc and pc.exists()
                        and install.GIT_MARK in pc.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass

    g = graph.load()
    concepts = sum(1 for n in g.get("nodes", {}).values() if not n.get("placeholder"))

    print("learnlance doctor\n")
    print(f"  learnlance        {ok(True)} {ver}")
    print(f"  python            {ok(True)} {platform.python_version()}")
    print(f"  git               {ok(git_ok)}")
    print(f"  backend           {backend}  ({ok(backend_ready)} ready)")
    if backend == "cli":
        print(f"  claude CLI        {ok(claude_ok)}"
              + ("" if claude_ok else "  → learnlance config --claude-bin PATH"))
    else:
        print(f"  api key           {ok(key_ok)}")
    print(f"  Claude Code hook  {ok(claude_hook)}" + ("" if claude_hook else "  → learnlance install"))
    print(f"  git hook (here)   {ok(git_hook)}" + ("" if git_hook else "  → learnlance install --git"))
    print(f"  concepts learned  {concepts}")


def _cmd_config(args):
    cfg = config.load_config()
    changed = False
    if args.backend is not None:
        cfg["backend"] = args.backend
        changed = True
    if args.claude_bin is not None:
        cfg["claude_bin"] = args.claude_bin
        changed = True
    if args.cli_model is not None:
        cfg["cli_model"] = args.cli_model
        changed = True
    if args.set_key is not None:
        cfg["api_key"] = args.set_key
        changed = True
    if args.model is not None:
        cfg["model"] = args.model
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
    # Show current state (mask the key)
    shown = dict(cfg)
    if shown.get("api_key"):
        shown["api_key"] = shown["api_key"][:7] + "…"
    key_src = "config" if cfg.get("api_key") else ("env" if config.get_api_key(cfg) else "MISSING")
    print(f"\nConfig ({config.CONFIG_PATH}):")
    for k, v in shown.items():
        print(f"  {k}: {v}")
    print(f"  api key source: {key_src}")


def _cmd_show(args):
    with Spinner("loading"):
        g = graph.load()
        path = viz.render_html(g)
    print(f"Graph written to {path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())


def _cmd_list(args):
    g = graph.load()
    nodes = [n for n in g.get("nodes", {}).values() if not n.get("placeholder")]
    if not nodes:
        print("Nothing learned yet. Install the hook and let Claude Code write some code.")
        return
    nodes.sort(key=lambda n: (-n.get("count", 0), n["name"].lower()))
    print(f"{len(nodes)} concepts learned:\n")
    for n in nodes:
        print(f"  • {n['name']}  [{n.get('category','')}·{n.get('level','')}]  seen {n.get('count',0)}×")
        if args.verbose and n.get("explanation"):
            print(f"      {n['explanation']}")


def _cmd_stats(args):
    g = graph.load()
    nodes = g.get("nodes", {})
    real = [n for n in nodes.values() if not n.get("placeholder")]
    cats: dict[str, int] = {}
    for n in real:
        cats[n.get("category", "other")] = cats.get(n.get("category", "other"), 0) + 1
    print(f"Concepts learned : {len(real)}")
    print(f"Related links    : {len(g.get('edges', []))}")
    print(f"Turns analyzed   : {g.get('meta', {}).get('turns', 0)}")
    print(f"Sessions         : {len(g.get('sessions', {}))}")
    if cats:
        print("\nBy category:")
        for c, n in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"  {c:18} {n}")


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
    g = graph.load()
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
        graph.save(g)
        viz.render_html(g)
        extra = f" (also pruned {len(pruned)} orphaned related node(s))" if pruned else ""
        print(f"Removed '{name}'.{extra}")
        return

    # No concept given -> wipe the whole graph.
    real = sum(1 for n in g.get("nodes", {}).values() if not n.get("placeholder"))
    if not args.yes and not _confirm(
        f"Clear the ENTIRE learning graph ({real} concepts)? This can't be undone."
    ):
        print("Aborted.")
        return
    graph.save(graph.empty())
    viz.render_html(graph.load())
    print("Learning graph cleared.")


def _cmd_add(args):
    cfg = config.load_config()
    # Backend readiness (mirrors the hook's check, but speaks to the user).
    if cfg.get("backend", "cli") == "api":
        if not config.get_api_key(cfg):
            print("Backend is 'api' but no API key is set. See `learnlance config`.")
            return
    elif not insights.resolve_claude_bin(cfg):
        print("`claude` not found on PATH. Set it: learnlance config --claude-bin PATH")
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

    api_key = config.get_api_key(cfg)
    try:
        with Spinner(f"analyzing '{topic}'"):
            result = insights.add_concept(cfg, api_key, topic, blob)
    except Exception as e:
        print(f"Could not analyze '{topic}': {e}")
        return

    if not result.get("topics"):
        print(f"Claude didn't find '{topic}' as a learnable concept in the code.")
        return

    g = graph.load()
    new_names = graph.update(g, result, {
        "when": _dt.datetime.now().isoformat(timespec="seconds"),
        "session": "manual", "cwd": root, "files": files,
    })
    graph.save(g)
    viz.render_html(g)
    added = ", ".join(t["name"] for t in result["topics"])
    tag = " (new)" if new_names else " (reinforced)"
    print(f"Added: {added}{tag}")


def _cmd_help(args):
    build_parser().print_help()


def _cmd_hook(args):
    hook.run_hook()


def _cmd_worker(args):
    hook.run_worker(args.job)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="learnlance",
        description="Turn what Claude Code builds into a growing knowledge graph.")
    # metavar lists only the user-facing commands; internal ones (hook,
    # _worker) are added below without help= so they stay out of the listing.
    sub = p.add_subparsers(
        dest="cmd",
        metavar="{install,uninstall,config,show,list,stats,clear,add,doctor,help}",
    )

    ins = sub.add_parser("install", help="install the Claude Code Stop hook (or --git)")
    ins.add_argument("--git", action="store_true",
                     help="install a git post-commit hook instead (works with any editor)")
    ins.add_argument("--path", metavar="DIR", help="repo dir for --git (default: current dir)")
    ins.set_defaults(func=_cmd_install)

    un = sub.add_parser("uninstall", help="remove the Stop hook (or --git)")
    un.add_argument("--git", action="store_true", help="remove the git post-commit hook instead")
    un.add_argument("--path", metavar="DIR", help="repo dir for --git (default: current dir)")
    un.set_defaults(func=_cmd_uninstall)

    c = sub.add_parser("config", help="view/set configuration")
    c.add_argument("--backend", choices=["cli", "api"],
                   help="cli = use logged-in `claude` (no key); api = Anthropic API")
    c.add_argument("--claude-bin", dest="claude_bin", metavar="PATH",
                   help="path to the claude executable (cli backend)")
    c.add_argument("--cli-model", dest="cli_model", metavar="MODEL",
                   help="optional model alias for the cli backend, e.g. haiku")
    c.add_argument("--set-key", dest="set_key", metavar="KEY", help="Anthropic API key (api backend)")
    c.add_argument("--model", help="model id for the api backend")
    c.add_argument("--enable", action="store_true")
    c.add_argument("--disable", action="store_true")
    c.add_argument("--background", choices=["on", "off"], help="run API work detached")
    c.add_argument("--max-topics", dest="max_topics", type=int)
    c.set_defaults(func=_cmd_config)

    s = sub.add_parser("show", help="render + open the HTML knowledge graph")
    s.add_argument("--no-open", action="store_true", help="just write the file")
    s.set_defaults(func=_cmd_show)

    l = sub.add_parser("list", help="list learned concepts in the terminal")
    l.add_argument("-v", "--verbose", action="store_true")
    l.set_defaults(func=_cmd_list)

    sub.add_parser("stats", help="summary counts").set_defaults(func=_cmd_stats)

    cl = sub.add_parser("clear", help="clear the whole graph, or one concept")
    cl.add_argument("concept", nargs="*",
                    help="concept name to remove; omit to clear the entire graph")
    cl.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
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
    h.set_defaults(func=_cmd_hook)
    w = sub.add_parser("_worker")
    w.add_argument("job")
    w.set_defaults(func=_cmd_worker)

    return p


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
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
