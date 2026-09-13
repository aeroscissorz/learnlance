"""Persistent knowledge graph. Nodes = concepts you've encountered, edges =
relationships between them. Stored as a single JSON file so it's easy to inspect,
back up, or version.

Connectivity model — how the graph stays "one connected web" instead of islands:
  * related    : the model names adjacent concepts for each topic. Related names
                 are linked only when they are already real learned nodes; this
                 prevents speculative concepts from polluting the graph.
  * co-occurs  : concepts learned in the same turn are linked.
  * shared-tag : every concept carries tags; a new concept links to EXISTING
                 concepts (from any past session) that share a tag. This is what
                 connects the graph globally, based on relatedness.
Each unordered pair has exactly one edge, whose `type` is the strongest relation
seen and whose `weight` grows each time the relationship is reinforced.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config

# Current on-disk graph format. Bumped when fields are added so `load` can
# migrate older files in place instead of silently losing them.
_SCHEMA = 2

# Stronger relation wins the edge's displayed type; weight still accumulates.
_PRIORITY = {"co-occurs": 3, "related": 2, "shared-tag": 1}
# Cap how many existing same-tag neighbours a new concept links to per tag.
# One strong neighbour beats three weak ones: broad tags were what turned the
# graph into a hairball where every node connected to every other node.
_MAX_TAG_NEIGHBOURS = 1
# How many siblings the turn's primary concept links to. A full N-1 star made a
# single rich turn denser than it deserved.
_MAX_COOCCUR = 3
# How many existing nodes the agent is shown as link candidates per new concept.
_MAX_CANDIDATES = 5
# Tags too broad to carry connective meaning. They still live on the node for
# display, but they don't drive shared-tag linking.
_WEAK_TAGS = {
    "algorithm", "algorithms", "code", "coding", "concept", "concepts",
    "data", "frontend", "backend", "general", "html", "css", "http",
    "java", "javascript", "typescript", "go", "rust", "python",
    "language", "misc", "other", "pattern", "patterns", "programming",
    "software", "technique", "techniques", "web",
}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unknown"


def _norm_tag(tag: str) -> str:
    return slug(tag)


def _name_tokens(name: str) -> set[str]:
    """Lowercase tokens of length >= 3 for cheap name-overlap scoring."""
    return set(re.findall(r"[a-z][a-z0-9_-]{2,}", (name or "").lower()))


def load(path: Path | None = None) -> dict:
    path = path or config.GRAPH_PATH
    if path.exists():
        try:
            g = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            g = {}
    else:
        g = {}
    g.setdefault("nodes", {})
    g.setdefault("edges", [])
    g.setdefault("sessions", {})
    g.setdefault("tag_index", {})
    g.setdefault("file_index", {})
    g.setdefault("meta", {"turns": 0})
    _migrate(g)
    return g


def _migrate(g: dict) -> None:
    """Upgrade a v1 graph in place to the current schema.

    v1 kept "where" and "why" only inside each node's capped `examples` list.
    Backfill those into the first-class `sources`/`whys` maps so existing graphs
    don't start empty, then rebuild the reverse file index.
    """
    if g.get("meta", {}).get("schema", 1) >= _SCHEMA:
        return
    for nid, node in g.get("nodes", {}).items():
        sources = node.setdefault("sources", {})
        whys = node.setdefault("whys", {})
        for ex in node.get("examples", []) or []:
            when = ex.get("when", "")
            for f in ex.get("files", []) or []:
                key = _norm_source(f)
                if not key:
                    continue
                src = sources.setdefault(key, {"count": 0, "last": when})
                src["count"] += 1
                src["last"] = when
            why = (ex.get("why_here") or "").strip()
            if why:
                w = whys.setdefault(why, {"count": 0, "last": when})
                w["count"] += 1
                w["last"] = when
    g["file_index"] = _rebuild_file_index(g)
    for session in g.get("sessions", {}).values():
        session.setdefault("goal", "")
    g.setdefault("meta", {}).setdefault("turns", 0)
    g["meta"]["schema"] = _SCHEMA


def load_project(cwd: str) -> dict:
    """Load the graph for a specific project. Migrates legacy graph on first call."""
    config.migrate_legacy_graph(cwd)
    path = config.project_graph_path(cwd)
    return load(path)


def save(graph: dict, path: Path | None = None) -> None:
    path = path or config.GRAPH_PATH
    config.ensure_home()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_project(graph: dict, cwd: str) -> None:
    """Save the graph for a specific project."""
    path = config.project_graph_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    save(graph, path)


def empty() -> dict:
    """A fresh, empty graph (used by `learnlance clear`)."""
    return {"nodes": {}, "edges": [], "sessions": {}, "tag_index": {},
            "file_index": {}, "meta": {"turns": 0, "schema": _SCHEMA}}


# --------------------------------------------------------------------------- #
# removal
# --------------------------------------------------------------------------- #
def find_concepts(graph: dict, query: str) -> list[str]:
    """Return node ids matching a query: exact slug first, else name substring.

    Placeholder nodes are ignored — you can only target real concepts.
    """
    nodes = graph.get("nodes", {})
    qs = slug(query)
    if qs in nodes and not nodes[qs].get("placeholder"):
        return [qs]
    q = (query or "").strip().lower()
    if not q:
        return []
    return sorted(
        nid for nid, n in nodes.items()
        if not n.get("placeholder") and q in (n.get("name", "").lower())
    )


def remove_node(graph: dict, nid: str) -> bool:
    """Delete a node and every trace of it (edges, tag index, session lists)."""
    if graph.get("nodes", {}).pop(nid, None) is None:
        return False
    graph["edges"] = [e for e in graph.get("edges", [])
                      if e["source"] != nid and e["target"] != nid]
    for bucket in graph.get("tag_index", {}).values():
        if nid in bucket:
            bucket.remove(nid)
    graph["tag_index"] = {t: b for t, b in graph.get("tag_index", {}).items() if b}
    for bucket in graph.get("file_index", {}).values():
        if nid in bucket:
            bucket.remove(nid)
    graph["file_index"] = {f: b for f, b in graph.get("file_index", {}).items() if b}
    for s in graph.get("sessions", {}).values():
        if nid in s.get("topics", []):
            s["topics"].remove(nid)
    return True


def prune_orphan_placeholders(graph: dict) -> list[str]:
    """Drop placeholder nodes left with no edges (e.g. after removing a concept).

    Returns the names of pruned placeholders.
    """
    linked: set[str] = set()
    for e in graph.get("edges", []):
        linked.add(e["source"])
        linked.add(e["target"])
    removed: list[str] = []
    for nid, n in list(graph.get("nodes", {}).items()):
        if n.get("placeholder") and nid not in linked:
            graph["nodes"].pop(nid, None)
            removed.append(n.get("name", nid))
    return removed


# --------------------------------------------------------------------------- #
# edges
# --------------------------------------------------------------------------- #
def _pair_key(a: str, b: str) -> str:
    return "::".join(sorted((a, b)))


def _find_edge(graph: dict, a: str, b: str) -> dict | None:
    key = _pair_key(a, b)
    for e in graph["edges"]:
        if _pair_key(e["source"], e["target"]) == key:
            return e
    return None


def _link(graph: dict, a: str, b: str, etype: str, tag: str | None = None,
          why: str | None = None, seen: set[str] | None = None) -> None:
    if a == b or a not in graph["nodes"] or b not in graph["nodes"]:
        return
    key = _pair_key(a, b)
    e = _find_edge(graph, a, b)
    if e is None:
        graph["edges"].append({
            "source": a, "target": b, "type": etype, "weight": 1,
            "tags": ([tag] if tag else []), "why": why or "",
        })
        if seen is not None:
            seen.add(key)
        return
    # Type and tags can still upgrade on a repeat link, but weight only grows once
    # per turn: a pair linked by `related`, `shared-tag`, and `co-occurs` in one
    # turn is a single relationship, not three separate reinforcements.
    if seen is None or key not in seen:
        e["weight"] = e.get("weight", 1) + 1
        if seen is not None:
            seen.add(key)
    if _PRIORITY.get(etype, 0) > _PRIORITY.get(e.get("type", "related"), 0):
        e["type"] = etype
    if tag and tag not in e.setdefault("tags", []):
        e["tags"].append(tag)
    if why and not e.get("why"):
        e["why"] = why


# --------------------------------------------------------------------------- #
# nodes / tags
# --------------------------------------------------------------------------- #
def _placeholder(nid: str, name: str, when: str) -> dict:
    return {
        "id": nid, "name": name, "category": "other", "level": "",
        "explanation": "", "count": 0, "first_seen": when, "last_seen": when,
        "examples": [], "tags": [], "sources": {}, "whys": {}, "placeholder": True,
    }


def _register_tags(graph: dict, nid: str, tags: list[str]) -> list[str]:
    """Add node to the tag index; return the normalized tags."""
    norm = []
    for t in tags:
        nt = _norm_tag(t)
        if not nt:
            continue
        norm.append(nt)
        bucket = graph["tag_index"].setdefault(nt, [])
        if nid not in bucket:
            bucket.append(nid)
    return norm


def _norm_source(path: str) -> str:
    """Normalize a file path so the same file always maps to one index key."""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _rebuild_file_index(g: dict) -> dict:
    """Recompute the reverse file -> concepts index from each node's sources."""
    index: dict[str, list[str]] = {}
    for nid, node in g.get("nodes", {}).items():
        for f in (node.get("sources") or {}):
            if not f:
                continue
            bucket = index.setdefault(f, [])
            if nid not in bucket:
                bucket.append(nid)
    return index


def _merge_sources(graph: dict, nid: str, files: list[str], when: str) -> None:
    """Record the files where a concept was used, and update the reverse index."""
    node = graph["nodes"][nid]
    sources = node.setdefault("sources", {})
    index = graph.setdefault("file_index", {})
    for f in files or []:
        key = _norm_source(f)
        if not key:
            continue
        src = sources.setdefault(key, {"count": 0, "last": when})
        src["count"] += 1
        src["last"] = when
        bucket = index.setdefault(key, [])
        if nid not in bucket:
            bucket.append(nid)


def _merge_why(graph: dict, nid: str, why: str, when: str) -> None:
    """Record one rationale snippet for a concept, deduped by text."""
    why = (why or "").strip()
    if not why:
        return
    node = graph["nodes"][nid]
    whys = node.setdefault("whys", {})
    w = whys.setdefault(why, {"count": 0, "last": when})
    w["count"] += 1
    w["last"] = when


def candidate_nodes(graph: dict, topic: dict,
                    max_candidates: int = _MAX_CANDIDATES) -> list[dict]:
    """Return existing nodes most likely to relate to `topic`.

    Uses the cheap signals the graph already has — shared (non-weak) tags and
    name-token overlap — to surface a small candidate list for the agent to rank,
    instead of sending it the whole graph.
    """
    nid = slug((topic.get("name") or "").strip())
    tags = {_norm_tag(t) for t in (topic.get("tags") or [])} - _WEAK_TAGS
    name_tokens = _name_tokens(topic.get("name", ""))
    scored: list[tuple[int, str, dict]] = []
    for other_id, node in graph.get("nodes", {}).items():
        if other_id == nid or node.get("placeholder"):
            continue
        shared = tags & set(node.get("tags") or [])
        score = len(shared) * 2 + len(name_tokens & _name_tokens(node.get("name", "")))
        if score > 0:
            scored.append((score, other_id, node))
    scored.sort(key=lambda item: -item[0])
    return [{
        "id": node.get("id") or other_id,
        "name": node.get("name", other_id),
        "category": node.get("category", ""),
        "tags": node.get("tags", []),
        "explanation": (node.get("explanation") or "")[:200],
    } for _, other_id, node in scored[:max_candidates]]


def update(graph: dict, insights: dict, context: dict) -> list[str]:
    """Merge one turn's insights into the graph. Returns names of NEW topics."""
    when = context.get("when", "")
    session = context.get("session", "")
    cwd = context.get("cwd", "")
    files = context.get("files", [])
    goal = context.get("goal", "")
    did = insights.get("did", "")

    graph["meta"]["turns"] = graph.get("meta", {}).get("turns", 0) + 1

    new_names: list[str] = []
    touched_ids: list[str] = []
    seen: set[str] = set()

    for t in insights.get("topics", []):
        name = (t.get("name") or "").strip()
        if not name:
            continue
        nid = slug(name)
        node = graph["nodes"].get(nid)
        was_placeholder = bool(node and node.get("placeholder"))
        if node is None or was_placeholder:
            if node is None:
                new_names.append(name)
                node = _placeholder(nid, name, when)
                graph["nodes"][nid] = node
            # Upgrade placeholder -> real concept (fuses clusters at this node).
            node["placeholder"] = False
            node["category"] = t.get("category", node.get("category", "other"))
            node["level"] = t.get("level", node.get("level", ""))

        node["count"] += 1
        node["last_seen"] = when
        if len(t.get("explanation", "")) > len(node.get("explanation", "")):
            node["explanation"] = t.get("explanation", "")

        # Where used + why: first-class maps, not just the capped examples list.
        topic_files = t.get("files") or files
        _merge_sources(graph, nid, topic_files, when)
        _merge_why(graph, nid, t.get("why_here", ""), when)

        # tags: union over time, and index for cross-session linking
        incoming = _register_tags(graph, nid, t.get("tags", []) or [])
        node_tags = node.setdefault("tags", [])
        for nt in incoming:
            if nt not in node_tags:
                node_tags.append(nt)

        node["examples"] = (node.get("examples", []) + [{
            "did": did, "why_here": t.get("why_here", ""),
            "files": topic_files, "when": when, "session": session,
        }])[-8:]

        touched_ids.append(nid)

        # 1) explicit related concepts. Never create a node from a suggestion
        # alone: only concepts evidenced by a real turn may enter the graph.
        for rel in t.get("related", []) or []:
            rname = (rel or "").strip()
            if not rname:
                continue
            rid = slug(rname)
            if rid in graph["nodes"] and not graph["nodes"][rid].get("placeholder"):
                _link(graph, nid, rid, "related", seen=seen)

        # 2) shared-tag links to EXISTING concepts across all past sessions
        linked_this_node: set[str] = set()
        for nt in incoming:
            if nt in _WEAK_TAGS:
                continue
            bucket = graph["tag_index"].get(nt, [])
            # prefer the most-reinforced existing real neighbours
            neighbours = [
                b for b in bucket
                if b != nid and not graph["nodes"].get(b, {}).get("placeholder")
            ]
            neighbours.sort(key=lambda b: -graph["nodes"].get(b, {}).get("count", 0))
            for b in neighbours[:_MAX_TAG_NEIGHBOURS]:
                if b in linked_this_node:
                    continue
                linked_this_node.add(b)
                _link(graph, nid, b, "shared-tag", tag=nt, seen=seen)

    # 2b) agent-validated semantic links: the LLM ranked a bounded candidate list
    #     of existing nodes and chose real relationships, each with a reason.
    for link in context.get("semantic_links", []) or []:
        a = link.get("a", "")
        b = link.get("b", "")
        if a and b:
            _link(graph, a, b, "related", why=link.get("why", ""), seen=seen)

    # 3) concepts learned together this turn. Link each to the turn's PRIMARY
    #    concept (topics come "most important first") rather than fully
    #    connecting them — a star of N-1 edges instead of a quadratic clique,
    #    which is what turned single rich turns into a hairball.
    if touched_ids:
        hub = touched_ids[0]
        for other in touched_ids[1:1 + _MAX_COOCCUR]:
            _link(graph, hub, other, "co-occurs", seen=seen)

    if session:
        s = graph["sessions"].setdefault(session, {"cwd": cwd, "goal": goal, "topics": [], "first": when})
        s["last"] = when
        if goal:
            s["goal"] = goal
        for nid in touched_ids:
            if nid not in s["topics"]:
                s["topics"].append(nid)

    return new_names
