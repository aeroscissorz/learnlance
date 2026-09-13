"""Regression tests for the knowledge-graph provenance overhaul.

Locks down the schema v2 additions (sources, whys, session goal, file_index),
the v1 -> v2 migration, the de-densified linking policy, atomic saves, and the
visualization default that hides weak links.
"""
from __future__ import annotations

import json

from learnlance import core, graph, insights, viz
from learnlance.events import CodeEvent


def _ctx(when="t", session="s", cwd="/p", files=None, goal="",
         semantic_links=None):
    return {"when": when, "session": session, "cwd": cwd,
            "files": files or [], "goal": goal,
            "semantic_links": semantic_links or []}


def _topic(name, **extra):
    t = {"name": name, "category": "other", "level": "",
         "explanation": "", "why_here": "", "tags": [], "related": []}
    t.update(extra)
    return t


def test_load_migrates_a_v1_graph_and_backfills_provenance(tmp_path):
    v1 = {
        "nodes": {
            "delta-encoding": {
                "id": "delta-encoding", "name": "Delta encoding",
                "category": "algorithm", "level": "intermediate",
                "explanation": "Stores differences.", "count": 2,
                "first_seen": "t0", "last_seen": "t1",
                "tags": ["compression"],
                "examples": [
                    {"did": "x", "why_here": "uses delta()",
                     "files": ["app/delta.py", "tests/test_delta.py"],
                     "when": "t0", "session": "s"},
                    {"did": "y", "why_here": "uses delta()",
                     "files": ["app/delta.py"], "when": "t1", "session": "s2"},
                ],
                "placeholder": False,
            }
        },
        "edges": [],
        "sessions": {"s": {"cwd": "/p", "topics": ["delta-encoding"],
                           "first": "t0"}},
        "tag_index": {"compression": ["delta-encoding"]},
        "meta": {"turns": 2},
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(v1), encoding="utf-8")

    g = graph.load(path)

    node = g["nodes"]["delta-encoding"]
    assert node["sources"] == {
        "app/delta.py": {"count": 2, "last": "t1"},
        "tests/test_delta.py": {"count": 1, "last": "t0"},
    }
    assert node["whys"] == {"uses delta()": {"count": 2, "last": "t1"}}
    assert g["file_index"] == {
        "app/delta.py": ["delta-encoding"],
        "tests/test_delta.py": ["delta-encoding"],
    }
    assert g["meta"]["schema"] == 2
    assert g["sessions"]["s"]["goal"] == ""


def test_update_records_sources_whys_and_file_index():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [
        _topic("Delta encoding", why_here="uses delta()",
               files=["app/delta.py", "app\\delta.py"]),
    ]}, _ctx())

    node = g["nodes"]["delta-encoding"]
    # Backslashes and forward slashes fold to one index key.
    assert node["sources"] == {"app/delta.py": {"count": 2, "last": "t"}}
    assert node["whys"] == {"uses delta()": {"count": 1, "last": "t"}}
    assert g["file_index"] == {"app/delta.py": ["delta-encoding"]}


def test_update_falls_back_to_turn_level_files():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Delta encoding")]},
                 _ctx(files=["app/delta.py"]))
    assert g["nodes"]["delta-encoding"]["sources"]["app/delta.py"]["count"] == 1


def test_session_goal_is_stored():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Delta encoding")]},
                 _ctx(goal="implement a diffing helper"))
    assert g["sessions"]["s"]["goal"] == "implement a diffing helper"


def test_weak_tags_do_not_drive_shared_tag_links():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("A", tags=["algorithm"])]},
                 _ctx(session="s1"))
    graph.update(g, {"did": "x", "topics": [_topic("B", tags=["algorithm"])]},
                 _ctx(session="s2"))
    assert g["edges"] == []


def test_meaningful_tags_still_link():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Caching", tags=["caching"])]},
                 _ctx(session="s1"))
    graph.update(g, {"did": "x", "topics": [_topic("Debouncing", tags=["caching"])]},
                 _ctx(session="s2"))
    assert [e for e in g["edges"] if e["type"] == "shared-tag"] != []


def test_co_occurs_star_is_capped():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [
        _topic(n) for n in ("A", "B", "C", "D", "E")
    ]}, _ctx())
    # The hub links to only the top 3 siblings, not every other topic.
    assert len(g["edges"]) == 3
    assert all(e["source"] == "a" or e["target"] == "a" for e in g["edges"])


def test_save_is_atomic_and_round_trips(home, tmp_path):
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Delta encoding")]}, _ctx())
    path = tmp_path / "graph.json"

    graph.save(g, path)

    assert path.exists()
    assert not path.with_name("graph.json.tmp").exists()
    assert graph.load(path) == g


def test_remove_node_cleans_file_index():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("A", files=["f.py"])]}, _ctx())
    assert g["file_index"]["f.py"] == ["a"]

    assert graph.remove_node(g, "a") is True
    assert g["file_index"] == {}


def test_viz_hides_weak_links_by_default(home, tmp_path):
    out = tmp_path / "graph.html"
    viz.render_html(graph.empty(), out)
    html = out.read_text(encoding="utf-8")
    assert "minWeight:2" in html
    assert 'value="2"' in html


def test_candidate_nodes_scores_by_shared_tag_and_name():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Data compression", tags=["compression"])]},
                 _ctx(session="seed"))
    graph.update(g, {"did": "x", "topics": [_topic("Delta encoding", tags=["diffing"])]},
                 _ctx(session="seed2"))

    cands = graph.candidate_nodes(g, _topic("Delta encoding", tags=["compression"]))
    assert [c["name"] for c in cands] == ["Data compression"]


def test_candidate_nodes_ignores_weak_tags():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("A", tags=["algorithm"])]},
                 _ctx(session="s1"))
    assert graph.candidate_nodes(g, _topic("B", tags=["algorithm"])) == []


def test_link_candidates_resolves_only_known_targets(monkeypatch):
    monkeypatch.setattr(insights, "_run_cli", lambda cfg, prompt: json.dumps({
        "links": [
            {"topic": "Delta encoding", "target": "Data compression",
             "why": "a form of compression"},
            {"topic": "Delta encoding", "target": "Not a real candidate",
             "why": "hallucinated"},
        ],
    }))

    topics = [{"name": "Delta encoding", "explanation": "Stores differences."}]
    candidates = {"Delta encoding": [
        {"id": "data-compression", "name": "Data compression",
         "category": "algorithm", "tags": [], "explanation": ""},
    ]}

    assert insights.link_candidates({}, topics, candidates) == [
        {"a": "delta-encoding", "b": "data-compression", "why": "a form of compression"},
    ]


def test_update_applies_semantic_links_with_why():
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Data compression")]},
                 _ctx(session="seed"))

    graph.update(g, {"did": "x", "topics": [_topic("Delta encoding")]},
                 _ctx(semantic_links=[{"a": "delta-encoding",
                                       "b": "data-compression",
                                       "why": "a form of compression"}]))

    edge = [e for e in g["edges"]
            if {e["source"], e["target"]} == {"delta-encoding", "data-compression"}][0]
    assert edge["type"] == "related"
    assert edge["why"] == "a form of compression"


def test_merge_result_skips_candidate_linking_by_default(cfg, project, home, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("candidate linking should not run by default")

    monkeypatch.setattr(insights, "resolve_backend", boom)
    monkeypatch.setattr(insights, "link_candidates", boom)

    event = CodeEvent("kiro", "after_agent_turn", cwd=str(project), session="s")
    result = {"did": "x", "topics": [_topic("Delta encoding")]}
    assert core.merge_result(cfg, event, result, announce=False) is True


def test_merge_result_applies_candidate_links_when_enabled(cfg, project, home,
                                                           monkeypatch):
    # Seed an existing concept the agent can link the new topic to.
    g = graph.empty()
    graph.update(g, {"did": "x", "topics": [_topic("Data compression", tags=["compression"])]},
                 {"when": "t0", "session": "seed", "cwd": str(project), "files": []})
    graph.save_project(g, str(project))

    monkeypatch.setattr(insights, "resolve_backend", lambda c: ["fake"])
    monkeypatch.setattr(insights, "link_candidates",
                        lambda cfg, topics, candidates: [
                            {"a": "delta-encoding", "b": "data-compression",
                             "why": "delta is a form of compression"},
                        ])

    event = CodeEvent("kiro", "after_agent_turn", cwd=str(project), session="s",
                      files=["app/delta.py"], user_prompt="learn delta")
    result = {"did": "x", "topics": [
        _topic("Delta encoding", category="algorithm", level="intermediate",
               explanation="Stores differences.", tags=["compression"],
               files=["app/delta.py"]),
    ]}

    assert core.merge_result(cfg, event, result, announce=False,
                             link_candidates=True) is True

    g2 = graph.load_project(str(project))
    edge = [e for e in g2["edges"]
            if {e["source"], e["target"]} == {"delta-encoding", "data-compression"}][0]
    assert edge["type"] == "related"
    assert edge["why"] == "delta is a form of compression"
