"""TaleForge runtime configuration.

Loads ``MINIMAX_API_KEY`` (and optional ``MINIMAX_BASE_URL``) from the
environment, transparently sourcing them from a local ``.env`` file via
python-dotenv. No network or filesystem I/O happens at import beyond the
idempotent ``load_dotenv`` call.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

# Idempotent; safe to call repeatedly. Returns False if no .env file is found,
# which is fine — env vars from the surrounding shell still win.
load_dotenv()


class Settings(BaseModel):
    """Frozen runtime configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── Gateway credentials ─────────────────────────────────────────────────
    # Minimax is reached via the gngn.my gateway (OpenAI-compatible
    # chat-completions). Gateway-specific model name strings are mapped
    # internally in MinimaxClient, see _GATEWAY_MODEL_NAMES there.
    minimax_api_key: str | None = None
    minimax_base_url: str = "https://api.gngn.my/v1"

    # ── Cost-aware model selection (design rule #6) ─────────────────────────
    # Logical model names; the client maps them to gateway-specific names
    # via the GATEWAY_*_MODEL env vars (see .env.example).
    model_quality: str = "opus-4-7"          # Narrator, NPCActor
    model_fast: str = "haiku-4-5"            # Orchestrator, Director, RulesLawyer

    # ── Gateway-specific model name overrides (read from env) ───────────────
    # Empty string = passthrough (the logical name is sent as-is).
    gateway_opus_model: str = ""
    gateway_sonnet_model: str = ""
    gateway_haiku_model: str = ""

    # ── Filesystem layout ───────────────────────────────────────────────────
    traces_dir: Path = Field(default_factory=lambda: Path("traces"))
    saves_dir: Path = Field(default_factory=lambda: Path("saves"))

    # ── HTTP client behavior ────────────────────────────────────────────────
    # 90s timeout: the gateway's reasoning passes can take a while on opus.
    request_timeout_s: float = 90.0
    max_retries: int = 3


def get_settings() -> Settings:
    """Build a fresh ``Settings`` from current environment variables.

    Tests that mutate env between calls should use this rather than the
    module-level ``settings`` singleton.
    """

    return Settings(
        minimax_api_key=os.getenv("MINIMAX_API_KEY"),
        minimax_base_url=os.getenv(
            "MINIMAX_BASE_URL", "https://api.gngn.my/v1"
        ),
        gateway_opus_model=os.getenv("GATEWAY_OPUS_MODEL", ""),
        gateway_sonnet_model=os.getenv("GATEWAY_SONNET_MODEL", ""),
        gateway_haiku_model=os.getenv("GATEWAY_HAIKU_MODEL", ""),
    )


# Module-level convenience instance — reflects env at first import.
settings: Settings = get_settings()


__all__ = ["Settings", "get_settings", "settings"]
