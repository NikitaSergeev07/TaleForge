"""Single async HTTP client for all Minimax M2.7 chat calls.

This module is the **only** place in TaleForge that performs HTTP to
``api.minimax.io`` (per the hard rule "All HTTP to Minimax through ONE
client"). It also centralises ``<think>...</think>`` block handling — required
because M2.7 emits interleaved thinking blocks and the spec mandates we
preserve them when re-sending multi-turn history.

Public surface:

- :class:`MinimaxClient` — context-managed async client with retry + cost tracking
- :func:`strip_think_blocks` — for *display* only; never call before re-sending
- :class:`CompletionResult`, :class:`TokenUsage`, :class:`MinimaxError`
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings, get_settings


_THINK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL | re.IGNORECASE)


def strip_think_blocks(content: str) -> str:
    """Remove ``<think>...</think>`` blocks for *display* to the user.

    DO NOT call this before sending a multi-turn message back to the model:
    the spec mandates that thinking blocks ride along in assistant history so
    M2.7 can chain its reasoning across turns.
    """

    return _THINK_RE.sub("", content).strip()


class MinimaxError(RuntimeError):
    """Raised when a Minimax call fails (after retries) or returns garbage."""


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def from_api(cls, raw: dict | None) -> "TokenUsage":
        raw = raw or {}
        return cls(
            prompt_tokens=int(raw.get("prompt_tokens", 0)),
            completion_tokens=int(raw.get("completion_tokens", 0)),
            total_tokens=int(raw.get("total_tokens", 0)),
        )


@dataclass
class CompletionResult:
    """One chat call's outcome.

    ``content`` is the assistant content WITH any inline ``<think>...</think>``
    blocks intact. ``visible_content`` has them stripped (for CLI display).
    ``thinking`` carries the gateway's separate ``reasoning_content`` field
    (gngn.my surfaces M2.7's reasoning out-of-band rather than inline).
    """

    content: str
    visible_content: str
    finish_reason: str
    model: str
    usage: TokenUsage
    cost_usd: float
    latency_s: float
    tool_calls: list[dict] = field(default_factory=list)
    thinking: str = ""  # message.reasoning_content from gateway, if any

    def to_assistant_message(self) -> dict[str, Any]:
        """Build an assistant turn for re-use in multi-turn history.

        Returns ``content`` (with any inline thinking) and NOT ``visible_content``.
        We deliberately do NOT re-send ``thinking`` (the separate
        ``reasoning_content``) — Anthropic-style APIs (which the gateway
        emulates) discard prior reasoning blocks on follow-up.
        """

        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


class MinimaxClient:
    """Async, retrying, cost-tracking client for Minimax's OpenAI-compatible API.

    Use as ``async with MinimaxClient() as c: ...`` so the underlying
    ``httpx.AsyncClient`` is closed cleanly. ``total_cost_usd`` and
    ``call_count`` give a running tally across the client's lifetime.
    """

    # Pricing in USD per 1,000,000 tokens, as ``(input, output)``.
    # The gngn.my gateway serves Minimax under Claude-branded model names. We
    # use Anthropic list prices as upper-bound placeholders; actual gateway
    # pricing is likely lower (Minimax wholesale). Verify before publishing.
    PRICES_USD_PER_M_TOK: dict[str, tuple[float, float]] = {
        "claude-opus-4-7":   (15.00, 75.00),
        "claude-sonnet-4-6": (3.00, 15.00),
        "claude-haiku-4-5":  (1.00, 5.00),
        # Legacy direct-Minimax names retained for backwards-compat tests.
        "MiniMax-M2.7": (0.30, 1.20),
        "MiniMax-M2.7-highspeed": (0.10, 0.40),
    }

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._has_api_key = bool(self.settings.minimax_api_key)
        self._using_mock_transport = transport is not None
        self._http = httpx.AsyncClient(
            base_url=self.settings.minimax_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self.settings.minimax_api_key or 'missing'}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.settings.request_timeout_s),
            transport=transport,
        )
        self._calls: list[CompletionResult] = []

    async def __aenter__(self) -> "MinimaxClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── public API ─────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        extra: dict | None = None,
    ) -> CompletionResult:
        """One chat completion call with retry. Updates running cost tally."""

        if not self._has_api_key and not self._using_mock_transport:
            raise MinimaxError("MINIMAX_API_KEY is not set; cannot call Minimax API")

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        if response_format:
            body["response_format"] = response_format
        if extra:
            body.update(extra)

        started = time.perf_counter()
        resp = await self._post_with_retry("/chat/completions", body)
        latency = time.perf_counter() - started

        data = resp.json()
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise MinimaxError(f"unexpected response shape: {data!r}") from e

        content = msg.get("content") or ""
        thinking = msg.get("reasoning_content") or ""  # gngn.my surfaces M2.7's reasoning here
        tool_calls = list(msg.get("tool_calls") or [])
        finish_reason = choice.get("finish_reason", "stop")
        usage = TokenUsage.from_api(data.get("usage"))
        cost = self.estimate_cost_usd(model, usage.prompt_tokens, usage.completion_tokens)

        result = CompletionResult(
            content=content,
            visible_content=strip_think_blocks(content),
            finish_reason=finish_reason,
            model=data.get("model", model),
            usage=usage,
            cost_usd=cost,
            latency_s=latency,
            tool_calls=tool_calls,
            thinking=thinking,
        )
        self._calls.append(result)
        return result

    @staticmethod
    def estimate_cost_usd(
        model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        in_p, out_p = MinimaxClient.PRICES_USD_PER_M_TOK.get(model, (0.30, 1.20))
        return (prompt_tokens * in_p + completion_tokens * out_p) / 1_000_000

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> list[CompletionResult]:
        return list(self._calls)

    # ── internals ──────────────────────────────────────────────────────

    async def _post_with_retry(self, path: str, body: dict) -> httpx.Response:
        last_err: Exception | str | None = None
        max_attempts = self.settings.max_retries + 1
        for attempt in range(max_attempts):
            if attempt > 0:
                # Exponential backoff with jitter; cap at 30s so a hung server
                # doesn't make a turn take a minute to fail.
                wait = min(30.0, 2 ** attempt) + random.random()
                await asyncio.sleep(wait)
            try:
                resp = await self._http.post(path, json=body)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_err = e
                continue
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                continue
            if resp.status_code >= 400:
                # Non-retryable client error — surface immediately.
                raise MinimaxError(
                    f"HTTP {resp.status_code} from Minimax: {resp.text[:500]}"
                )
            return resp
        raise MinimaxError(
            f"Minimax call failed after {max_attempts} attempts: {last_err}"
        )


__all__ = [
    "MinimaxClient",
    "MinimaxError",
    "CompletionResult",
    "TokenUsage",
    "strip_think_blocks",
]
