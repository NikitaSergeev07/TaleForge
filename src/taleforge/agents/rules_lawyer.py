"""RulesLawyer agent — D&D 5e-lite resolver.

Local seeded dice (``random.Random((rng_seed, turn, *salts))`` from
:class:`WorldState`); LLM (``MiniMax-M2.7-highspeed``) is consulted ONLY to
choose an ability + DC for ambiguous skill checks. All arithmetic is local —
no LLM math.

Resolution methods return :class:`Outcome` objects with ``rolls`` populated
and any proposed ``state_mutations`` for the Keeper to validate-and-apply.
The Lawyer NEVER mutates state.
"""

from __future__ import annotations

import json
import random
from typing import Any

from ..config import Settings
from ..llm.minimax import MinimaxClient, strip_think_blocks
from ..llm.prompts import DC_SETTER
from ..models import Action, Entity, Outcome, WorldState
from .base import BaseAgent


# ── tables ────────────────────────────────────────────────────────────────


_WEAPONS: dict[str, dict[str, Any]] = {
    "shortsword": {"name": "shortsword", "die": 6, "ability": "str"},
    "dagger":     {"name": "dagger",     "die": 4, "ability": "dex"},
    "club":       {"name": "club",       "die": 4, "ability": "str"},
    "lute":       {"name": "lute (improvised)", "die": 2, "ability": "str"},
}
_FISTS = {"name": "fists", "die": 2, "ability": "str"}

_ABILITY_ALIASES: dict[str, str] = {
    "str": "str", "strength": "str",
    "dex": "dex", "dexterity": "dex",
    "con": "con", "constitution": "con",
    "int": "int", "intelligence": "int",
    "wis": "wis", "wisdom": "wis",
    "cha": "cha", "charisma": "cha",
}


# ── agent ────────────────────────────────────────────────────────────────


class RulesLawyer(BaseAgent):
    """D&D 5e-lite resolver. Local dice; LLM only for fuzzy DC-setting."""

    name = "rules_lawyer"

    def __init__(self, client: MinimaxClient, settings: Settings | None = None) -> None:
        super().__init__(client, settings=settings)
        self.model = self.settings.model_fast

    # ── public resolution API ─────────────────────────────────────────

    async def resolve_attack(
        self,
        state: WorldState,
        action: Action,
        *,
        actor_id: str | None = None,
    ) -> Outcome:
        actor = state.entities[actor_id or state.player_id]
        target_id = action.target_ids[0] if action.target_ids else None

        if target_id is None or target_id not in state.entities:
            return Outcome(success=False, public_facts=["No valid target for the attack."])
        target = state.entities[target_id]
        if not target.alive:
            return Outcome(success=False, public_facts=[f"{target.name} is already down."])

        rng = self._rng_for(state, "attack", actor.id, target.id)
        weapon = self._infer_weapon(actor)
        ability = weapon["ability"]
        ab_mod = self._ability_mod(actor.attrs.get(ability, 10))
        prof = 2  # 5e-lite proficiency
        ac = self._target_ac(target)

        d20 = rng.randint(1, 20)
        crit = d20 == 20
        fumble = d20 == 1
        attack_total = d20 + ab_mod + prof
        hit = crit or (not fumble and attack_total >= ac)

        rolls: list[dict[str, Any]] = [{
            "kind": "attack",
            "weapon": weapon["name"],
            "ability": ability,
            "d20": d20,
            "modifier": ab_mod + prof,
            "dc": ac,
            "total": attack_total,
            "success": hit,
            "crit": crit,
            "fumble": fumble,
        }]
        mutations: list[dict] = []
        facts: list[str] = []

        if not hit:
            adverb = "fumbles wildly past" if fumble else "misses"
            facts.append(f"{actor.name}'s {weapon['name']} {adverb} the {target.name}.")
        else:
            n_dice = 2 if crit else 1
            damage = sum(rng.randint(1, weapon["die"]) for _ in range(n_dice)) + ab_mod
            damage = max(1, damage)
            rolls.append({
                "kind": "damage",
                "dice": f"{n_dice}d{weapon['die']}",
                "modifier": ab_mod,
                "total": damage,
                "crit": crit,
            })
            mutations.append({
                "op": "apply_damage",
                "args": {"entity_id": target.id, "amount": damage},
            })
            prefix = "CRITICAL — " if crit else ""
            facts.append(
                f"{prefix}{actor.name}'s {weapon['name']} bites the {target.name} "
                f"for {damage} damage."
            )

        return Outcome(
            success=hit,
            rolls=rolls,
            state_mutations=mutations,
            public_facts=facts,
        )

    async def resolve_skill_check(
        self,
        state: WorldState,
        action: Action,
        *,
        actor_id: str | None = None,
    ) -> Outcome:
        actor = state.entities[actor_id or state.player_id]

        # Ask the LLM for ability + DC. Failures fall back to wis @ DC 12.
        try:
            ability, dc, justification = await self._ask_dc(action, state, actor)
        except Exception as e:  # noqa: BLE001 — narrow fallback path
            ability, dc, justification = "wis", 12, f"DC parse fallback: {e}"

        rng = self._rng_for(state, "skill", actor.id, action.raw)
        d20 = rng.randint(1, 20)
        ab_mod = self._ability_mod(actor.attrs.get(ability, 10))
        total = d20 + ab_mod
        success = total >= dc

        roll = {
            "kind": "skill_check",
            "ability": ability,
            "d20": d20,
            "modifier": ab_mod,
            "dc": dc,
            "total": total,
            "success": success,
        }
        verb = "succeeded" if success else "failed"
        return Outcome(
            success=success,
            rolls=[roll],
            state_mutations=[],
            public_facts=[f"You {verb} a {ability.upper()} check (DC {dc}; total {total})."],
            private_facts=[justification] if justification else [],
        )

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _ability_mod(score: int) -> int:
        return (score - 10) // 2

    @staticmethod
    def _normalize_ability(name: str) -> str:
        return _ABILITY_ALIASES.get(str(name).strip().lower(), "wis")

    @staticmethod
    def _infer_weapon(actor: Entity) -> dict[str, Any]:
        for item in actor.inventory:
            if item in _WEAPONS:
                return _WEAPONS[item]
        return _FISTS

    def _target_ac(self, target: Entity) -> int:
        ac = target.attrs.get("ac")
        if ac:
            return ac
        return 10 + self._ability_mod(target.attrs.get("dex", 10))

    @staticmethod
    def _rng_for(state: WorldState, *salts: Any) -> random.Random:
        """Deterministic RNG seeded from ``(rng_seed, turn, *salts)``.

        Same state + same salts → identical dice. Different salts (e.g. distinct
        attacker/target pairs in a single turn) → independent sequences.

        We serialise to a string seed because Python 3.13 dropped tuple seeds.
        """
        seed_repr = "|".join(str(x) for x in (state.rng_seed, state.turn, *salts))
        return random.Random(seed_repr)

    async def _ask_dc(
        self, action: Action, state: WorldState, actor: Entity
    ) -> tuple[str, int, str]:
        """LLM call: pick ability + DC + one-line justification."""
        loc = state.locations.get(actor.location_id) if actor.location_id else None
        context = {
            "action": action.raw,
            "actor": {"name": actor.name, "attrs": actor.attrs},
            "location": (
                {"name": loc.name, "description": loc.description} if loc else None
            ),
            "visible_entity_ids": loc.present_entity_ids if loc else [],
        }
        result = await self.client.chat(
            [
                {"role": "system", "content": DC_SETTER},
                {"role": "user", "content": json.dumps(context)},
            ],
            model=self.model,
            temperature=0.2,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        body = strip_think_blocks(result.content)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"DC-setter returned non-JSON: {body[:120]!r}") from e
        return (
            self._normalize_ability(parsed.get("ability", "wis")),
            max(5, min(30, int(parsed.get("dc", 12)))),
            str(parsed.get("justification", "")).strip(),
        )


__all__ = ["RulesLawyer"]
