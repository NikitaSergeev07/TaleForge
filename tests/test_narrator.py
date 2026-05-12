"""Tests for the Narrator.

The most important assertions are leak checks: the Narrator's prompt payload
must NEVER contain NPC secrets, goals, memory, dispositions, or co-located
entities the player cannot see.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from taleforge.agents.narrator import Narrator, _hp_label, _summarize_rolls
from taleforge.config import Settings
from taleforge.llm.minimax import MinimaxClient
from taleforge.models import Entity, Outcome
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


def _state():
    return WorldStateKeeper.from_scenario(SCENARIO).state


# ── pure helpers ────────────────────────────────────────────────────────


def test_hp_label_buckets():
    e = Entity(id="x", name="x", kind="creature", hp=10, max_hp=10)
    assert _hp_label(e) == "uninjured"
    e.hp = 7
    assert _hp_label(e) == "scratched"
    e.hp = 4
    assert _hp_label(e) == "wounded"
    e.hp = 2
    assert _hp_label(e) == "bloodied"
    e.hp = 1  # 0.10 < 0.15 → near death
    assert _hp_label(e) == "near death"
    e.hp, e.alive = 0, False
    assert _hp_label(e) == "down"


def test_summarize_rolls_handles_attack_damage_skill():
    rolls = [
        {"kind": "attack", "weapon": "shortsword", "d20": 14, "modifier": 4,
         "dc": 12, "total": 18, "success": True, "crit": False, "fumble": False},
        {"kind": "damage", "dice": "1d6", "modifier": 2, "total": 5, "crit": False},
        {"kind": "skill_check", "ability": "dex", "d20": 11, "modifier": 1,
         "dc": 12, "total": 12, "success": True},
    ]
    summary = _summarize_rolls(rolls)
    assert "attack" in summary and "hit" in summary
    assert "damage 1d6" in summary
    assert "dex" in summary and "pass" in summary


def test_summarize_rolls_returns_none_when_empty():
    assert _summarize_rolls([]) is None


# ── visible-scene filtering ────────────────────────────────────────────


def test_visible_scene_includes_only_co_located_entities():
    state = _state()
    scene = Narrator._build_visible_scene(state)
    visible_ids = {e["id"] for e in scene["entities"]}
    # village_square has [pc, tibor]; pc is filtered out.
    assert visible_ids == {"tibor"}
    assert scene["location"]["name"] == "Village Square"
    assert "north" in scene["location"]["exits"]


def test_visible_scene_strips_npc_internals():
    state = _state()
    scene = Narrator._build_visible_scene(state)
    tibor_view = next(e for e in scene["entities"] if e["id"] == "tibor")
    # Only safe fields are exposed to the Narrator.
    assert set(tibor_view.keys()) == {"id", "name", "kind", "alive", "hp_label"}
    # No HP integers — bucketed label only.
    assert isinstance(tibor_view["hp_label"], str)


def test_visible_scene_omits_offscreen_npcs():
    state = _state()
    scene = Narrator._build_visible_scene(state)
    payload = json.dumps(scene)
    # Maren is in the tavern; player is in village_square. She must not appear.
    assert "Maren" not in payload
    assert "Roan" not in payload


# ── _build_view defensive checks ───────────────────────────────────────


SECRET_LITERALS = [
    "waters down the ale",
    "stretch the barrel",
    "rabid",
    "bounty low",
    "cannot actually fight",
    "exaggerates",
    "join the player",        # tibor's goal
    "keep the tavern",        # maren's goal
    "protect Brackenhollow",  # roan's goal
]


def test_build_view_drops_private_facts_and_secrets():
    state = _state()
    outcome = Outcome(
        success=True,
        public_facts=["You spot wolf-tracks pressed into the mud."],
        private_facts=["Maren noticed your sword is bloody."],
    )
    scene = Narrator._build_visible_scene(state)
    view = Narrator._build_view(scene, outcome, ["earlier prose"])
    payload = json.dumps(view)

    # public_facts and continuity DO make it through.
    assert "wolf-tracks" in payload
    assert "earlier prose" in payload

    # private_facts and every secret literal MUST be absent.
    assert "bloody" not in payload
    for lit in SECRET_LITERALS:
        assert lit not in payload, f"leaked secret: {lit!r}"


# ── mocked end-to-end smoke test ───────────────────────────────────────


def _capturing_handler(captured: dict, content: str = "The square is quiet."):
    def h(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.read()
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "MiniMax-M2.7",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 8, "total_tokens": 108},
            },
        )

    return httpx.MockTransport(h)


@pytest.mark.asyncio
async def test_narrate_returns_prose_and_updates_history():
    captured: dict = {}
    transport = _capturing_handler(
        captured, content="<think>setting scene</think>The square is quiet."
    )
    async with MinimaxClient(_settings(), transport=transport) as client:
        narrator = Narrator(client)
        state = _state()
        outcome = Outcome(success=True, public_facts=["You step into the square."])
        prose1 = await narrator.narrate(state, outcome)

        assert prose1 == "The square is quiet."
        assert "<think>" not in prose1                 # think stripped from visible
        assert narrator.prose_history[-1] == "The square is quiet."

        # Second call: previous_prose is wired in.
        outcome2 = Outcome(success=True, public_facts=["A bird calls."])
        await narrator.narrate(state, outcome2)
        body = json.loads(captured["body"])
        user_payload = json.loads(body["messages"][1]["content"])
        assert "The square is quiet." in user_payload["previous_prose"]


@pytest.mark.asyncio
async def test_narrate_payload_is_leak_free_against_real_npcs():
    """End-to-end: assert the actual HTTP body sent to Minimax is leak-free."""
    captured: dict = {}
    transport = _capturing_handler(captured, content="ok")
    async with MinimaxClient(_settings(), transport=transport) as client:
        narrator = Narrator(client)
        state = _state()
        # Move player into the tavern so Maren (NPC with goals + secrets) is visible.
        state.entities["pc"].location_id = "tavern"
        state.locations["tavern"].present_entity_ids.append("pc")
        outcome = Outcome(
            success=True,
            public_facts=["You enter the tavern; Maren wipes a copper pot."],
            private_facts=["Maren clocks the dust on your boots."],
        )
        await narrator.narrate(state, outcome)

        body = captured["body"].decode()
        # Legitimate scene info IS present.
        assert "Boar & Barrel" in body or "tavern" in body.lower()
        assert "Maren" in body  # her name is OK; her secrets are not.

        # Every secret / goal / private fact must be absent.
        for lit in SECRET_LITERALS:
            assert lit not in body, f"leaked secret: {lit!r}"
        assert "clocks the dust" not in body  # private fact dropped


@pytest.mark.asyncio
async def test_narrate_history_window_caps_at_max_history():
    captured: dict = {}
    transport = _capturing_handler(captured, content="line")
    async with MinimaxClient(_settings(), transport=transport) as client:
        narrator = Narrator(client, prose_history=["p1", "p2", "p3", "p4"])
        state = _state()
        outcome = Outcome(success=True, public_facts=["x"])
        await narrator.narrate(state, outcome, max_history=3)
        body = json.loads(captured["body"])
        view = json.loads(body["messages"][1]["content"])
        # Only the last 3 prior prose lines are passed in.
        assert view["previous_prose"] == ["p2", "p3", "p4"]
