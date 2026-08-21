"""learnlance command-line interface."""
from __future__ import annotations

import argparse
import sys
import webbrowser

from . import config, graph, hook, insights, install, viz


def _cmd_install(args):
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
    print(install.uninstall_hook())


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


def _cmd_hook(args):
    hook.run_hook()


def _cmd_worker(args):
    hook.run_worker(args.job)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="learnlance",
        description="Turn what Claude Code builds into a growing knowledge graph.")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("install", help="install the Claude Code Stop hook").set_defaults(func=_cmd_install)
    sub.add_parser("uninstall", help="remove the Stop hook").set_defaults(func=_cmd_uninstall)

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

    h = sub.add_parser("hook", help="(internal) Stop-hook entry point")
    h.set_defaults(func=_cmd_hook)
    w = sub.add_parser("_worker", help=argparse.SUPPRESS)
    w.add_argument("job")
    w.set_defaults(func=_cmd_worker)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
