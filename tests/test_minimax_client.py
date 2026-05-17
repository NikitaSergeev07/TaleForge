"""Tests for the Minimax HTTP client.

Most tests use ``httpx.MockTransport`` so they need neither network nor an API
key. ONE test (:func:`test_grumpy_innkeeper_real_api`) actually calls the
configured gateway and is skipped if ``MINIMAX_API_KEY`` is unset.
"""

from __future__ import annotations

import os

import httpx
import pytest

from taleforge.config import Settings
from taleforge.llm.minimax import (
    CompletionResult,
    MinimaxClient,
    MinimaxError,
    TokenUsage,
    strip_think_blocks,
)


# ── pure helpers ─────────────────────────────────────────────────────────


def test_strip_think_blocks_removes_thinking_keeps_visible():
    raw = "<think>okay let me think</think>Hello, traveler."
    assert strip_think_blocks(raw) == "Hello, traveler."


def test_strip_think_blocks_handles_multiple_and_multiline():
    raw = (
        "<think>step 1\nstep 2</think>"
        "First sentence. "
        "<think>aside</think>"
        "Second sentence."
    )
    assert strip_think_blocks(raw) == "First sentence. Second sentence."


def test_strip_think_blocks_no_thinking_is_passthrough():
    assert strip_think_blocks("plain prose") == "plain prose"


def test_estimate_cost_uses_known_price():
    cost = MinimaxClient.estimate_cost_usd(
        "MiniMax-M2.7", prompt_tokens=1_000_000, completion_tokens=500_000
    )
    # 1M input × $0.30 + 0.5M output × $1.20 = 0.30 + 0.60 = $0.90
    assert cost == pytest.approx(0.90)


def test_estimate_cost_falls_back_to_default_for_unknown_model():
    cost = MinimaxClient.estimate_cost_usd(
        "weird-model", prompt_tokens=100, completion_tokens=100
    )
    assert cost == pytest.approx(150 / 1_000_000)


# ── helpers for mocked transport ────────────────────────────────────────


def _ok_response(
    content: str = "Greetings.", *, prompt_t: int = 20, completion_t: int = 4
) -> httpx.Response:
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
            "usage": {
                "prompt_tokens": prompt_t,
                "completion_tokens": completion_t,
                "total_tokens": prompt_t + completion_t,
            },
        },
    )


def _settings(**overrides) -> Settings:
    base = dict(minimax_api_key="test-key", max_retries=2, request_timeout_s=5.0)
    base.update(overrides)
    return Settings(**base)


# ── mocked HTTP behaviour ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_returns_completion_result_and_tracks_cost():
    transport = httpx.MockTransport(
        lambda req: _ok_response("hi <think>x</think>there")
    )
    async with MinimaxClient(_settings(), transport=transport) as client:
        result = await client.chat(
            [{"role": "user", "content": "hi"}], model="MiniMax-M2.7"
        )
        assert isinstance(result, CompletionResult)
        assert result.content == "hi <think>x</think>there"
        assert result.visible_content == "hi there"
        assert result.usage == TokenUsage(20, 4, 24)
        assert result.cost_usd > 0
        assert client.total_cost_usd == result.cost_usd
        assert client.call_count == 1


@pytest.mark.asyncio
async def test_chat_retries_on_5xx_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, text="boom")
        return _ok_response()

    async def _no_sleep(*_a, **_kw):
        return None

    monkeypatch.setattr("taleforge.llm.minimax.asyncio.sleep", _no_sleep)
    transport = httpx.MockTransport(handler)
    async with MinimaxClient(_settings(max_retries=3), transport=transport) as client:
        result = await client.chat(
            [{"role": "user", "content": "ping"}], model="MiniMax-M2.7"
        )
    assert calls["n"] == 2
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_raises_on_4xx_immediately():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="nope"))
    async with MinimaxClient(_settings(), transport=transport) as client:
        with pytest.raises(MinimaxError, match="401"):
            await client.chat(
                [{"role": "user", "content": "hi"}], model="MiniMax-M2.7"
            )


@pytest.mark.asyncio
async def test_chat_gives_up_after_max_retries(monkeypatch):
    async def _no_sleep(*_a, **_kw):
        return None

    monkeypatch.setattr("taleforge.llm.minimax.asyncio.sleep", _no_sleep)
    transport = httpx.MockTransport(lambda req: httpx.Response(502, text="bad gw"))
    async with MinimaxClient(_settings(max_retries=2), transport=transport) as client:
        with pytest.raises(MinimaxError, match="failed after 3 attempts"):
            await client.chat(
                [{"role": "user", "content": "hi"}], model="MiniMax-M2.7"
            )


@pytest.mark.asyncio
async def test_chat_parses_gateway_reasoning_content_field():
    """gngn.my surfaces M2.7 reasoning in message.reasoning_content (out-of-band).

    The client must capture it as result.thinking; visible_content is the
    plain content. Re-sent assistant messages do NOT include thinking
    (gateway-protocol discard).
    """
    transport = httpx.MockTransport(lambda req: httpx.Response(
        200,
        json={
            "id": "x",
            "model": "opus-4-7",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Greetings.",
                    "reasoning_content": "thought about how to greet politely",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 4, "total_tokens": 104},
        },
    ))
    async with MinimaxClient(_settings(), transport=transport) as client:
        r = await client.chat(
            [{"role": "user", "content": "hi"}], model="opus-4-7"
        )
        assert r.content == "Greetings."
        assert r.visible_content == "Greetings."
        assert r.thinking == "thought about how to greet politely"
        # Re-sent assistant message does NOT include the gateway-side reasoning.
        msg = r.to_assistant_message()
        assert "thought about how" not in msg["content"]
        assert "reasoning_content" not in msg


@pytest.mark.asyncio
async def test_to_assistant_message_preserves_thinking_for_multi_turn():
    transport = httpx.MockTransport(
        lambda req: _ok_response("<think>plan</think>Done.")
    )
    async with MinimaxClient(_settings(), transport=transport) as client:
        r = await client.chat(
            [{"role": "user", "content": "x"}], model="MiniMax-M2.7"
        )
        msg = r.to_assistant_message()
        # CRITICAL: thinking is NOT stripped when re-sending.
        assert "<think>plan</think>" in msg["content"]
        assert msg["role"] == "assistant"
        assert r.visible_content == "Done."


@pytest.mark.asyncio
async def test_chat_without_api_key_raises():
    async with MinimaxClient(_settings(minimax_api_key=None)) as client:
        with pytest.raises(MinimaxError, match="MINIMAX_API_KEY"):
            await client.chat(
                [{"role": "user", "content": "hi"}], model="MiniMax-M2.7"
            )


# ── one real-API smoke test (skipped if no key) ────────────────────────


@pytest.mark.asyncio
async def test_grumpy_innkeeper_real_api():
    """One real call to the configured gateway. Skipped if no key in env."""
    key = os.getenv("MINIMAX_API_KEY")
    if not key or key == "replace-me":
        pytest.skip("MINIMAX_API_KEY unset / placeholder; skipping real-API call")
    from taleforge.config import get_settings

    settings = get_settings()
    async with MinimaxClient(settings) as client:
        result = await client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a grumpy medieval innkeeper. "
                        "Respond in one short sentence."
                    ),
                },
                {"role": "user", "content": "Say hi."},
            ],
            model=settings.model_quality,
            temperature=0.7,
            max_tokens=200,  # gateway needs headroom for reasoning + reply
        )
        assert result.visible_content.strip(), "innkeeper said nothing"
        assert result.usage.completion_tokens > 0
        assert result.cost_usd > 0
        # Print so the user sees the in-character output during `pytest -s`.
        print(f"\n[innkeeper] {result.visible_content}")
        if result.thinking:
            print(f"[reasoning] {result.thinking[:200]}…")
