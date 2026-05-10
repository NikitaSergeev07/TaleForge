"""Tool schemas, validators, and appliers for state mutations.

Only ``WorldStateKeeper`` invokes these (via ``store.apply``). Other agents
*propose* mutations as dicts of the form ``{"op": ..., "args": {...}}``. The
keeper looks up the spec in :data:`TOOLS`, runs ``validate``, then ``apply``.

Each :class:`ToolSpec` carries:

- ``name`` — also the dict key
- ``description`` — for LLM tool-use prompting
- ``parameters`` — JSON schema (OpenAI tool-call style)
- ``validate(state, args)`` — raises :class:`StateMutationError` on bad pre-conditions
- ``apply(state, args)`` — mutates state in-place; returns ``list[str]`` of log events
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..models import NPC, Quest, WorldState


class StateMutationError(ValueError):
    """Raised when a proposed mutation is invalid given current state."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    validate: Callable[[WorldState, dict[str, Any]], None]
    apply: Callable[[WorldState, dict[str, Any]], list[str]]


# ── helpers ────────────────────────────────────────────────────────────────


def _require(args: dict, *keys: str) -> None:
    missing = [k for k in keys if k not in args]
    if missing:
        raise StateMutationError(f"missing required args: {missing}")


def _entity(state: WorldState, eid: str):
    if eid not in state.entities:
        raise StateMutationError(f"unknown entity {eid!r}")
    return state.entities[eid]


def _location(state: WorldState, lid: str):
    if lid not in state.locations:
        raise StateMutationError(f"unknown location {lid!r}")
    return state.locations[lid]


def _npc(state: WorldState, eid: str) -> NPC:
    e = _entity(state, eid)
    if not isinstance(e, NPC):
        raise StateMutationError(f"entity {eid!r} is not an NPC")
    return e


def _quest(state: WorldState, qid: str) -> Quest:
    if qid not in state.quests:
        raise StateMutationError(f"unknown quest {qid!r}")
    return state.quests[qid]


def _schema(props: dict[str, dict], required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


# ── apply_damage ───────────────────────────────────────────────────────────


def _v_apply_damage(state: WorldState, args: dict) -> None:
    _require(args, "entity_id", "amount")
    e = _entity(state, args["entity_id"])
    if not e.alive:
        raise StateMutationError(f"cannot damage dead entity {e.id!r}")
    if e.hp is None:
        raise StateMutationError(f"entity {e.id!r} has no HP track")
    if not isinstance(args["amount"], int) or args["amount"] < 1:
        raise StateMutationError("amount must be a positive integer")


def _a_apply_damage(state: WorldState, args: dict) -> list[str]:
    e = _entity(state, args["entity_id"])
    e.hp = max(0, (e.hp or 0) - args["amount"])
    events = [f"{e.name} took {args['amount']} damage (hp={e.hp})"]
    if e.hp == 0:
        e.alive = False
        events.append(f"{e.name} fell")
    return events


# ── heal ───────────────────────────────────────────────────────────────────


def _v_heal(state: WorldState, args: dict) -> None:
    _require(args, "entity_id", "amount")
    e = _entity(state, args["entity_id"])
    if not e.alive:
        raise StateMutationError(f"cannot heal dead entity {e.id!r}")
    if e.hp is None or e.max_hp is None:
        raise StateMutationError(f"entity {e.id!r} has no HP track")
    if not isinstance(args["amount"], int) or args["amount"] < 1:
        raise StateMutationError("amount must be a positive integer")


def _a_heal(state: WorldState, args: dict) -> list[str]:
    e = _entity(state, args["entity_id"])
    e.hp = min(e.max_hp or 0, (e.hp or 0) + args["amount"])
    return [f"{e.name} healed to hp={e.hp}/{e.max_hp}"]


# ── add_item / remove_item ────────────────────────────────────────────────


def _v_add_item(state: WorldState, args: dict) -> None:
    _require(args, "entity_id", "item")
    _entity(state, args["entity_id"])
    if not isinstance(args["item"], str) or not args["item"]:
        raise StateMutationError("item must be a non-empty string")
    count = args.get("count", 1)
    if not isinstance(count, int) or count < 1:
        raise StateMutationError("count must be a positive integer")


def _a_add_item(state: WorldState, args: dict) -> list[str]:
    e = _entity(state, args["entity_id"])
    count = args.get("count", 1)
    e.inventory.extend([args["item"]] * count)
    return [f"{e.name} gained {count}× {args['item']}"]


def _v_remove_item(state: WorldState, args: dict) -> None:
    _require(args, "entity_id", "item")
    e = _entity(state, args["entity_id"])
    count = args.get("count", 1)
    if not isinstance(count, int) or count < 1:
        raise StateMutationError("count must be a positive integer")
    have = e.inventory.count(args["item"])
    if have < count:
        raise StateMutationError(
            f"{e.id!r} does not have {count}× {args['item']!r} (has {have})"
        )


def _a_remove_item(state: WorldState, args: dict) -> list[str]:
    e = _entity(state, args["entity_id"])
    count = args.get("count", 1)
    for _ in range(count):
        e.inventory.remove(args["item"])
    return [f"{e.name} lost {count}× {args['item']}"]


# ── move_entity ────────────────────────────────────────────────────────────


def _v_move_entity(state: WorldState, args: dict) -> None:
    _require(args, "entity_id", "to_location_id")
    e = _entity(state, args["entity_id"])
    if not e.alive:
        raise StateMutationError(f"cannot move dead entity {e.id!r}")
    _location(state, args["to_location_id"])


def _a_move_entity(state: WorldState, args: dict) -> list[str]:
    e = _entity(state, args["entity_id"])
    to_id = args["to_location_id"]
    from_id = e.location_id
    if from_id and from_id in state.locations:
        loc_from = state.locations[from_id]
        if e.id in loc_from.present_entity_ids:
            loc_from.present_entity_ids.remove(e.id)
    e.location_id = to_id
    loc_to = state.locations[to_id]
    if e.id not in loc_to.present_entity_ids:
        loc_to.present_entity_ids.append(e.id)
    return [f"{e.name} moved to {loc_to.name}"]


# ── kill_entity ────────────────────────────────────────────────────────────


def _v_kill_entity(state: WorldState, args: dict) -> None:
    _require(args, "entity_id")
    e = _entity(state, args["entity_id"])
    if not e.alive:
        raise StateMutationError(f"entity {e.id!r} already dead")


def _a_kill_entity(state: WorldState, args: dict) -> list[str]:
    e = _entity(state, args["entity_id"])
    e.alive = False
    if e.hp is not None:
        e.hp = 0
    return [f"{e.name} died"]


# ── update_disposition ────────────────────────────────────────────────────


def _v_update_disposition(state: WorldState, args: dict) -> None:
    _require(args, "npc_id", "delta")
    _npc(state, args["npc_id"])
    if not isinstance(args["delta"], int):
        raise StateMutationError("delta must be an integer")


def _a_update_disposition(state: WorldState, args: dict) -> list[str]:
    n = _npc(state, args["npc_id"])
    new = max(-100, min(100, n.disposition + args["delta"]))
    delta_actual = new - n.disposition
    n.disposition = new
    return [f"{n.name}'s disposition changed by {delta_actual} → {n.disposition}"]


# ── add_npc_memory ────────────────────────────────────────────────────────


def _v_add_npc_memory(state: WorldState, args: dict) -> None:
    _require(args, "npc_id", "memory")
    _npc(state, args["npc_id"])
    if not isinstance(args["memory"], str) or not args["memory"]:
        raise StateMutationError("memory must be a non-empty string")


def _a_add_npc_memory(state: WorldState, args: dict) -> list[str]:
    n = _npc(state, args["npc_id"])
    n.memory.append(args["memory"])
    return [f"{n.name} learned: {args['memory']}"]


# ── advance_time ──────────────────────────────────────────────────────────


def _v_advance_time(state: WorldState, args: dict) -> None:
    _require(args, "hours")
    if not isinstance(args["hours"], int) or args["hours"] < 0:
        raise StateMutationError("hours must be a non-negative integer")


def _a_advance_time(state: WorldState, args: dict) -> list[str]:
    hour = state.in_game_time.get("hour", 0) + args["hours"]
    day = state.in_game_time.get("day", 1) + hour // 24
    hour %= 24
    state.in_game_time["day"] = day
    state.in_game_time["hour"] = hour
    return [f"time advanced to day {day} hour {hour:02d}"]


# ── quest ops ─────────────────────────────────────────────────────────────


def _v_add_quest_objective(state: WorldState, args: dict) -> None:
    _require(args, "quest_id", "objective")
    _quest(state, args["quest_id"])
    obj = args["objective"]
    if not isinstance(obj, dict) or "id" not in obj or "description" not in obj:
        raise StateMutationError("objective requires {id, description}")


def _a_add_quest_objective(state: WorldState, args: dict) -> list[str]:
    q = _quest(state, args["quest_id"])
    obj = dict(args["objective"])
    obj.setdefault("done", False)
    q.objectives.append(obj)
    return [f"new objective on {q.title}: {obj.get('description','')}"]


def _v_complete_quest_objective(state: WorldState, args: dict) -> None:
    _require(args, "quest_id", "objective_id")
    q = _quest(state, args["quest_id"])
    if not any(o.get("id") == args["objective_id"] for o in q.objectives):
        raise StateMutationError(
            f"objective {args['objective_id']!r} not found on quest {q.id!r}"
        )


def _a_complete_quest_objective(state: WorldState, args: dict) -> list[str]:
    q = _quest(state, args["quest_id"])
    events: list[str] = []
    for o in q.objectives:
        if o.get("id") == args["objective_id"]:
            o["done"] = True
            events.append(f"objective {o.get('id')} of {q.title} done")
    if q.objectives and all(o.get("done") for o in q.objectives):
        q.state = "completed"
        events.append(f"quest {q.title} completed")
    elif q.state == "unknown":
        q.state = "active"
    return events


def _v_fail_quest(state: WorldState, args: dict) -> None:
    _require(args, "quest_id")
    q = _quest(state, args["quest_id"])
    if q.state in ("completed", "failed"):
        raise StateMutationError(f"quest {q.id!r} already {q.state}")


def _a_fail_quest(state: WorldState, args: dict) -> list[str]:
    q = _quest(state, args["quest_id"])
    q.state = "failed"
    return [f"quest {q.title} failed"]


# ── registry ──────────────────────────────────────────────────────────────


TOOLS: dict[str, ToolSpec] = {
    s.name: s
    for s in [
        ToolSpec("apply_damage", "Reduce HP of an entity. Cannot damage a dead entity or one without HP.",
                 _schema({"entity_id": {"type": "string"}, "amount": {"type": "integer", "minimum": 1}},
                         ["entity_id", "amount"]), _v_apply_damage, _a_apply_damage),
        ToolSpec("heal", "Restore HP of an entity, capped at max_hp.",
                 _schema({"entity_id": {"type": "string"}, "amount": {"type": "integer", "minimum": 1}},
                         ["entity_id", "amount"]), _v_heal, _a_heal),
        ToolSpec("add_item", "Add count copies of an item to an entity's inventory.",
                 _schema({"entity_id": {"type": "string"}, "item": {"type": "string"},
                          "count": {"type": "integer", "minimum": 1, "default": 1}},
                         ["entity_id", "item"]), _v_add_item, _a_add_item),
        ToolSpec("remove_item", "Remove count copies of an item; fails if entity lacks them.",
                 _schema({"entity_id": {"type": "string"}, "item": {"type": "string"},
                          "count": {"type": "integer", "minimum": 1, "default": 1}},
                         ["entity_id", "item"]), _v_remove_item, _a_remove_item),
        ToolSpec("move_entity", "Move an entity to a new location; fails if location is unknown.",
                 _schema({"entity_id": {"type": "string"}, "to_location_id": {"type": "string"}},
                         ["entity_id", "to_location_id"]), _v_move_entity, _a_move_entity),
        ToolSpec("kill_entity", "Mark an entity dead; fails if already dead.",
                 _schema({"entity_id": {"type": "string"}}, ["entity_id"]),
                 _v_kill_entity, _a_kill_entity),
        ToolSpec("update_disposition",
                 "Adjust an NPC's disposition toward the player by delta (clamped -100..100).",
                 _schema({"npc_id": {"type": "string"}, "delta": {"type": "integer"}},
                         ["npc_id", "delta"]), _v_update_disposition, _a_update_disposition),
        ToolSpec("add_npc_memory", "Append a memory string to an NPC's private memory.",
                 _schema({"npc_id": {"type": "string"}, "memory": {"type": "string"}},
                         ["npc_id", "memory"]), _v_add_npc_memory, _a_add_npc_memory),
        ToolSpec("advance_time", "Advance in-game time by N hours; rolls over days.",
                 _schema({"hours": {"type": "integer", "minimum": 0}}, ["hours"]),
                 _v_advance_time, _a_advance_time),
        ToolSpec("add_quest_objective",
                 "Add a new objective {id, description} to an existing quest.",
                 _schema({"quest_id": {"type": "string"}, "objective": {"type": "object"}},
                         ["quest_id", "objective"]), _v_add_quest_objective, _a_add_quest_objective),
        ToolSpec("complete_quest_objective",
                 "Mark a quest objective complete; flips quest to 'completed' once all objectives are done.",
                 _schema({"quest_id": {"type": "string"}, "objective_id": {"type": "string"}},
                         ["quest_id", "objective_id"]), _v_complete_quest_objective,
                 _a_complete_quest_objective),
        ToolSpec("fail_quest", "Mark a quest as failed; cannot re-fail or fail a completed quest.",
                 _schema({"quest_id": {"type": "string"}}, ["quest_id"]),
                 _v_fail_quest, _a_fail_quest),
    ]
}


def tool_descriptors() -> list[dict[str, Any]]:
    """Return JSON-schema tool descriptors for LLM tool-use prompts."""
    return [
        {"name": s.name, "description": s.description, "parameters": s.parameters}
        for s in TOOLS.values()
    ]


__all__ = ["StateMutationError", "ToolSpec", "TOOLS", "tool_descriptors"]
