"""NPCActor — one NPC in one head.

Per design rule #3 each NPC has its OWN system prompt and OWN conversation
history. NPCActor is *that* one NPC's wrapper around the LLM. The NPCDirector
keeps a cache of NPCActor instances (one per NPC the player ever talks to),
so a single LLM call NEVER plays multiple characters at once — that's the
whole point of the project.

The actor:
- Builds a per-NPC system prompt from current goals/secrets/memory/disposition
  (rebuilt every call so the latest memory is reflected).
- Maintains its own ``user / assistant`` history list, with assistant turns
  preserved verbatim — including ``<think>...</think>`` blocks — so M2.7 can
  chain its reasoning across turns (centralised invariant, see
  :func:`MinimaxClient.to_assistant_message`).
- Returns a structured :class:`NPCResponse` so the Director can translate it
  into Keeper mutations (add_npc_memory, update_disposition).

The actor never mutates state. The Director never mutates state. Only the
Keeper writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..llm.minimax import MinimaxClient, strip_think_blocks
from ..llm.prompts import NPC_ACTOR_TEMPLATE, language_suffix
from ..models import NPC
from .base import BaseAgent


# ── disposition labels ──────────────────────────────────────────────────


def _disposition_label(d: int) -> str:
    if d <= -75:
        return "loathing"
    if d <= -40:
        return "hostile"
    if d <= -10:
        return "wary"
    if d <= 10:
        return "neutral"
    if d <= 40:
        return "friendly"
    if d <= 75:
        return "warm"
    return "devoted"


def _bullets(items: list[str], placeholder: str = "(none)") -> str:
    return "\n".join(f"  - {x}" for x in items) if items else f"  {placeholder}"


def build_npc_system_prompt(npc: NPC, scene_context: dict | None = None) -> str:
    """Format the per-NPC system prompt with current goals/secrets/memory/etc."""
    scene_block = ""
    if scene_context:
        co = ", ".join(scene_context.get("co_present") or []) or "no one else"
        loc = scene_context.get("location_name") or "here"
        scene_block = f"\nYou are in {loc}; also present: {co}.\n"
    return NPC_ACTOR_TEMPLATE.format(
        name=npc.name,
        goals_block=_bullets(list(npc.goals)),
        secrets_block=_bullets(list(npc.secrets)),
        memory_block=_bullets(list(npc.memory[-10:]), placeholder="(nothing yet)"),
        disposition_label=_disposition_label(npc.disposition),
        disposition_int=npc.disposition,
        scene_block=scene_block,
    )


# ── response parsing ───────────────────────────────────────────────────


class NPCParseError(ValueError):
    """Raised when an NPC reply doesn't conform to the expected JSON shape."""


@dataclass
class NPCResponse:
    reply: str
    remember: str = ""
    disposition_delta: int = 0
    revealed_secret: bool = False


def _extract_json_blob(text: str) -> str:
    """Best-effort: pull the outermost ``{...}`` from a possibly-noisy reply."""
    text = text.strip()
    if text.startswith("{"):
        return text
    i, j = text.find("{"), text.rfind("}")
    return text[i : j + 1] if i >= 0 and j > i else text


def parse_npc_response(content: str) -> NPCResponse:
    body = _extract_json_blob(strip_think_blocks(content))
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise NPCParseError(f"non-JSON NPC reply: {body[:160]!r}") from e
    if not isinstance(parsed, dict) or "reply" not in parsed:
        raise NPCParseError(f"NPC reply missing 'reply': {parsed!r}")
    return NPCResponse(
        reply=str(parsed.get("reply", "")).strip(),
        remember=str(parsed.get("remember", "")).strip(),
        disposition_delta=max(-10, min(10, int(parsed.get("disposition_delta", 0) or 0))),
        revealed_secret=bool(parsed.get("revealed_secret", False)),
    )


# ── agent ──────────────────────────────────────────────────────────────


class NPCActor(BaseAgent):
    """Wraps ONE NPC. Owns a private conversation history."""

    name = "npc_actor"

    def __init__(
        self,
        client: MinimaxClient,
        settings: Settings | None = None,
        *,
        history: list[dict] | None = None,
    ) -> None:
        super().__init__(client, settings=settings)
        self.model = self.settings.model_quality  # voice matters
        self._history: list[dict[str, Any]] = list(history or [])

    @property
    def history(self) -> list[dict[str, Any]]:
        """Copy of the conversation so far (user + assistant alternation)."""
        return list(self._history)

    async def speak(
        self,
        npc: NPC,
        player_utterance: str,
        scene_context: dict | None = None,
        *,
        temperature: float = 0.8,
        max_tokens: int = 350,
        language: str = "en",
    ) -> NPCResponse:
        """Have ``npc`` respond to ``player_utterance``.

        Builds the per-NPC system prompt fresh from the current NPC fields, so
        any memory/disposition mutations the keeper applied between turns are
        reflected automatically. ``language`` ("en", "ru", …) is appended to
        the system prompt — only the ``reply`` field is asked to be in that
        language; the JSON shape and ``remember`` stay English-keyed for the
        keeper.
        """
        sys_prompt = build_npc_system_prompt(npc, scene_context) + language_suffix(language, reply_only=True)
        messages = [
            {"role": "system", "content": sys_prompt},
            *self._history,
            {"role": "user", "content": player_utterance},
        ]
        result = await self.client.chat(
            messages,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Append BEFORE parsing so a parser failure still leaves a record.
        self._history.append({"role": "user", "content": player_utterance})
        self._history.append(result.to_assistant_message())  # preserves <think>
        return parse_npc_response(result.content)


__all__ = [
    "NPCActor",
    "NPCResponse",
    "NPCParseError",
    "build_npc_system_prompt",
    "parse_npc_response",
]
