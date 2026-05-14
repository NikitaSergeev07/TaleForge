"""FastAPI server wrapping the Orchestrator for the web frontend.

Run with::

    uvicorn taleforge.web.server:app --reload --port 8000

The Vite dev server (port 5173) proxies ``/api/*`` here.

Sessions live in an in-process registry — one Orchestrator per session id,
shared across requests. Each session gets its own ``cumulative_cost_usd``.
The single shared :class:`MinimaxClient` is created in the FastAPI lifespan
hook and closed on shutdown.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from ..agents.narrator import _hp_label
from ..agents.npc_actor import _disposition_label
from ..agents.orchestrator import Orchestrator
from ..config import Settings, get_settings
from ..llm.minimax import MinimaxClient
from ..models import NPC, WorldState
from ..state.store import WorldStateKeeper
from .schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    LocationDTO,
    NpcCardDTO,
    PlayerDTO,
    SceneDTO,
    SceneEntityDTO,
    SessionSummary,
    TurnRequest,
    TurnResultDTO,
    WorldMapDTO,
    WorldMapEdge,
    WorldMapNode,
)


# ── session registry ────────────────────────────────────────────────────


@dataclass
class SessionEntry:
    keeper: WorldStateKeeper
    orchestrator: Orchestrator
    cumulative_cost_usd: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _State:
    settings: Settings
    client: MinimaxClient | None = None
    sessions: dict[str, SessionEntry] = {}


STATE = _State()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    STATE.settings = get_settings()
    STATE.client = MinimaxClient(STATE.settings)
    try:
        yield
    finally:
        if STATE.client is not None:
            await STATE.client.aclose()


app = FastAPI(title="TaleForge web API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ────────────────────────────────────────────────────────────


def _scenario_path(name: str) -> Path:
    pkg = Path(__file__).resolve().parent.parent / "scenarios"
    for cand in (pkg / f"{name}.yaml", pkg / name, Path(name)):
        if cand.exists():
            return cand
    raise HTTPException(404, f"scenario {name!r} not found")


def _session_db(sid: str) -> Path:
    STATE.settings.saves_dir.mkdir(parents=True, exist_ok=True)
    return STATE.settings.saves_dir / f"{sid}.sqlite"


def _session_trace(sid: str) -> Path:
    STATE.settings.traces_dir.mkdir(parents=True, exist_ok=True)
    return STATE.settings.traces_dir / f"{sid}.jsonl"


def _get_session(sid: str) -> SessionEntry:
    if sid not in STATE.sessions:
        db = _session_db(sid)
        if not db.exists():
            raise HTTPException(404, f"session {sid!r} has no save db")
        keeper = WorldStateKeeper.from_db(db, trace_path=_session_trace(sid))
        assert STATE.client is not None
        orch = Orchestrator(STATE.client, keeper, settings=STATE.settings)
        STATE.sessions[sid] = SessionEntry(keeper=keeper, orchestrator=orch)
    return STATE.sessions[sid]


# ── DTO builders ───────────────────────────────────────────────────────


def _scene_dto(state: WorldState) -> SceneDTO:
    player = state.entities[state.player_id]
    loc = state.locations.get(player.location_id) if player.location_id else None
    location_dto: LocationDTO | None = None
    entities: list[SceneEntityDTO] = []
    if loc is not None:
        location_dto = LocationDTO(
            id=loc.id,
            name=loc.name,
            description=loc.description,
            exits=dict(loc.exits),
        )
        for eid in loc.present_entity_ids:
            if eid == player.id:
                continue
            e = state.entities.get(eid)
            if e is None:
                continue
            entities.append(SceneEntityDTO(
                id=e.id, name=e.name, kind=e.kind,
                alive=e.alive, hp_label=_hp_label(e),
            ))
    return SceneDTO(
        location=location_dto,
        entities=entities,
        player=PlayerDTO(
            id=player.id, name=player.name, hp=player.hp, max_hp=player.max_hp,
            hp_label=_hp_label(player),
            inventory=[i for i in player.inventory if i != "gp"],
            gp=player.inventory.count("gp"),
        ),
        turn=state.turn,
        in_game_time=dict(state.in_game_time),
    )


def _world_map_dto(state: WorldState) -> WorldMapDTO:
    nodes = [WorldMapNode(id=l.id, name=l.name) for l in state.locations.values()]
    edges: list[WorldMapEdge] = []
    seen: set[tuple[str, str]] = set()
    for loc in state.locations.values():
        for direction, dest in loc.exits.items():
            key = (loc.id, dest, direction)
            if key in seen:
                continue
            seen.add(key)
            edges.append(WorldMapEdge.model_validate(
                {"from": loc.id, "to": dest, "direction": direction}
            ))
    player = state.entities[state.player_id]
    return WorldMapDTO(nodes=nodes, edges=edges, current=player.location_id)


def _npc_dtos(state: WorldState) -> list[NpcCardDTO]:
    out: list[NpcCardDTO] = []
    for ent in state.entities.values():
        if not isinstance(ent, NPC):
            continue
        out.append(NpcCardDTO(
            id=ent.id, name=ent.name,
            hp_label=_hp_label(ent), alive=ent.alive,
            disposition_norm=max(-1.0, min(1.0, ent.disposition / 100.0)),
            disposition_label=_disposition_label(ent.disposition),
            has_interacted=bool(ent.memory),
            location_id=ent.location_id,
        ))
    return out


# ── routes: sessions ──────────────────────────────────────────────────


@app.post("/api/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    sid = req.session_id or f"{req.scenario}-{int(time.time())}"
    db = _session_db(sid)
    if db.exists():
        raise HTTPException(409, f"session {sid!r} already exists")
    spath = _scenario_path(req.scenario)
    keeper = WorldStateKeeper.from_scenario(
        spath, session_id=sid, db_path=db, trace_path=_session_trace(sid)
    )
    keeper.save()
    assert STATE.client is not None
    orch = Orchestrator(STATE.client, keeper, settings=STATE.settings)
    STATE.sessions[sid] = SessionEntry(keeper=keeper, orchestrator=orch)
    return CreateSessionResponse(session_id=sid, db_path=str(db))


@app.get("/api/sessions", response_model=list[SessionSummary])
async def list_sessions() -> list[SessionSummary]:
    out: list[SessionSummary] = []
    for db in sorted(STATE.settings.saves_dir.glob("*.sqlite")):
        sid = db.stem
        try:
            entry = _get_session(sid)
        except HTTPException:
            continue
        s = entry.keeper.state
        loc = s.locations.get(s.entities[s.player_id].location_id or "")
        out.append(SessionSummary(
            session_id=sid, turn=s.turn,
            cumulative_cost_usd=entry.cumulative_cost_usd,
            location_name=loc.name if loc else None,
        ))
    return out


# ── routes: per-session views ─────────────────────────────────────────


@app.get("/api/sessions/{sid}/scene", response_model=SceneDTO)
async def get_scene(sid: str) -> SceneDTO:
    entry = _get_session(sid)
    return _scene_dto(entry.keeper.state)


@app.get("/api/sessions/{sid}/world-map", response_model=WorldMapDTO)
async def get_world_map(sid: str) -> WorldMapDTO:
    entry = _get_session(sid)
    return _world_map_dto(entry.keeper.state)


@app.get("/api/sessions/{sid}/npcs", response_model=list[NpcCardDTO])
async def get_npcs(sid: str) -> list[NpcCardDTO]:
    entry = _get_session(sid)
    return _npc_dtos(entry.keeper.state)


@app.post("/api/sessions/{sid}/turn", response_model=TurnResultDTO)
async def post_turn(sid: str, req: TurnRequest) -> TurnResultDTO:
    entry = _get_session(sid)
    async with entry.lock:
        result = await entry.orchestrator.take_turn(req.input)
        entry.cumulative_cost_usd += result.turn_cost_usd
        entry.keeper.save()
    return TurnResultDTO(
        turn=result.turn,
        intent=result.action.intent,
        raw_input=result.action.raw,
        prose=result.prose,
        rolls=result.outcome.rolls,
        applied_mutations=[m for m, _ in result.applied_mutations],
        rejected_mutations=[
            {"mutation": m, "error": err} for m, err in result.rejected_mutations
        ],
        turn_cost_usd=result.turn_cost_usd,
        cumulative_cost_usd=entry.cumulative_cost_usd,
    )


@app.post("/api/sessions/{sid}/undo", response_model=SceneDTO)
async def undo(sid: str) -> SceneDTO:
    import sqlite3
    from ..state.store import _hydrate_state
    entry = _get_session(sid)
    db = entry.keeper.db_path
    if db is None or not db.exists():
        raise HTTPException(400, "no db to undo from")
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT state_json FROM snapshots WHERE turn < ? ORDER BY turn DESC LIMIT 1",
            (entry.keeper.state.turn,),
        ).fetchone()
    if not row:
        raise HTTPException(400, "nothing to undo")
    entry.keeper.state = _hydrate_state(json.loads(row[0]))
    return _scene_dto(entry.keeper.state)


# ── portrait placeholder (SVG, no LLM, hash-gradient + initials) ──────


_GRADIENTS = [
    ("#fde68a", "#f59e0b"), ("#a7f3d0", "#10b981"), ("#bfdbfe", "#3b82f6"),
    ("#fbcfe8", "#ec4899"), ("#ddd6fe", "#8b5cf6"), ("#fecaca", "#ef4444"),
    ("#fed7aa", "#f97316"), ("#bae6fd", "#0ea5e9"),
]


@app.get("/api/portraits/{npc_id}.svg")
def portrait(npc_id: str) -> Response:
    h = int(hashlib.sha1(npc_id.encode()).hexdigest(), 16)
    a, b = _GRADIENTS[h % len(_GRADIENTS)]
    initial = (npc_id[:1] or "?").upper()
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
  <defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
    <stop offset='0' stop-color='{a}'/><stop offset='1' stop-color='{b}'/>
  </linearGradient></defs>
  <rect width='64' height='64' rx='32' fill='url(#g)'/>
  <text x='32' y='40' text-anchor='middle' font-family='ui-sans-serif,system-ui'
        font-size='28' font-weight='700' fill='white'>{initial}</text>
</svg>"""
    return Response(svg, media_type="image/svg+xml", headers={"cache-control": "public, max-age=86400"})


__all__ = ["app"]
