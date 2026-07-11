"""Tests that the interpretation graph assembles and compiles."""

from __future__ import annotations


def test_graph_has_expected_nodes() -> None:
    from agent.graph import graph

    nodes = set(graph.nodes.keys())
    expected = {
        "reason",
        "retrieve",
        "route_search",
        "search",
        "classify",
        "emit_ops",
        "gen_snippet",
        "validate_snippet",
        "judge",
    }
    assert expected.issubset(nodes)


def test_graph_is_compiled() -> None:
    from agent.graph import graph

    # A compiled graph exposes an invoke method.
    assert hasattr(graph, "invoke")


def test_build_graph_returns_stategraph() -> None:
    from langgraph.graph import StateGraph

    from agent.graph import build_graph

    assert isinstance(build_graph(), StateGraph)
