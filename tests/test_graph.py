"""Lock-in tests for the LangGraph-based turn graph.

These don't run any LLM calls — they just compile the graph against a mocked
orchestrator and assert that the node set, the entry edge from START, the
conditional fan-out from `parse`, and the convergence on `apply → narrator
→ END` are intact. Catches accidental wiring breakage during refactors.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from taleforge.agents.graph import build_turn_graph


def _compiled():
    return build_turn_graph(MagicMock())


def test_graph_compiles_and_has_expected_nodes():
    g = _compiled().get_graph()
    nodes = set(g.nodes.keys())
    expected = {
        "__start__", "__end__",
        "parse",
        "lawyer_attack", "lawyer_skill",
        "director_talk",
        "move", "look", "inventory", "other",
        "apply", "narrator",
    }
    assert nodes == expected, f"unexpected nodes: {nodes ^ expected}"


def test_start_goes_to_parse():
    g = _compiled().get_graph()
    edges = [(e.source, e.target) for e in g.edges]
    assert ("__start__", "parse") in edges


def test_all_resolvers_except_inventory_funnel_through_apply():
    """Single call site for Keeper.try_apply across the graph."""
    g = _compiled().get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    for resolver in ("lawyer_attack", "lawyer_skill", "director_talk", "move", "look", "other"):
        assert (resolver, "apply") in edges, f"{resolver} → apply missing"
    # inventory bypasses both apply and narrator.
    assert ("inventory", "apply") not in edges
    assert ("inventory", "__end__") in edges


def test_apply_runs_before_narrator_which_ends_the_graph():
    g = _compiled().get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("apply", "narrator") in edges
    assert ("narrator", "__end__") in edges


def test_parse_fan_out_covers_all_seven_intents():
    g = _compiled().get_graph()
    # Conditional edges show up as targets from `parse` with optional labels.
    targets_from_parse = {e.target for e in g.edges if e.source == "parse"}
    assert targets_from_parse == {
        "lawyer_attack", "lawyer_skill",
        "director_talk",
        "move", "look", "inventory", "other",
    }


def test_graph_renders_mermaid():
    """The auto-generated mermaid is what we paste into the README."""
    mermaid = _compiled().get_graph().draw_mermaid()
    assert "graph TD" in mermaid
    assert "parse" in mermaid and "narrator" in mermaid and "apply" in mermaid
    # Conditional fan-out labels survive the render.
    assert "attack" in mermaid and "talk" in mermaid
