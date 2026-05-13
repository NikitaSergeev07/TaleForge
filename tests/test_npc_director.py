"""Tests for NPCActor + NPCDirector.

Covers the three things the spec calls for:
- routes to the right NPC (explicit target, sole-NPC fallback, miss cases)
- NPC stays in character (system prompt is built from THIS NPC's persona only)
- NPC's memory updates after a conversation (mutation lands on the right NPC)

Plus: per-NPC history continuity across multiple talks, ``<think>``
preservation in multi-turn history, and routing rejects non-NPC targets.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from taleforge.agents.npc_actor import NPCActor, NPCParseError, parse_npc_response
from taleforge.agents.npc_director import NPCDirector
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


def _state():
    return WorldStateKeeper.from_scenario(SCENARIO).state


def _payload_handler(captured: dict, payload: dict) -> httpx.MockTransport:
    """A handler that always replies with ``payload`` as the assistant content."""

    def h(req: httpx.Request) -> httpx.Response:
        captured.setdefault("bodies", []).append(json.loads(req.read()))
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "MiniMax-M2.7",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 30,
                    "total_tokens": 110,
                },
            },
        )

    return httpx.MockTransport(h)


# ── parse_npc_response (pure) ─────────────────────────────────────────


def test_parse_npc_response_strips_thinking_and_clamps_delta():
    raw = (
        "<think>plan</think>"
        '{"reply": "hi", "remember": "x", "disposition_delta": 999, "revealed_secret": true}'
    )
    r = parse_npc_response(raw)
    assert r.reply == "hi"
    assert r.remember == "x"
    assert r.disposition_delta == 10  # clamped from 999
    assert r.revealed_secret is True


def test_parse_npc_response_extracts_json_from_noisy_text():
    raw = 'Here ya go: {"reply": "ok", "remember": "", "disposition_delta": 0, "revealed_secret": false} cheers'
    r = parse_npc_response(raw)
    assert r.reply == "ok"


def test_parse_npc_response_raises_on_garbage():
    with pytest.raises(NPCParseError):
        parse_npc_response("nope, not JSON at all")


# ── NPCActor: per-NPC system prompt + history ────────────────────────


@pytest.mark.asyncio
async def test_actor_system_prompt_uses_target_npc_persona_only():
    captured: dict = {}
    transport = _payload_handler(
        captured,
        {"reply": "Aye.", "remember": "", "disposition_delta": 0, "revealed_secret": False},
    )
    async with MinimaxClient(_settings(), transport=transport) as client:
        actor = NPCActor(client)
        state = _state()
        await actor.speak(state.entities["maren"], "Hello.", None)

        sys_msg = captured["bodies"][0]["messages"][0]["content"]
        assert "Maren the Innkeeper" in sys_msg
        assert "keep the tavern profitable" in sys_msg          # Maren's goal
        assert "waters down the ale" in sys_msg                 # Maren's secret
        # Other NPCs' goals + secrets MUST NOT appear in Maren's prompt.
        assert "join the player's adventure" not in sys_msg     # Tibor's goal
        assert "rabid" not in sys_msg                           # Roan's secret


@pytest.mark.asyncio
async def test_actor_appends_user_and_assistant_to_history_with_thinking():
    captured: dict = {}
    # Note the <think> block — must survive into the next call's history.
    raw_content = (
        "<think>they greeted me politely</think>"
        '{"reply": "Welcome.", "remember": "", "disposition_delta": 1, "revealed_secret": false}'
    )

    def h(req: httpx.Request) -> httpx.Response:
        captured.setdefault("bodies", []).append(json.loads(req.read()))
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "MiniMax-M2.7",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": raw_content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 60, "completion_tokens": 20, "total_tokens": 80},
            },
        )

    async with MinimaxClient(_settings(), transport=httpx.MockTransport(h)) as client:
        actor = NPCActor(client)
        state = _state()
        await actor.speak(state.entities["maren"], "Hi.", None)
        await actor.speak(state.entities["maren"], "Got rooms?", None)

        # Second call's payload includes the first turn's user + assistant
        # messages, AND the assistant content STILL contains the <think> block.
        msgs2 = captured["bodies"][1]["messages"]
        assert msgs2[0]["role"] == "system"
        assert msgs2[1]["role"] == "user" and "Hi." in msgs2[1]["content"]
        assert msgs2[2]["role"] == "assistant"
        assert "<think>they greeted me politely</think>" in msgs2[2]["content"]
        assert msgs2[3]["role"] == "user" and "Got rooms?" in msgs2[3]["content"]


# ── NPCDirector: routing ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_director_routes_to_explicit_target():
    captured: dict = {}
    transport = _payload_handler(captured, {
        "reply": "Don't bother me.",
        "remember": "asked about wolves",
        "disposition_delta": -3,
        "revealed_secret": False,
    })
    async with MinimaxClient(_settings(), transport=transport) as client:
        director = NPCDirector(client)
        state = _state()
        action = Action(raw="Tell me about the wolves.", intent="talk", target_ids=["elder_roan"])
        outcome = await director.talk(state, action)

        assert outcome.success
        assert any("Roan" in f and "Don't bother me" in f for f in outcome.public_facts)
        ops = [(m["op"], m["args"]["npc_id"]) for m in outcome.state_mutations]
        assert ("add_npc_memory", "elder_roan") in ops
        assert ("update_disposition", "elder_roan") in ops


@pytest.mark.asyncio
async def test_director_falls_back_to_sole_npc_in_room():
    captured: dict = {}
    transport = _payload_handler(captured, {
        "reply": "Adventure awaits!",
        "remember": "introduced themselves",
        "disposition_delta": 5,
        "revealed_secret": False,
    })
    async with MinimaxClient(_settings(), transport=transport) as client:
        director = NPCDirector(client)
        state = _state()  # village_square has only Tibor as NPC
        action = Action(raw="Hello there.", intent="talk", target_ids=[])
        outcome = await director.talk(state, action)
        assert outcome.success
        assert any("Tibor" in f for f in outcome.public_facts)
        assert outcome.state_mutations[0]["args"]["npc_id"] == "tibor"


@pytest.mark.asyncio
async def test_director_fails_when_no_one_to_talk_to():
    captured: dict = {}
    transport = _payload_handler(captured, {"reply": "x", "remember": "", "disposition_delta": 0, "revealed_secret": False})
    async with MinimaxClient(_settings(), transport=transport) as client:
        director = NPCDirector(client)
        state = _state()
        state.entities["pc"].location_id = "deep_woods"  # no NPCs there
        action = Action(raw="Hello?", intent="talk", target_ids=[])
        outcome = await director.talk(state, action)
        assert outcome.success is False
        assert "no one" in outcome.public_facts[0].lower()


@pytest.mark.asyncio
async def test_director_rejects_non_npc_target():
    """Cannot ``talk`` to a creature like the dire wolf."""
    captured: dict = {}
    transport = _payload_handler(captured, {"reply": "x", "remember": "", "disposition_delta": 0, "revealed_secret": False})
    async with MinimaxClient(_settings(), transport=transport) as client:
        director = NPCDirector(client)
        state = _state()
        action = Action(raw="Talk to wolf.", intent="talk", target_ids=["dire_wolf"])
        outcome = await director.talk(state, action)
        assert outcome.success is False
        # No LLM call should have been made for the wolf either.
        assert captured.get("bodies", []) == []


# ── NPCDirector: per-NPC actor cache (history continuity) ───────────


@pytest.mark.asyncio
async def test_director_caches_actor_so_npc_history_persists_across_turns():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        captured.setdefault("bodies", []).append(body)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "MiniMax-M2.7",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps({
                        "reply": f"Reply {len(captured['bodies'])}",
                        "remember": "noted",
                        "disposition_delta": 1,
                        "revealed_secret": False,
                    })},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
            },
        )

    async with MinimaxClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        director = NPCDirector(client)
        state = _state()
        # Move the player into the tavern with Maren.
        state.entities["pc"].location_id = "tavern"
        state.locations["tavern"].present_entity_ids.append("pc")

        await director.talk(state, Action(raw="Hi.", intent="talk", target_ids=["maren"]))
        await director.talk(state, Action(raw="Got rooms?", intent="talk", target_ids=["maren"]))

        # Second call: system + (user1 + assistant1) + user2 = 4 messages.
        msgs2 = captured["bodies"][1]["messages"]
        assert len(msgs2) == 4
        assert msgs2[1]["role"] == "user" and "Hi." in msgs2[1]["content"]
        assert msgs2[2]["role"] == "assistant"
        assert msgs2[3]["role"] == "user" and "Got rooms?" in msgs2[3]["content"]
        # Same NPCActor reused (one entry in the cache).
        assert set(director.actors.keys()) == {"maren"}


# ── end-to-end: memory mutation lands on the right NPC ─────────────


@pytest.mark.asyncio
async def test_npc_memory_updates_after_conversation(tmp_path):
    """Director proposes add_npc_memory; keeper applies; the right NPC remembers."""
    captured: dict = {}
    transport = _payload_handler(captured, {
        "reply": "I might.",
        "remember": "the player offered 50gp",
        "disposition_delta": 4,
        "revealed_secret": False,
    })
    async with MinimaxClient(_settings(), transport=transport) as client:
        director = NPCDirector(client)
        keeper = WorldStateKeeper.from_scenario(SCENARIO, session_id="t")
        # Move the player into the tavern.
        keeper.state.entities["pc"].location_id = "tavern"
        keeper.state.locations["tavern"].present_entity_ids.append("pc")

        action = Action(
            raw="I'll pay you 50gp for a tip about the wolves.",
            intent="talk",
            target_ids=["maren"],
        )
        outcome = await director.talk(keeper.state, action)
        for m in outcome.state_mutations:
            keeper.apply(m)

        maren = keeper.state.entities["maren"]
        assert "the player offered 50gp" in maren.memory
        assert maren.disposition == 24  # starts at 20, delta +4
        # Other NPCs' memory and disposition are untouched.
        assert keeper.state.entities["tibor"].memory == []
        assert keeper.state.entities["elder_roan"].disposition == 5
