"""Pydantic v2 domain models for TaleForge.

Notes on a small, deliberate deviation from the spec:

The spec types `WorldState.entities` as `dict[str, Entity | NPC]`. Because `NPC`
is a subclass of `Entity` *and* `Entity.kind` already includes ``"npc"`` as a
valid literal, a true pydantic discriminated union would be ambiguous. We keep
the field as `dict[str, Entity]` here — `NPC` instances satisfy `Entity` via
inheritance — and let the scenario loader (Step 2) construct the correct
subclass based on the YAML ``kind`` field.

Default values added beyond the spec are pure ergonomic conveniences (e.g.
``turn: int = 0``, ``Quest.state = "unknown"``). Field types and names are
otherwise preserved verbatim.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Entity(BaseModel):
    """A creature, item, player, or NPC living in the world."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: Literal["player", "npc", "creature", "item"]
    hp: int | None = None
    max_hp: int | None = None
    location_id: str | None = None
    alive: bool = True
    inventory: list[str] = Field(default_factory=list)
    attrs: dict[str, int] = Field(default_factory=dict)  # str/dex/con/int/wis/cha


class NPC(Entity):
    """Entity + character-specific fields the Director / Actor consume."""

    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)  # never shown to Narrator
    disposition: int = 0  # -100..100 toward player
    memory: list[str] = Field(default_factory=list)  # what THIS npc has learned


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    exits: dict[str, str] = Field(default_factory=dict)  # direction → location_id
    present_entity_ids: list[str] = Field(default_factory=list)


class Quest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    state: Literal["unknown", "active", "completed", "failed"] = "unknown"
    objectives: list[dict] = Field(default_factory=list)


class WorldState(BaseModel):
    """The single source of truth. Only WorldStateKeeper writes to this."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    turn: int = 0
    player_id: str
    # See module docstring re: `dict[str, Entity | NPC]` typing.
    entities: dict[str, Entity] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    quests: dict[str, Quest] = Field(default_factory=dict)
    in_game_time: dict[str, int] = Field(
        default_factory=lambda: {"day": 1, "hour": 8}
    )
    log: list[str] = Field(default_factory=list)  # short factual events, NOT prose
    rng_seed: int = 0  # seed for RulesLawyer's reproducible dice (design rule #4)


class Action(BaseModel):
    """A parsed player action ready for the Orchestrator to route."""

    model_config = ConfigDict(extra="forbid")

    raw: str
    intent: Literal[
        "attack", "move", "talk", "skill_check", "inventory", "look", "other"
    ]
    target_ids: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)


class Outcome(BaseModel):
    """Result of resolving an Action, before the Narrator turns it into prose."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    rolls: list[dict] = Field(default_factory=list)  # {kind,d20,modifier,dc,total,success}
    state_mutations: list[dict] = Field(default_factory=list)  # tool-call-style ops
    public_facts: list[str] = Field(default_factory=list)  # what the player can perceive
    private_facts: list[str] = Field(default_factory=list)  # what NPCs learned but player didn't


__all__ = [
    "Entity",
    "NPC",
    "Location",
    "Quest",
    "WorldState",
    "Action",
    "Outcome",
]
