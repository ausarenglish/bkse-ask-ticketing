"""Builds the configured ModelProvider. Shared by the CLI, the UI and the eval scripts.

Configuration comes from the environment (or explicit overrides), never from UI code.
Selection is explicit: exactly one provider is built, with no automatic fallback and
no cross-provider retries. A failure names what the reviewer needs to fix.

- `anthropic` (default): Anthropic Claude API, key from ANTHROPIC_API_KEY.
- `bedrock`: Amazon Bedrock, standard AWS credential chain (optional named profile).
- `openai`: OpenAI API, key from OPENAI_API_KEY.

To add a provider: write an adapter implementing `ModelProvider` and add one branch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from ask_ticketing.model import ModelConfigError, ModelProvider

PROVIDERS = ("anthropic", "bedrock", "openai")


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "anthropic"
    model_id: str | None = None  # None -> the adapter's default model
    region: str | None = None  # bedrock only; None -> AWS config/env, else us-east-1
    profile: str | None = None  # bedrock only; None -> standard AWS credential chain
    max_retries: int | None = None  # SDK retries; None -> adapter default (1)

    @classmethod
    def from_env(cls, **overrides) -> "ProviderSettings":
        env = cls(
            provider=os.environ.get("ASK_TICKETING_PROVIDER") or cls.provider,
            model_id=os.environ.get("ASK_TICKETING_MODEL_ID") or None,
            region=os.environ.get("ASK_TICKETING_REGION") or None,
            profile=os.environ.get("ASK_TICKETING_AWS_PROFILE") or None,
            max_retries=int(os.environ["ASK_TICKETING_MAX_RETRIES"]) if os.environ.get("ASK_TICKETING_MAX_RETRIES") else None,
        )
        return replace(env, **{k: v for k, v in overrides.items() if v is not None})


def build_model(settings: ProviderSettings | None = None) -> ModelProvider:
    settings = settings or ProviderSettings.from_env()
    retries = {} if settings.max_retries is None else {"max_retries": settings.max_retries}
    provider = settings.provider.strip().lower()
    if provider == "anthropic":
        from ask_ticketing.anthropic_api import DEFAULT_MODEL_ID, AnthropicModel

        return AnthropicModel(model_id=settings.model_id or DEFAULT_MODEL_ID, **retries)
    if provider == "bedrock":
        from ask_ticketing.bedrock import DEFAULT_MODEL_ID, BedrockModel

        return BedrockModel(model_id=settings.model_id or DEFAULT_MODEL_ID, region=settings.region, profile=settings.profile, **retries)
    if provider == "openai":
        from ask_ticketing.openai_api import DEFAULT_MODEL_ID, OpenAIModel

        return OpenAIModel(model_id=settings.model_id or DEFAULT_MODEL_ID, **retries)
    raise ModelConfigError(
        f"Unknown model provider '{settings.provider}'. Set ASK_TICKETING_PROVIDER to one of: {', '.join(PROVIDERS)} (see README)."
    )
