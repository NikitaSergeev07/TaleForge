"""NPCDirector — picks the right NPC and routes the player utterance.

Strict router, per design rule #3. The Director:

- Picks ONE target NPC for a ``talk`` action (explicit ``target_ids`` win;
  otherwise falls back to "the only NPC in the player's room").
- Looks up (or creates) the per-NPC :class:`NPCActor` instance so each NPC has
  its OWN conversation history that survives across turns.
- Calls ``actor.speak``, then translates the structured :class:`NPCResponse`
  into a tool-call-style :class:`Outcome` that the Keeper applies and the
  Narrator narrates.

The Director never plays a character itself. The Director never mutates state.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..llm.minimax import MinimaxClient
from ..models import NPC, Action, Outcome, WorldState
from .base import BaseAgent
from .npc_actor import NPCActor, NPCParseError


class NPCDirector(BaseAgent):
    """Router from player input → the right per-NPC NPCActor."""

    name = "npc_director"

    def __init__(
        self,
        client: MinimaxClient,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(client, settings=settings)
        # Director itself doesn't hit the LLM in this implementation; it just
        # picks a target. Keep the cheap model wired so future LLM-assisted
        # routing (e.g. disambiguating between two NPCs) drops in cleanly.
        self.model = self.settings.model_fast
        self._actors: dict[str, NPCActor] = {}

    # ── actor registry ─────────────────────────────────────────────────

    def get_actor(self, npc_id: str) -> NPCActor:
        if npc_id not in self._actors:
            self._actors[npc_id] = NPCActor(self.client, settings=self.settings)
        return self._actors[npc_id]

    @property
    def actors(self) -> dict[str, NPCActor]:
        """Read-only snapshot of cached actors keyed by NPC id."""
        return dict(self._actors)

    # ── public API ────────────────────────────────────────────────────

    async def talk(
        self,
        state: WorldState,
        action: Action,
        *,
        target_npc_id: str | None = None,
        language: str = "en",
    ) -> Outcome:
        """Resolve a ``talk`` action; return Outcome for the keeper + narrator."""
        npc_id = target_npc_id or self._pick_target(state, action)
        if not npc_id:
            return Outcome(
                success=False,
                public_facts=["There is no one here to talk to."],
            )

        npc = state.entities.get(npc_id)
        if not isinstance(npc, NPC):
            return Outcome(
                success=False,
                public_facts=[f"You can't strike up a conversation with that."],
            )

        scene_ctx = self._scene_for(state, npc)
        actor = self.get_actor(npc_id)
        try:
            resp = await actor.speak(npc, action.raw, scene_ctx, language=language)
        except NPCParseError as e:
            return Outcome(
                success=False,
                public_facts=[f"{npc.name} mumbles something incoherent."],
                private_facts=[f"npc_actor parse failure: {e}"],
            )

        public_facts = [f"{npc.name} says: “{resp.reply}”"]
        mutations: list[dict[str, Any]] = []
        if resp.remember:
            mutations.append({
                "op": "add_npc_memory",
                "args": {"npc_id": npc_id, "memory": resp.remember[:200]},
            })
        if resp.disposition_delta:
            mutations.append({
                "op": "update_disposition",
                "args": {"npc_id": npc_id, "delta": int(resp.disposition_delta)},
            })

        return Outcome(
            success=True,
            rolls=[],
            state_mutations=mutations,
            public_facts=public_facts,
            private_facts=(
                [f"{npc.name} revealed a secret"] if resp.revealed_secret else []
            ),
        )

    # ── helpers ────────────────────────────────────────────────────────

    def _pick_target(self, state: WorldState, action: Action) -> str | None:
        """Routing policy: explicit NPC target wins; else lone NPC in the room."""
        if action.target_ids:
            # Caller specified targets — only accept NPCs; don't fall through.
            for tid in action.target_ids:
                ent = state.entities.get(tid)
                if isinstance(ent, NPC):
                    return tid
            return None

        player = state.entities[state.player_id]
        loc = state.locations.get(player.location_id) if player.location_id else None
        if not loc:
            return None
        npcs_here = [
            eid
            for eid in loc.present_entity_ids
            if isinstance(state.entities.get(eid), NPC)
        ]
        return npcs_here[0] if len(npcs_here) == 1 else None

    @staticmethod
    def _scene_for(state: WorldState, npc: NPC) -> dict[str, Any]:
        loc = state.locations.get(npc.location_id) if npc.location_id else None
        co_present: list[str] = []
        if loc:
            for eid in loc.present_entity_ids:
                if eid == npc.id:
                    continue
                other = state.entities.get(eid)
                if other:
                    co_present.append(other.name)
        return {
            "location_name": loc.name if loc else "unknown",
            "co_present": co_present,
        }


__all__ = ["NPCDirector"]
