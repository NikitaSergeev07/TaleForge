"""LangGraph wiring for the per-turn call graph.

Each agent is a node; intents fan out via conditional_edges; mutations are
applied in a single ``apply`` node before the Narrator. The shape:

    START → parse → ┬→ lawyer_attack ──┐
                    │→ lawyer_skill ───┤
                    │→ director_talk ──┼→ apply → narrator → END
                    │→ move ───────────┤
                    │→ look ───────────┤
                    │→ other ──────────┘
                    └→ inventory ──────────────────────────→ END

The Orchestrator builds and compiles this graph once at ``__init__``;
``take_turn`` just calls ``graph.ainvoke`` and unpacks the final state.

Why structure it this way:
- ``parse`` is the only fan-out point. Routing is a pure function of
  ``state["action"].intent`` (no LLM in the router).
- All state-changing intents converge on a single ``apply`` node, so the
  Keeper's validate-then-apply contract has exactly one call site in the
  whole graph — matching the spec invariant "only the Keeper writes".
- ``inventory`` skips ``apply`` AND ``narrator`` (renders prose locally,
  no LLM beyond the parser) — keeps the design rule "inventory → CLI direct".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from langgraph.graph import END, START, StateGraph

from ..models import Action, Outcome

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class TurnState(TypedDict, total=False):
    """Channel state carried through the langgraph nodes for one turn."""

    raw_input: str
    language: str
    action: Action
    outcome: Outcome
    prose: str
    applied_mutations: list  # list[tuple[dict, list[str]]]
    rejected_mutations: list  # list[tuple[dict, str]]


def build_turn_graph(orchestrator: "Orchestrator"):
    """Compile and return the per-turn StateGraph.

    Nodes close over ``orchestrator`` so they can reach the Keeper and the
    sub-agents without threading them through state. The returned object is
    a compiled langgraph; call ``.ainvoke({"raw_input": ..., "language": ...})``.
    """

    # ── nodes ─────────────────────────────────────────────────────────

    async def parse_node(state: TurnState) -> dict:
        action = await orchestrator.parse_action(
            state["raw_input"], orchestrator.keeper.state
        )
        return {"action": action}

    def route_by_intent(state: TurnState) -> str:
        # parse_action clamps intent to the seven valid strings, so this is safe.
        return state["action"].intent

    async def lawyer_attack_node(state: TurnState) -> dict:
        outcome = await orchestrator.lawyer.resolve_attack(
            orchestrator.keeper.state, state["action"]
        )
        return {"outcome": outcome}

    async def lawyer_skill_node(state: TurnState) -> dict:
        outcome = await orchestrator.lawyer.resolve_skill_check(
            orchestrator.keeper.state, state["action"]
        )
        return {"outcome": outcome}

    async def director_talk_node(state: TurnState) -> dict:
        outcome = await orchestrator.director.talk(
            orchestrator.keeper.state,
            state["action"],
            language=state.get("language", "en"),
        )
        return {"outcome": outcome}

    def move_node(state: TurnState) -> dict:
        return {"outcome": orchestrator._handle_move(state["action"])}

    def look_node(state: TurnState) -> dict:
        return {
            "outcome": Outcome(
                success=True,
                public_facts=["You take in your surroundings."],
            )
        }

    def inventory_node(state: TurnState) -> dict:
        # Prose rendered locally (no LLM). Outcome carried for trace + bench.
        inv = orchestrator.keeper.state.entities[orchestrator.keeper.state.player_id].inventory
        listing = ", ".join(inv) if inv else "nothing"
        outcome = Outcome(
            success=True, public_facts=[f"You are carrying: {listing}."]
        )
        return {"outcome": outcome, "prose": orchestrator._render_inventory()}

    def other_node(state: TurnState) -> dict:
        return {
            "outcome": Outcome(
                success=False, public_facts=["Nothing happens."]
            )
        }

    def apply_node(state: TurnState) -> dict:
        """Sole call site for Keeper.try_apply across the whole graph."""
        applied: list = []
        rejected: list = []
        for mut in state["outcome"].state_mutations:
            ok, result = orchestrator.keeper.try_apply(mut)
            (applied if ok else rejected).append((mut, result))
        return {"applied_mutations": applied, "rejected_mutations": rejected}

    async def narrator_node(state: TurnState) -> dict:
        prose = await orchestrator.narrator.narrate(
            orchestrator.keeper.state,
            state["outcome"],
            language=state.get("language", "en"),
        )
        return {"prose": prose}

    # ── graph ─────────────────────────────────────────────────────────

    g: StateGraph = StateGraph(TurnState)
    g.add_node("parse", parse_node)
    g.add_node("lawyer_attack", lawyer_attack_node)
    g.add_node("lawyer_skill", lawyer_skill_node)
    g.add_node("director_talk", director_talk_node)
    g.add_node("move", move_node)
    g.add_node("look", look_node)
    g.add_node("inventory", inventory_node)
    g.add_node("other", other_node)
    g.add_node("apply", apply_node)
    g.add_node("narrator", narrator_node)

    g.add_edge(START, "parse")
    g.add_conditional_edges(
        "parse",
        route_by_intent,
        {
            "attack": "lawyer_attack",
            "skill_check": "lawyer_skill",
            "talk": "director_talk",
            "move": "move",
            "look": "look",
            "inventory": "inventory",
            "other": "other",
        },
    )
    # Resolvers (everything except inventory) converge on apply.
    for resolver in ("lawyer_attack", "lawyer_skill", "director_talk", "move", "look", "other"):
        g.add_edge(resolver, "apply")
    # apply → narrator → END
    g.add_edge("apply", "narrator")
    g.add_edge("narrator", END)
    # inventory skips both apply and narrator: prose is local, no mutations.
    g.add_edge("inventory", END)

    return g.compile()


__all__ = ["TurnState", "build_turn_graph"]
