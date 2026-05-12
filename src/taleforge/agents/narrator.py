"""Narrator agent — turns Outcomes + visible scene into atmospheric prose.

Strict invariant (per design rule #2): the Narrator NEVER sees NPC secrets,
NPC goals, NPC memory, NPC disposition, the full state log, or any
``Outcome.private_facts``. Its input is filtered to exactly:

- ``this_turn.public_facts`` — what the player can perceive after resolution
- ``scene.location`` — current room name + description + exit directions
- ``scene.entities`` — co-located entities, with HP shown as a *qualitative*
  label (uninjured / scratched / wounded / bloodied / near death / down) so the
  Narrator never narrates exact HP numbers
- ``previous_prose`` — last N prose strings (for continuity)

The Narrator never mutates state. ``narrate`` returns the visible prose and
records it on the agent instance so the next turn's continuity is automatic.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..llm.minimax import MinimaxClient
from ..llm.prompts import NARRATOR
from ..models import Entity, Outcome, WorldState
from .base import BaseAgent


# ── helpers ────────────────────────────────────────────────────────────


def _hp_label(ent: Entity) -> str:
    """Bucket HP into a qualitative label so the Narrator never says '12/22 HP'."""
    if not ent.alive:
        return "down"
    if ent.hp is None or ent.max_hp is None or ent.max_hp <= 0:
        return "unknown"
    pct = ent.hp / ent.max_hp
    if pct >= 1.0:
        return "uninjured"
    if pct >= 0.7:
        return "scratched"
    if pct >= 0.4:
        return "wounded"
    if pct >= 0.15:
        return "bloodied"
    return "near death"


def _summarize_rolls(rolls: list[dict]) -> str | None:
    """One-liner summary of dice for the Narrator's reference (never numeric prose)."""
    if not rolls:
        return None
    parts: list[str] = []
    for r in rolls:
        kind = r.get("kind")
        if kind == "attack":
            verdict = "crit" if r.get("crit") else ("hit" if r.get("success") else "miss")
            parts.append(f"attack d20={r['d20']}→{r['total']} vs AC{r['dc']} ({verdict})")
        elif kind == "damage":
            parts.append(f"damage {r['dice']}+{r.get('modifier', 0)}={r['total']}")
        elif kind == "skill_check":
            verdict = "pass" if r.get("success") else "fail"
            parts.append(
                f"{r['ability']} d20={r['d20']}+{r['modifier']}={r['total']} vs DC{r['dc']} ({verdict})"
            )
    return "; ".join(parts)


def _safe_player_view(player: Entity) -> dict[str, Any]:
    """Player's own visible state — name, HP label, inventory. NO attrs spillover."""
    return {
        "name": player.name,
        "hp_label": _hp_label(player),
        "inventory": list(player.inventory),
    }


# ── agent ──────────────────────────────────────────────────────────────


class Narrator(BaseAgent):
    """Prose-only agent. Strict input filtering; never mutates state."""

    name = "narrator"

    def __init__(
        self,
        client: MinimaxClient,
        settings: Settings | None = None,
        *,
        prose_history: list[str] | None = None,
    ) -> None:
        super().__init__(client, settings=settings)
        self.model = self.settings.model_quality
        self._prose_history: list[str] = list(prose_history or [])

    @property
    def prose_history(self) -> list[str]:
        """Return a copy of recorded prose so far (most recent at the end)."""
        return list(self._prose_history)

    async def narrate(
        self,
        state: WorldState,
        outcome: Outcome,
        *,
        max_history: int = 3,
        temperature: float = 0.85,
        max_tokens: int = 400,
    ) -> str:
        """Generate prose for ``outcome`` in the current scene.

        Returns the visible prose (with ``<think>`` blocks stripped) and
        appends it to ``prose_history`` for continuity on the next turn.
        """
        scene = self._build_visible_scene(state)
        view = self._build_view(scene, outcome, self._prose_history[-max_history:])
        result = await self.client.chat(
            [
                {"role": "system", "content": NARRATOR},
                {"role": "user", "content": json.dumps(view)},
            ],
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        prose = result.visible_content.strip()
        self._prose_history.append(prose)
        return prose

    # ── view builders (pure, used by tests directly) ──────────────────

    @staticmethod
    def _build_visible_scene(state: WorldState) -> dict[str, Any]:
        """What the player can perceive *right now*. Filtered, never raw state."""
        player = state.entities[state.player_id]
        loc = state.locations.get(player.location_id) if player.location_id else None
        if loc is None:
            return {"location": None, "entities": [], "player": _safe_player_view(player)}

        visible_entities: list[dict[str, Any]] = []
        for eid in loc.present_entity_ids:
            if eid == player.id:
                continue
            ent = state.entities.get(eid)
            if not ent:
                continue
            visible_entities.append(
                {
                    "id": ent.id,
                    "name": ent.name,
                    "kind": ent.kind,
                    "alive": ent.alive,
                    "hp_label": _hp_label(ent),
                }
            )

        return {
            "location": {
                "id": loc.id,
                "name": loc.name,
                "description": loc.description,
                "exits": list(loc.exits.keys()),
            },
            "entities": visible_entities,
            "player": _safe_player_view(player),
        }

    @staticmethod
    def _build_view(
        scene: dict[str, Any],
        outcome: Outcome,
        previous_prose: list[str],
    ) -> dict[str, Any]:
        """Bundle scene + this-turn public facts + recent prose for the prompt.

        Anything not in this dict cannot reach the Narrator, by construction.
        """
        return {
            "scene": scene,
            "this_turn": {
                "success": outcome.success,
                "public_facts": list(outcome.public_facts),
                "rolls_summary": _summarize_rolls(outcome.rolls),
            },
            "previous_prose": list(previous_prose),
        }


__all__ = ["Narrator"]
