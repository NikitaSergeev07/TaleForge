"""Tests for the consistency benchmark.

We don't hit the real API. A smart MockTransport fakes the orchestrator's
parser, the narrator, the rules-lawyer DC-setter, the NPC actor, AND the
chronicler — so the full bench runs in well under a second.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from taleforge.agents.orchestrator import Orchestrator
from taleforge.bench.consistency import (
    BenchReport,
    FACT_QUESTIONS,
    SCRIPTED_30,
    render_bench_report,
    run_bench,
)
from taleforge.config import Settings
from taleforge.llm.minimax import MinimaxClient
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


def _wrap(content: str) -> dict:
    return {
        "id": "x",
        "model": "MiniMax-M2.7",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 60, "completion_tokens": 25, "total_tokens": 85},
    }


def _bench_handler(captured: dict | None = None):
    """Smart handler routing by system-prompt heuristic."""

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        if captured is not None:
            captured.setdefault("calls", []).append(body)
        sys = body["messages"][0]["content"].lower()
        last_user = body["messages"][-1]["content"]

        # Action parser ---------------------------------------------------
        if "action router" in sys:
            text = json.loads(last_user)["input"].lower()
            if text.startswith("look") or "look around" in text:
                p = {"intent": "look", "target_ids": [], "parameters": {}}
            elif "go north" in text:
                p = {"intent": "move", "target_ids": [], "parameters": {"direction": "north"}}
            elif "go south" in text:
                p = {"intent": "move", "target_ids": [], "parameters": {"direction": "south"}}
            elif "go east" in text:
                p = {"intent": "move", "target_ids": [], "parameters": {"direction": "east"}}
            elif "go west" in text:
                p = {"intent": "move", "target_ids": [], "parameters": {"direction": "west"}}
            elif "attack" in text:
                p = {"intent": "attack", "target_ids": ["dire_wolf"], "parameters": {}}
            elif any(w in text for w in ("search", "listen", "sneak")):
                p = {"intent": "skill_check", "target_ids": [], "parameters": {}}
            elif "inventory" in text or text.startswith("check"):
                p = {"intent": "inventory", "target_ids": [], "parameters": {}}
            elif "maren" in text:
                p = {"intent": "talk", "target_ids": ["maren"], "parameters": {}}
            elif "roan" in text:
                p = {"intent": "talk", "target_ids": ["elder_roan"], "parameters": {}}
            elif "tibor" in text:
                p = {"intent": "talk", "target_ids": ["tibor"], "parameters": {}}
            elif any(w in text for w in ("hi ", "hello", "say hi")):
                p = {"intent": "talk", "target_ids": [], "parameters": {}}
            else:
                p = {"intent": "other", "target_ids": [], "parameters": {}}
            return httpx.Response(200, json=_wrap(json.dumps(p)))

        # Narrator --------------------------------------------------------
        if "narrator" in sys:
            return httpx.Response(200, json=_wrap("Prose for the player."))

        # Rules Lawyer DC setter -----------------------------------------
        if "rules lawyer" in sys:
            return httpx.Response(200, json=_wrap(
                json.dumps({"ability": "wis", "dc": 12, "justification": "x"})
            ))

        # NPC Actor (roleplay) -------------------------------------------
        if "roleplaying" in sys:
            return httpx.Response(200, json=_wrap(json.dumps({
                "reply": "Welcome, traveler.",
                "remember": "the player visited",
                "disposition_delta": 1,
                "revealed_secret": False,
            })))

        # Chronicler -----------------------------------------------------
        if "chronicler" in sys:
            q = last_user.split("QUESTION:")[-1].lower()
            if "alive" in q or "still" in q:
                return httpx.Response(200, json=_wrap("yes, alive."))
            if "location" in q or "where" in q:
                return httpx.Response(200, json=_wrap("Hask's Smithy."))
            if "gp" in q:
                return httpx.Response(200, json=_wrap("10"))
            if "hp" in q:
                return httpx.Response(200, json=_wrap("18"))
            if "regard" in q or "feel" in q:
                return httpx.Response(200, json=_wrap("friendly"))
            if "quest" in q or "howling" in q:
                return httpx.Response(200, json=_wrap("active"))
            if "learned" in q or "anything" in q:
                return httpx.Response(200, json=_wrap("yes, the player visited."))
            if "day" in q:
                return httpx.Response(200, json=_wrap("1"))
            return httpx.Response(200, json=_wrap("unknown"))

        return httpx.Response(200, json=_wrap("ok"))

    return httpx.MockTransport(h)


# ── tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_bench_returns_full_report():
    async with MinimaxClient(_settings(), transport=_bench_handler()) as client:
        keeper = WorldStateKeeper.from_scenario(SCENARIO, session_id="bench-test")
        orch = Orchestrator(client, keeper)
        report = await run_bench(orch, keeper)

        assert isinstance(report, BenchReport)
        assert report.scripted_turn_count == 30
        assert len(report.state_truths) == len(FACT_QUESTIONS) == 10
        assert len(report.narrator_answers) == 10
        assert 0.0 <= report.narrator_recall_accuracy <= 1.0
        assert report.mutation_applied_count > 0
        assert report.total_cost_usd > 0


@pytest.mark.asyncio
async def test_run_bench_short_script_runs_subset():
    async with MinimaxClient(_settings(), transport=_bench_handler()) as client:
        keeper = WorldStateKeeper.from_scenario(SCENARIO, session_id="t")
        orch = Orchestrator(client, keeper)
        report = await run_bench(
            orch, keeper, scripted=["look around"], questions=FACT_QUESTIONS[:2]
        )
        assert report.scripted_turn_count == 1
        assert len(report.state_truths) == 2
        assert "wolf_alive" in report.state_truths


@pytest.mark.asyncio
async def test_bench_report_records_state_truths_match_keeper_state():
    async with MinimaxClient(_settings(), transport=_bench_handler()) as client:
        keeper = WorldStateKeeper.from_scenario(SCENARIO, session_id="t")
        orch = Orchestrator(client, keeper)
        report = await run_bench(orch, keeper)
        # Truths come from the post-script state.
        assert report.state_truths["player_gp"] == keeper.state.entities["pc"].inventory.count("gp")
        assert report.state_truths["tibor_alive"] == keeper.state.entities["tibor"].alive
        # Whatever location the player ended in, that name is the truth.
        loc_id = keeper.state.entities["pc"].location_id
        assert report.state_truths["player_location"] == keeper.state.locations[loc_id].name


def test_render_bench_report_is_a_string():
    r = BenchReport(
        session_id="x",
        scripted_turn_count=30,
        state_truths={"q1": True, "q2": "Hask's Smithy"},
        narrator_answers={"q1": "yes", "q2": "smithy"},
        narrator_correct={"q1": True, "q2": False},
        narrator_recall_accuracy=0.5,
        mutation_applied_count=8,
        mutation_rejected_count=2,
        mutation_rejection_rate=0.2,
        total_cost_usd=0.1234,
    )
    out = render_bench_report(r)
    assert "narrator_recall         : 50%" in out
    assert "mutation_rejection_rate : 20.0%" in out
    assert "$0.1234" in out
    assert "✓ q1" in out
    assert "✗ q2" in out


# ── CLI: bench command (no key → exit 1) ────────────────────────────────


def test_cli_bench_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    from typer.testing import CliRunner

    from taleforge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["bench", "x"])
    assert result.exit_code == 1
    assert "MINIMAX_API_KEY" in result.output
