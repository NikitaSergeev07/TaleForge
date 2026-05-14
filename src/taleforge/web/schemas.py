"""DTOs for the web API.

Carefully filtered: never expose ``NPC.secrets``, ``NPC.goals``, ``NPC.memory``,
or anything else the spec lists as private. Disposition is shown to the
frontend as a normalised float (so a bar can be drawn) plus a label — the raw
integer is treated as game-internal.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── inputs ──────────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str = "starter_village"
    session_id: str | None = None
    language: str = "en"  # narrator/NPC reply language; "en", "ru", …


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str
    language: str | None = None  # overrides session default if set


# ── scene / map / npc ───────────────────────────────────────────────────


class SceneEntityDTO(BaseModel):
    id: str
    name: str
    kind: str          # "player" | "npc" | "creature" | "item"
    alive: bool
    hp_label: str      # qualitative bucket (uninjured / scratched / wounded / …)


class LocationDTO(BaseModel):
    id: str
    name: str
    description: str
    exits: dict[str, str]  # direction → location_id


class PlayerDTO(BaseModel):
    id: str
    name: str
    hp: int | None
    max_hp: int | None
    hp_label: str
    inventory: list[str]
    gp: int


class SceneDTO(BaseModel):
    """What the player sees right now. Mirrors Narrator._build_visible_scene."""
    location: LocationDTO | None
    entities: list[SceneEntityDTO]
    player: PlayerDTO
    turn: int
    in_game_time: dict[str, int]


class WorldMapNode(BaseModel):
    id: str
    name: str


class WorldMapEdge(BaseModel):
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    direction: str
    model_config = ConfigDict(populate_by_name=True)


class WorldMapDTO(BaseModel):
    nodes: list[WorldMapNode]
    edges: list[WorldMapEdge]
    current: str | None


class NpcCardDTO(BaseModel):
    """Public-facing NPC view. NO secrets, NO goals, NO raw memory."""
    id: str
    name: str
    hp_label: str
    alive: bool
    disposition_norm: float       # -1.0 .. 1.0 for bar rendering
    disposition_label: str        # "friendly" / "wary" / etc.
    has_interacted: bool          # True if NPC.memory is non-empty
    location_id: str | None       # so the UI can pin them on the map


# ── turn ────────────────────────────────────────────────────────────────


class TurnResultDTO(BaseModel):
    turn: int
    intent: str
    raw_input: str
    prose: str
    rolls: list[dict]
    applied_mutations: list[dict]
    rejected_mutations: list[dict]
    turn_cost_usd: float
    cumulative_cost_usd: float


# ── sessions ────────────────────────────────────────────────────────────


class CreateSessionResponse(BaseModel):
    session_id: str
    db_path: str


class SessionSummary(BaseModel):
    session_id: str
    turn: int
    cumulative_cost_usd: float
    location_name: str | None


__all__ = [
    "CreateSessionRequest",
    "TurnRequest",
    "SceneEntityDTO",
    "LocationDTO",
    "PlayerDTO",
    "SceneDTO",
    "WorldMapNode",
    "WorldMapEdge",
    "WorldMapDTO",
    "NpcCardDTO",
    "TurnResultDTO",
    "CreateSessionResponse",
    "SessionSummary",
]
