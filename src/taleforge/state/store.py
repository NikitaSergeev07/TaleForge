"""WorldStateKeeper: the only writer of WorldState.

- :func:`load_scenario_yaml` builds a fresh :class:`WorldState` from a YAML file,
  dispatching ``Entity`` vs ``NPC`` based on the ``kind`` field.
- :class:`WorldStateKeeper` validates and applies mutations, snapshots state to
  SQLite (one DB per session), and optionally writes a JSONL trace line per
  mutation (full trace module lands in a later step).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

from ..models import Entity, Location, NPC, Quest, WorldState
from .tools import TOOLS, StateMutationError


# ── scenario loader ────────────────────────────────────────────────────────


def _build_entity(raw: dict[str, Any]) -> Entity:
    """Pick :class:`NPC` vs :class:`Entity` based on the ``kind`` field."""
    return NPC.model_validate(raw) if raw.get("kind") == "npc" else Entity.model_validate(raw)


def _expand_inventory(raw_inv: list[Any]) -> list[str]:
    """Allow ``{item, count}`` shorthand in YAML; expand to a flat string list."""
    out: list[str] = []
    for entry in raw_inv or []:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict) and "item" in entry:
            out.extend([entry["item"]] * int(entry.get("count", 1)))
        else:
            raise ValueError(f"unsupported inventory entry: {entry!r}")
    return out


def load_scenario_yaml(path: Path, session_id: str | None = None) -> WorldState:
    """Build a fresh :class:`WorldState` from a scenario YAML file."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"scenario {path} did not parse to a dict")

    entities: dict[str, Entity] = {}
    for raw_e in raw.get("entities", []):
        if "inventory" in raw_e:
            raw_e = {**raw_e, "inventory": _expand_inventory(raw_e["inventory"])}
        e = _build_entity(raw_e)
        entities[e.id] = e

    locations: dict[str, Location] = {
        loc_raw["id"]: Location.model_validate(loc_raw)
        for loc_raw in raw.get("locations", [])
    }
    quests: dict[str, Quest] = {
        q_raw["id"]: Quest.model_validate(q_raw) for q_raw in raw.get("quests", [])
    }

    return WorldState(
        session_id=session_id or raw.get("session_id", "default"),
        turn=raw.get("turn", 0),
        player_id=raw["player_id"],
        entities=entities,
        locations=locations,
        quests=quests,
        in_game_time=raw.get("in_game_time", {"day": 1, "hour": 8}),
        log=list(raw.get("log", [])),
        rng_seed=raw.get("rng_seed", 0),
    )


# ── trace logging (minimal, inlined for Step 2) ───────────────────────────


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ── keeper ────────────────────────────────────────────────────────────────


class WorldStateKeeper:
    """Sole writer of :class:`WorldState`.

    Validates and applies tool-call-style mutations, optionally snapshotting to
    SQLite and writing per-mutation JSONL trace lines.
    """

    def __init__(
        self,
        state: WorldState,
        db_path: Path | None = None,
        trace_path: Path | None = None,
    ) -> None:
        self.state = state
        self.db_path = Path(db_path) if db_path else None
        self.trace_path = Path(trace_path) if trace_path else None
        if self.db_path is not None:
            self._init_db()

    # factories --------------------------------------------------------------

    @classmethod
    def from_scenario(
        cls,
        scenario_path: Path,
        *,
        session_id: str | None = None,
        db_path: Path | None = None,
        trace_path: Path | None = None,
    ) -> "WorldStateKeeper":
        state = load_scenario_yaml(Path(scenario_path), session_id=session_id)
        return cls(state, db_path=db_path, trace_path=trace_path)

    @classmethod
    def from_db(
        cls, db_path: Path, *, trace_path: Path | None = None
    ) -> "WorldStateKeeper":
        state = _load_latest_snapshot(Path(db_path))
        return cls(state, db_path=Path(db_path), trace_path=trace_path)

    # mutations --------------------------------------------------------------

    def apply(self, mutation: dict[str, Any]) -> list[str]:
        """Validate and apply a single mutation. Returns log events emitted.

        Raises :class:`StateMutationError` on unknown ops or invalid pre-conditions.
        """
        op = mutation.get("op")
        if not op or op not in TOOLS:
            raise StateMutationError(f"unknown op {op!r}")
        spec = TOOLS[op]
        args = mutation.get("args", {}) or {}
        spec.validate(self.state, args)
        events = spec.apply(self.state, args)
        self.state.log.extend(events)
        if self.trace_path is not None:
            _append_jsonl(
                self.trace_path,
                {
                    "kind": "mutation",
                    "turn": self.state.turn,
                    "op": op,
                    "args": args,
                    "events": events,
                    "ts": time.time(),
                },
            )
        return events

    def try_apply(self, mutation: dict[str, Any]) -> tuple[bool, Any]:
        """Like :meth:`apply` but returns ``(False, err_message)`` on failure
        instead of raising. Rejected mutations are still traced.
        """
        try:
            return True, self.apply(mutation)
        except StateMutationError as e:
            if self.trace_path is not None:
                _append_jsonl(
                    self.trace_path,
                    {
                        "kind": "mutation_rejected",
                        "turn": self.state.turn,
                        "op": mutation.get("op"),
                        "args": mutation.get("args"),
                        "error": str(e),
                        "ts": time.time(),
                    },
                )
            return False, str(e)

    def advance_turn(self) -> int:
        self.state.turn += 1
        return self.state.turn

    # persistence ------------------------------------------------------------

    def save(self) -> None:
        if self.db_path is None:
            raise RuntimeError("Keeper has no db_path; cannot save.")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (turn, state_json, saved_at) VALUES (?, ?, ?)",
                (
                    self.state.turn,
                    self.state.model_dump_json(),
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )
            conn.commit()

    def _init_db(self) -> None:
        assert self.db_path is not None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    turn INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    saved_at TEXT NOT NULL
                )
                """
            )
            conn.commit()


# ── DB load helpers ───────────────────────────────────────────────────────


def _load_latest_snapshot(db_path: Path) -> WorldState:
    if not db_path.exists():
        raise FileNotFoundError(f"no save db at {db_path}")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM snapshots ORDER BY turn DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError(f"save db {db_path} has no snapshots")
    return _hydrate_state(json.loads(row[0]))


def _hydrate_state(raw: dict[str, Any]) -> WorldState:
    """Rebuild a :class:`WorldState` from a JSON dump, dispatching Entity vs NPC."""
    raw_clone = dict(raw)
    raw_clone["entities"] = {
        eid: _build_entity(re) for eid, re in (raw.get("entities") or {}).items()
    }
    return WorldState.model_validate(raw_clone)


__all__ = ["StateMutationError", "WorldStateKeeper", "load_scenario_yaml"]
