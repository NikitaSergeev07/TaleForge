"""Tests for the WorldStateKeeper, scenario loader, and tool validators."""

from __future__ import annotations

from pathlib import Path

import pytest

from taleforge.models import NPC
from taleforge.state.store import WorldStateKeeper, load_scenario_yaml
from taleforge.state.tools import StateMutationError


SCENARIO = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "taleforge"
    / "scenarios"
    / "starter_village.yaml"
)


def _keeper(**kw) -> WorldStateKeeper:
    return WorldStateKeeper.from_scenario(SCENARIO, session_id="t", **kw)


# ── scenario loader ───────────────────────────────────────────────────────


def test_load_scenario_builds_entities_and_npcs():
    state = load_scenario_yaml(SCENARIO)

    # Player is a plain Entity (not NPC).
    assert state.player_id == "pc"
    assert state.entities["pc"].kind == "player"
    assert not isinstance(state.entities["pc"], NPC)

    # NPCs come back as NPC instances with goals + secrets.
    maren = state.entities["maren"]
    assert isinstance(maren, NPC)
    assert "keep the tavern profitable" in maren.goals
    assert maren.secrets and "ale" in maren.secrets[0]

    # Inventory expansion: pc starts with shortsword + 10× gp.
    inv = state.entities["pc"].inventory
    assert inv.count("shortsword") == 1
    assert inv.count("gp") == 10

    # Locations + dire wolf wired up.
    assert "wolf_den" in state.locations
    wolf = state.entities["dire_wolf"]
    assert wolf.hp == 22 and wolf.alive
    assert state.rng_seed == 1138


# ── valid mutations ───────────────────────────────────────────────────────


def test_apply_damage_reduces_hp_and_kills_at_zero():
    k = _keeper()
    k.apply({"op": "apply_damage", "args": {"entity_id": "dire_wolf", "amount": 10}})
    assert k.state.entities["dire_wolf"].hp == 12
    k.apply({"op": "apply_damage", "args": {"entity_id": "dire_wolf", "amount": 12}})
    assert k.state.entities["dire_wolf"].hp == 0
    assert not k.state.entities["dire_wolf"].alive


def test_heal_caps_at_max_hp():
    k = _keeper()
    k.apply({"op": "apply_damage", "args": {"entity_id": "pc", "amount": 5}})
    assert k.state.entities["pc"].hp == 13
    k.apply({"op": "heal", "args": {"entity_id": "pc", "amount": 100}})
    assert k.state.entities["pc"].hp == 18  # capped


def test_add_and_remove_item_with_count():
    k = _keeper()
    k.apply({"op": "add_item", "args": {"entity_id": "pc", "item": "gp", "count": 50}})
    assert k.state.entities["pc"].inventory.count("gp") == 60
    k.apply({"op": "remove_item", "args": {"entity_id": "pc", "item": "gp", "count": 25}})
    assert k.state.entities["pc"].inventory.count("gp") == 35


def test_move_entity_updates_both_locations():
    k = _keeper()
    k.apply({"op": "move_entity", "args": {"entity_id": "pc", "to_location_id": "tavern"}})
    assert k.state.entities["pc"].location_id == "tavern"
    assert "pc" in k.state.locations["tavern"].present_entity_ids
    assert "pc" not in k.state.locations["village_square"].present_entity_ids


def test_disposition_clamps_and_npc_memory_appends():
    k = _keeper()
    k.apply({"op": "update_disposition", "args": {"npc_id": "maren", "delta": 200}})
    assert k.state.entities["maren"].disposition == 100  # clamped
    k.apply(
        {
            "op": "add_npc_memory",
            "args": {"npc_id": "tibor", "memory": "the player asked about wolves"},
        }
    )
    assert "wolves" in k.state.entities["tibor"].memory[0]


def test_advance_time_rolls_over_days():
    k = _keeper()
    k.apply({"op": "advance_time", "args": {"hours": 20}})  # 8 + 20 = 28 → day 2 hour 4
    assert k.state.in_game_time == {"day": 2, "hour": 4}


def test_complete_objective_then_quest_completed():
    k = _keeper()
    k.apply(
        {
            "op": "complete_quest_objective",
            "args": {"quest_id": "howling_woods", "objective_id": "defeat_wolf"},
        }
    )
    assert k.state.quests["howling_woods"].state == "active"  # not all done yet
    k.apply(
        {
            "op": "complete_quest_objective",
            "args": {"quest_id": "howling_woods", "objective_id": "claim_bounty"},
        }
    )
    assert k.state.quests["howling_woods"].state == "completed"


# ── invalid mutations: must raise StateMutationError ─────────────────────


def test_reject_damage_to_dead_entity():
    k = _keeper()
    k.apply({"op": "kill_entity", "args": {"entity_id": "dire_wolf"}})
    with pytest.raises(StateMutationError, match="dead"):
        k.apply({"op": "apply_damage", "args": {"entity_id": "dire_wolf", "amount": 1}})


def test_reject_remove_nonexistent_item():
    k = _keeper()
    with pytest.raises(StateMutationError, match="does not have"):
        k.apply({"op": "remove_item", "args": {"entity_id": "pc", "item": "rubber_duck"}})


def test_reject_move_to_unknown_location():
    k = _keeper()
    with pytest.raises(StateMutationError, match="unknown location"):
        k.apply(
            {"op": "move_entity", "args": {"entity_id": "pc", "to_location_id": "atlantis"}}
        )


def test_reject_unknown_op():
    k = _keeper()
    with pytest.raises(StateMutationError, match="unknown op"):
        k.apply({"op": "summon_dragon", "args": {}})


def test_try_apply_returns_false_on_invalid():
    k = _keeper()
    ok, err = k.try_apply(
        {"op": "move_entity", "args": {"entity_id": "pc", "to_location_id": "atlantis"}}
    )
    assert ok is False and "atlantis" in err


# ── persistence: SQLite save + load ──────────────────────────────────────


def test_save_then_from_db_round_trips_state(tmp_path):
    db = tmp_path / "session.sqlite"
    k = WorldStateKeeper.from_scenario(SCENARIO, session_id="rt", db_path=db)
    k.apply({"op": "apply_damage", "args": {"entity_id": "dire_wolf", "amount": 7}})
    k.advance_turn()
    k.save()

    k2 = WorldStateKeeper.from_db(db)
    assert k2.state.entities["dire_wolf"].hp == 15
    assert k2.state.turn == 1
    # NPC subclass survives the JSON round-trip.
    assert isinstance(k2.state.entities["maren"], NPC)
    assert "watters" not in str(k2.state.entities["maren"].secrets)  # sanity: no typo
    assert k2.state.entities["maren"].disposition == 20


# ── trace logging ────────────────────────────────────────────────────────


def test_trace_logger_writes_jsonl(tmp_path):
    trace = tmp_path / "trace.jsonl"
    k = _keeper(trace_path=trace)
    k.apply({"op": "apply_damage", "args": {"entity_id": "dire_wolf", "amount": 3}})
    ok, _ = k.try_apply(
        {"op": "move_entity", "args": {"entity_id": "pc", "to_location_id": "atlantis"}}
    )
    assert ok is False
    lines = [ln for ln in trace.read_text().splitlines() if ln.strip()]
    assert any('"kind": "mutation"' in ln for ln in lines)
    assert any('"kind": "mutation_rejected"' in ln for ln in lines)
