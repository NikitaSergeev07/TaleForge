"""Tests for RulesLawyer.

Local dice are deterministic via the seed in WorldState; the DC-setter LLM
call is mocked through ``httpx.MockTransport`` so no network or API key is
needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from taleforge.agents.rules_lawyer import RulesLawyer
from taleforge.config import Settings
from taleforge.llm.minimax import MinimaxClient
from taleforge.models import Action
from taleforge.state.store import WorldStateKeeper


SCENARIO = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "taleforge"
    / "scenarios"
    / "starter_village.yaml"
)


def _settings() -> Settings:
    return Settings(minimax_api_key="test-key", max_retries=1)


def _state(seed: int = 1138, turn: int = 0):
    """Fresh WorldState with the player moved to wolf_den so attacks make sense."""
    k = WorldStateKeeper.from_scenario(SCENARIO, session_id="t")
    s = k.state
    s.rng_seed = seed
    s.turn = turn
    s.entities["pc"].location_id = "wolf_den"
    s.locations["wolf_den"].present_entity_ids.append("pc")
    return s


def _dc_handler(payload: dict) -> httpx.MockTransport:
    """Return a MockTransport whose every reply contains the given DC payload."""

    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "MiniMax-M2.7-highspeed",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70,
                },
            },
        )

    return httpx.MockTransport(h)


# ── pure helpers ─────────────────────────────────────────────────────────


def test_ability_mod_matches_5e_rules():
    assert RulesLawyer._ability_mod(10) == 0
    assert RulesLawyer._ability_mod(8) == -1
    assert RulesLawyer._ability_mod(14) == 2
    assert RulesLawyer._ability_mod(20) == 5


def test_normalize_ability_handles_full_words_and_case():
    assert RulesLawyer._normalize_ability("Dexterity") == "dex"
    assert RulesLawyer._normalize_ability("CHA") == "cha"
    assert RulesLawyer._normalize_ability("nonsense") == "wis"  # fallback


# ── attack: deterministic + reproducible ────────────────────────────────


@pytest.mark.asyncio
async def test_attack_against_dire_wolf_is_deterministic():
    transport = _dc_handler({"ability": "wis", "dc": 12, "justification": "x"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s1 = _state(seed=42, turn=0)
        s2 = _state(seed=42, turn=0)
        action = Action(raw="attack the wolf", intent="attack", target_ids=["dire_wolf"])
        o1 = await lawyer.resolve_attack(s1, action)
        o2 = await lawyer.resolve_attack(s2, action)
        # Same (seed, turn, actor, target) → identical dice.
        assert o1.rolls == o2.rolls
        assert o1.state_mutations == o2.state_mutations
        assert o1.success == o2.success


@pytest.mark.asyncio
async def test_attack_returns_attack_roll_and_optional_damage():
    transport = _dc_handler({"ability": "wis", "dc": 12, "justification": "x"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state(seed=1)
        action = Action(raw="attack the wolf", intent="attack", target_ids=["dire_wolf"])
        outcome = await lawyer.resolve_attack(s, action)

        atk = outcome.rolls[0]
        assert atk["kind"] == "attack"
        assert 1 <= atk["d20"] <= 20
        assert atk["weapon"] == "shortsword"  # pc starts with one
        if outcome.success:
            dmg = outcome.rolls[1]
            assert dmg["kind"] == "damage"
            assert outcome.state_mutations[0]["op"] == "apply_damage"
            assert outcome.state_mutations[0]["args"]["entity_id"] == "dire_wolf"
            assert dmg["total"] >= 1


@pytest.mark.asyncio
async def test_attack_on_dead_target_short_circuits():
    transport = _dc_handler({"ability": "wis", "dc": 12, "justification": "x"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state()
        s.entities["dire_wolf"].alive = False
        action = Action(raw="attack the wolf", intent="attack", target_ids=["dire_wolf"])
        outcome = await lawyer.resolve_attack(s, action)
        assert outcome.success is False
        assert outcome.rolls == []
        assert "already down" in outcome.public_facts[0]


@pytest.mark.asyncio
async def test_attack_with_no_target_short_circuits():
    transport = _dc_handler({"ability": "wis", "dc": 12, "justification": "x"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state()
        action = Action(raw="attack", intent="attack", target_ids=[])
        outcome = await lawyer.resolve_attack(s, action)
        assert outcome.success is False
        assert "No valid target" in outcome.public_facts[0]


@pytest.mark.asyncio
async def test_attack_does_not_call_llm():
    """Pure local resolution: no Minimax requests should fly during an attack."""
    calls = {"n": 0}

    def h(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="should not be called")

    async with MinimaxClient(_settings(), transport=httpx.MockTransport(h)) as client:
        lawyer = RulesLawyer(client)
        s = _state(seed=11)
        action = Action(raw="attack the wolf", intent="attack", target_ids=["dire_wolf"])
        await lawyer.resolve_attack(s, action)
        assert calls["n"] == 0


# ── skill check: LLM picks DC, dice are local ───────────────────────────


@pytest.mark.asyncio
async def test_skill_check_uses_llm_dc_and_local_dice():
    transport = _dc_handler({"ability": "Dexterity", "dc": 15, "justification": "stealth"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state(seed=7)
        action = Action(raw="I sneak past the guard", intent="skill_check")
        outcome = await lawyer.resolve_skill_check(s, action)
        roll = outcome.rolls[0]
        assert roll["ability"] == "dex"  # normalized from "Dexterity"
        assert roll["dc"] == 15
        assert 1 <= roll["d20"] <= 20
        # Whatever the dice did, success ↔ total >= dc must hold.
        assert roll["success"] == (roll["total"] >= 15)


@pytest.mark.asyncio
async def test_skill_check_falls_back_when_llm_returns_garbage():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "x",
                "model": "x",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "lol nope"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        )
    )
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state(seed=9)
        action = Action(raw="I attempt arcane lore", intent="skill_check")
        outcome = await lawyer.resolve_skill_check(s, action)
        # Fallback: wis @ DC 12.
        assert outcome.rolls[0]["ability"] == "wis"
        assert outcome.rolls[0]["dc"] == 12
        # Justification carries the parser error.
        assert outcome.private_facts and "fallback" in outcome.private_facts[0].lower()


@pytest.mark.asyncio
async def test_skill_check_clamps_dc_to_5_30():
    transport = _dc_handler({"ability": "wis", "dc": 999, "justification": "absurd"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state(seed=3)
        action = Action(raw="lift the moon", intent="skill_check")
        outcome = await lawyer.resolve_skill_check(s, action)
        assert outcome.rolls[0]["dc"] == 30  # clamped


# ── salts → independent sequences ──────────────────────────────────────


@pytest.mark.asyncio
async def test_distinct_attacker_target_pairs_get_independent_dice():
    transport = _dc_handler({"ability": "wis", "dc": 12, "justification": "x"})
    async with MinimaxClient(_settings(), transport=transport) as client:
        lawyer = RulesLawyer(client)
        s = _state(seed=42, turn=0)
        # Player attacks wolf, then wolf attacks player on the SAME turn.
        # Different salts (actor.id, target.id) should give different dice.
        a1 = Action(raw="hit wolf", intent="attack", target_ids=["dire_wolf"])
        a2 = Action(raw="hit pc", intent="attack", target_ids=["pc"])
        o1 = await lawyer.resolve_attack(s, a1, actor_id="pc")
        o2 = await lawyer.resolve_attack(s, a2, actor_id="dire_wolf")
        # Different actor + target → different roll dicts (weapon, modifier, AC all differ).
        assert o1.rolls[0] != o2.rolls[0]
