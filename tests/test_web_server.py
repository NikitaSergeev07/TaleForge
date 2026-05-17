"""Tests for the FastAPI web server.

We monkey-patch ``MinimaxClient`` with a smart MockTransport (lifted from
test_orchestrator.py's strategy) so the full request/response cycle runs
without hitting the gateway.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient


def _wrap(content: str) -> dict:
    return {
        "id": "x",
        "model": "opus-4-7",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }


def _smart_handler():
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        sys = body["messages"][0]["content"].lower()
        last_user = body["messages"][-1]["content"]
        if "action router" in sys:
            text = json.loads(last_user)["input"].lower()
            if text.startswith("look"):
                p = {"intent": "look", "target_ids": [], "parameters": {}}
            elif "north" in text:
                p = {"intent": "move", "target_ids": [], "parameters": {"direction": "north"}}
            else:
                p = {"intent": "other", "target_ids": [], "parameters": {}}
            return httpx.Response(200, json=_wrap(json.dumps(p)))
        if "narrator" in sys:
            return httpx.Response(200, json=_wrap("Atmospheric prose."))
        return httpx.Response(200, json=_wrap("ok"))
    return httpx.MockTransport(h)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up the FastAPI app with a temp saves/traces dir + mocked LLM."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

    # Patch MinimaxClient to inject MockTransport before lifespan creates it.
    from taleforge.llm import minimax as minimax_mod

    real_init = minimax_mod.MinimaxClient.__init__

    def patched_init(self, settings=None, *, transport=None):
        real_init(self, settings, transport=_smart_handler())

    monkeypatch.setattr(minimax_mod.MinimaxClient, "__init__", patched_init)

    # Force re-import so STATE picks up the patched env.
    import importlib

    from taleforge.web import server as srv

    importlib.reload(srv)
    with TestClient(srv.app) as c:
        yield c


def test_create_session_then_get_scene(client):
    r = client.post("/api/sessions", json={"scenario": "starter_village", "session_id": "t1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["session_id"] == "t1"

    r = client.get("/api/sessions/t1/scene")
    assert r.status_code == 200
    scene = r.json()
    assert scene["location"]["name"] == "Village Square"
    assert any(e["name"] == "Tibor the Bard" for e in scene["entities"])
    assert scene["player"]["gp"] == 10
    assert "shortsword" in scene["player"]["inventory"]


def test_world_map_includes_all_locations_and_current(client):
    client.post("/api/sessions", json={"session_id": "t2"})
    r = client.get("/api/sessions/t2/world-map")
    data = r.json()
    assert {n["name"] for n in data["nodes"]} >= {"Village Square", "The Boar & Barrel", "Wolf Den"}
    assert data["current"] == "village_square"
    # An edge from village_square should exist.
    assert any(e["from"] == "village_square" for e in data["edges"])


def test_npc_cards_filter_secrets_and_goals(client):
    """The NPC card DTO must NOT leak secrets/goals/raw memory."""
    client.post("/api/sessions", json={"session_id": "t3"})
    r = client.get("/api/sessions/t3/npcs")
    payload = r.text  # full JSON body, easy substring scan
    SECRETS = [
        "waters down the ale", "stretch the barrel",
        "rabid", "bounty low",
        "cannot actually fight", "exaggerates",
        "join the player", "keep the tavern", "protect Brackenhollow",
    ]
    for lit in SECRETS:
        assert lit not in payload, f"leaked: {lit!r}"
    cards = r.json()
    maren = next(c for c in cards if c["id"] == "maren")
    assert maren["disposition_label"] == "friendly"
    assert -1.0 <= maren["disposition_norm"] <= 1.0
    assert maren["has_interacted"] is False  # nothing learned yet


def test_post_turn_returns_prose_and_advances_state(client):
    client.post("/api/sessions", json={"session_id": "t4"})
    r = client.post("/api/sessions/t4/turn", json={"input": "look around"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["intent"] == "look"
    assert data["prose"] == "Atmospheric prose."
    assert data["turn"] == 1
    assert data["turn_cost_usd"] > 0
    assert data["cumulative_cost_usd"] == data["turn_cost_usd"]


def test_post_turn_move_then_scene_reflects_new_location(client):
    client.post("/api/sessions", json={"session_id": "t5"})
    client.post("/api/sessions/t5/turn", json={"input": "go north"})
    scene = client.get("/api/sessions/t5/scene").json()
    assert scene["location"]["id"] == "tavern"


def test_undo_restores_previous_snapshot(client):
    client.post("/api/sessions", json={"session_id": "t6"})
    client.post("/api/sessions/t6/turn", json={"input": "look around"})
    client.post("/api/sessions/t6/turn", json={"input": "look around"})
    assert client.get("/api/sessions/t6/scene").json()["turn"] == 2
    r = client.post("/api/sessions/t6/undo")
    assert r.status_code == 200
    assert r.json()["turn"] == 1


def test_portrait_returns_svg(client):
    r = client.get("/api/portraits/maren.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    body = r.text
    assert body.startswith("<svg")
    assert ">M<" in body  # initial


def test_create_session_conflict_on_duplicate(client):
    client.post("/api/sessions", json={"session_id": "dup"})
    r = client.post("/api/sessions", json={"session_id": "dup"})
    assert r.status_code == 409


def test_get_scene_404_for_unknown_session(client):
    r = client.get("/api/sessions/no-such/scene")
    assert r.status_code == 404


def test_session_language_propagates_to_narrator_system_prompt(tmp_path, monkeypatch):
    """Create a session with language=ru, take a turn, verify the captured
    narrator request contains the Russian language hint."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    captured: dict = {"prompts": []}

    from taleforge.llm import minimax as minimax_mod

    def recording_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        sys = body["messages"][0]["content"]
        captured["prompts"].append(sys)
        if "action router" in sys.lower():
            return httpx.Response(200, json=_wrap(json.dumps({
                "intent": "look", "target_ids": [], "parameters": {}
            })))
        if "narrator" in sys.lower():
            return httpx.Response(200, json=_wrap("Площадь тиха."))
        return httpx.Response(200, json=_wrap("ok"))

    real_init = minimax_mod.MinimaxClient.__init__

    def patched_init(self, settings=None, *, transport=None):
        real_init(self, settings, transport=httpx.MockTransport(recording_handler))

    monkeypatch.setattr(minimax_mod.MinimaxClient, "__init__", patched_init)

    import importlib

    from taleforge.web import server as srv

    importlib.reload(srv)
    with TestClient(srv.app) as c:
        r1 = c.post("/api/sessions", json={"session_id": "ru1", "language": "ru"})
        assert r1.status_code == 200, r1.text
        r = c.post("/api/sessions/ru1/turn", json={"input": "осмотрись"})
        assert r.status_code == 200, r.text
        assert r.json()["prose"] == "Площадь тиха."

    narrator_prompts = [p for p in captured["prompts"] if "narrator" in p.lower()]
    assert narrator_prompts, "narrator was never called"
    assert any("Russian" in p for p in narrator_prompts), \
        "language hint did not reach the narrator system prompt"
