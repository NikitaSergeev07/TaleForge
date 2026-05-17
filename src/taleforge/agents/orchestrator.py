"""Orchestrator — compiles + runs the per-turn LangGraph.

Each turn is a single ``graph.ainvoke`` over the StateGraph built in
``agents/graph.py``. The graph encodes the deterministic call sequence:
``parse → (lawyer_attack | lawyer_skill | director_talk | move | look | other)
→ apply → narrator → END``, with ``inventory`` bypassing both apply and
narrator (no LLM beyond the parser).

The Orchestrator itself owns:
- the shared :class:`MinimaxClient` (for cost tracking)
- the :class:`WorldStateKeeper` (the only state writer)
- the three sub-agents instantiated lazily (Narrator, RulesLawyer, NPCDirector)
- the compiled graph (built once in ``__init__``, reused across turns)
- per-turn JSONL trace emission (``_trace_turn``)

External contract (``take_turn`` signature + :class:`TurnResult` shape) stays
the same so the CLI, the web API, and the bench keep working unchanged.

TODO: NPCDirector(react) on attack and (scene_entry) on move from the spec's
call graph — the Narrator currently handles scene transitions adequately.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..llm.minimax import MinimaxClient, strip_think_blocks
from ..llm.prompts import ORCHESTRATOR_PARSE
from ..models import Action, Outcome, WorldState
from ..state.store import WorldStateKeeper
from .base import BaseAgent
from .graph import build_turn_graph
from .narrator import Narrator
from .npc_director import NPCDirector
from .rules_lawyer import RulesLawyer


_VALID_INTENTS = {"attack", "move", "talk", "skill_check", "inventory", "look", "other"}


def _extract_json_blob(text: str) -> str:
    text = text.strip()
    if text.startswith("{"):
        return text
    i, j = text.find("{"), text.rfind("}")
    return text[i : j + 1] if i >= 0 and j > i else text


@dataclass
class TurnResult:
    turn: int
    action: Action
    outcome: Outcome
    prose: str
    applied_mutations: list[tuple[dict, list[str]]] = field(default_factory=list)
    rejected_mutations: list[tuple[dict, str]] = field(default_factory=list)
    turn_cost_usd: float = 0.0


class Orchestrator(BaseAgent):
    """Deterministic router from raw player input → narrated turn."""

    name = "orchestrator"

    def __init__(
        self,
        client: MinimaxClient,
        keeper: WorldStateKeeper,
        *,
        narrator: Narrator | None = None,
        rules_lawyer: RulesLawyer | None = None,
        npc_director: NPCDirector | None = None,
        settings: Settings | None = None,
        language: str = "en",
    ) -> None:
        super().__init__(client, settings=settings)
        self.model = self.settings.model_fast
        self.keeper = keeper
        self.language = language  # propagated to Narrator + NPCActor as a hint
        self.narrator = narrator or Narrator(client, settings=self.settings)
        self.lawyer = rules_lawyer or RulesLawyer(client, settings=self.settings)
        self.director = npc_director or NPCDirector(client, settings=self.settings)
        # Compile the LangGraph once; reused across every turn of this session.
        self.graph = build_turn_graph(self)

    # ── public ──────────────────────────────────────────────────────

    async def take_turn(self, raw_input: str, *, language: str | None = None) -> TurnResult:
        """Run one turn through the compiled LangGraph and pack the result."""
        lang = language or self.language
        cost_before = self.client.total_cost_usd

        final_state = await self.graph.ainvoke({
            "raw_input": raw_input,
            "language": lang,
        })

        action: Action = final_state["action"]
        outcome: Outcome = final_state["outcome"]
        prose: str = final_state.get("prose", "")
        applied = list(final_state.get("applied_mutations", []))
        rejected = list(final_state.get("rejected_mutations", []))

        self.keeper.advance_turn()
        result = TurnResult(
            turn=self.keeper.state.turn,
            action=action,
            outcome=outcome,
            prose=prose,
            applied_mutations=applied,
            rejected_mutations=rejected,
            turn_cost_usd=self.client.total_cost_usd - cost_before,
        )
        self._trace_turn(result)
        return result

    async def parse_action(self, raw_input: str, state: WorldState) -> Action:
        view = self._scene_for_parser(state)
        result = await self.client.chat(
            [
                {"role": "system", "content": ORCHESTRATOR_PARSE},
                {
                    "role": "user",
                    "content": json.dumps({"input": raw_input, "scene": view}),
                },
            ],
            model=self.model,
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        body = _extract_json_blob(strip_think_blocks(result.content))
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return Action(raw=raw_input, intent="other")
        intent = parsed.get("intent", "other")
        if intent not in _VALID_INTENTS:
            intent = "other"
        return Action(
            raw=raw_input,
            intent=intent,
            target_ids=list(parsed.get("target_ids") or []),
            parameters=dict(parsed.get("parameters") or {}),
        )

    # ── routing helpers (called by graph nodes) ────────────────────

    def _handle_move(self, action: Action) -> Outcome:
        state = self.keeper.state
        player = state.entities[state.player_id]
        loc = state.locations.get(player.location_id) if player.location_id else None
        if loc is None:
            return Outcome(success=False, public_facts=["You are nowhere; cannot move."])

        direction = str(action.parameters.get("direction", "")).lower().strip()
        target: str | None = None
        if direction and direction in loc.exits:
            target = loc.exits[direction]
        else:
            for tid in action.target_ids:
                if tid in state.locations:
                    target = tid
                    break
                if tid in loc.exits.values():
                    target = tid
                    break
        if target is None:
            return Outcome(success=False, public_facts=["You can't go that way."])

        return Outcome(
            success=True,
            state_mutations=[{
                "op": "move_entity",
                "args": {"entity_id": player.id, "to_location_id": target},
            }],
            public_facts=[f"You head toward {state.locations[target].name}."],
        )

    # ── helpers ────────────────────────────────────────────────────

    def _render_inventory(self) -> str:
        inv = self.keeper.state.entities[self.keeper.state.player_id].inventory
        gp_count = inv.count("gp")
        items = [i for i in inv if i != "gp"]
        items_part = ", ".join(items) if items else "no gear"
        return f"Inventory — {items_part}; {gp_count} gp."

    def _scene_for_parser(self, state: WorldState) -> dict[str, Any]:
        player = state.entities[state.player_id]
        loc = state.locations.get(player.location_id) if player.location_id else None
        if loc is None:
            return {"location_id": None, "exits": {}, "visible_entity_ids": []}
        visible: list[dict[str, Any]] = []
        for eid in loc.present_entity_ids:
            if eid == player.id:
                continue
            ent = state.entities.get(eid)
            if not ent:
                continue
            visible.append({"id": ent.id, "name": ent.name, "kind": ent.kind})
        return {
            "location_id": loc.id,
            "location_name": loc.name,
            "exits": dict(loc.exits),
            "visible_entity_ids": [v["id"] for v in visible],
            "visible_entities": visible,
        }

    def _trace_turn(self, result: TurnResult) -> None:
        if self.keeper.trace_path is None:
            return
        record = {
            "kind": "turn",
            "ts": time.time(),
            "turn": result.turn,
            "intent": result.action.intent,
            "raw_input": result.action.raw,
            "target_ids": result.action.target_ids,
            "rolls": result.outcome.rolls,
            "applied_mutations": [m for m, _ in result.applied_mutations],
            "rejected_mutations": [
                {"mutation": m, "error": err} for m, err in result.rejected_mutations
            ],
            "prose": result.prose,
            "cost_usd": result.turn_cost_usd,
        }
        self.keeper.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.keeper.trace_path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


__all__ = ["Orchestrator", "TurnResult"]
