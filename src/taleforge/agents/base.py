"""Base class for TaleForge agents.

Concrete agents subclass :class:`BaseAgent`, set ``model`` (one of the names
in :class:`Settings`), and add their own resolution methods. The base wires a
shared :class:`MinimaxClient` reference and a tiny ``acomplete`` convenience
so each subclass doesn't have to re-spell ``client.chat(..., model=self.model)``.
"""

from __future__ import annotations

from abc import ABC

from ..config import Settings, get_settings
from ..llm.minimax import CompletionResult, MinimaxClient


class BaseAgent(ABC):
    """Shared base for all TaleForge agents.

    Attributes
    ----------
    name : str
        Short identifier used by trace logging.
    model : str
        Which Minimax model this agent uses; subclasses set it in ``__init__``.
    """

    name: str = "agent"
    model: str = ""

    def __init__(
        self,
        client: MinimaxClient,
        settings: Settings | None = None,
    ) -> None:
        self.client = client
        self.settings = settings or get_settings()

    async def acomplete(self, messages: list[dict], **kw) -> CompletionResult:
        """Thin wrapper around ``client.chat`` that fills in this agent's model."""
        return await self.client.chat(messages, model=self.model, **kw)


__all__ = ["BaseAgent"]
