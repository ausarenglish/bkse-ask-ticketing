"""Anthropic Claude API adapter for the `ModelProvider` port (the default provider).

Structured output uses `output_config.format` (JSON schema), because current
Claude models reject forced tool choice. The returned JSON is still validated
by the application (pydantic) before anything is executed.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

import anthropic

from ask_ticketing.model import (
    ModelAccessError,
    ModelAuthError,
    ModelConfigError,
    ModelOutputError,
    ModelRequestError,
    ModelResponse,
    ModelUnavailableError,
    ToolSpec,
)

DEFAULT_MODEL_ID = "claude-sonnet-5-5"

# Per-model settings, verified against platform.claude.com docs (models overview,
# pricing) on 2026-10-03. Thinking tokens count toward max_tokens, so each call gets
# `thinking_allowance` on top of the workflow's visible-answer limit.
#  - Sonnet 5.5: adaptive thinking by default (not forced on); effort default is
#    `high` with recalibrated levels, so it is set explicitly here.
#  - Opus 5.5: thinking is always on; effort default is `medium`.
MODEL_SETTINGS = {
    "claude-sonnet-5-5": {"effort": "medium", "thinking_allowance": 4000, "usd_per_mtok": (2.00, 10.00)},
    "claude-opus-5-5": {"effort": "medium", "thinking_allowance": 4000, "usd_per_mtok": (4.00, 20.00)},
}
THINKING_ALLOWANCE = MODEL_SETTINGS[DEFAULT_MODEL_ID]["thinking_allowance"]
_KEY_HINT = 'Export your Anthropic API key as ANTHROPIC_API_KEY (README: "Choose your provider").'
MISSING_KEY = "The selected provider is Anthropic (the default), but ANTHROPIC_API_KEY is not set. " + _KEY_HINT


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs require additionalProperties: false on every object."""
    out = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(out)
    return out


class AnthropicModel:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        effort: str | None = None,
        max_retries: int = 1,
        timeout: float = 90.0,
        client: Any = None,
    ):
        if model_id not in MODEL_SETTINGS:
            raise ModelConfigError(f"Model '{model_id}' has no verified settings in anthropic_api.MODEL_SETTINGS.")
        settings = MODEL_SETTINGS[model_id]
        self.model_id = model_id
        self.effort = effort or settings["effort"]
        self.thinking_allowance = settings["thinking_allowance"]
        self.usd_per_token = tuple(p / 1e6 for p in settings["usd_per_mtok"])
        self.max_retries = max_retries
        if client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise ModelAuthError(MISSING_KEY)
            try:
                client = anthropic.Anthropic(max_retries=max_retries, timeout=timeout)
            except anthropic.AnthropicError:
                raise ModelConfigError("Anthropic API credentials are not configured. " + _KEY_HINT) from None
        self._client = client

    def call_tool(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> ModelResponse:
        try:
            response = self._client.messages.create(**self.build_request(system=system, prompt=prompt, tool=tool, max_tokens=max_tokens))
        except anthropic.AuthenticationError:
            raise ModelAuthError("The Anthropic API key is missing or invalid. " + _KEY_HINT) from None
        except anthropic.PermissionDeniedError:
            raise ModelAccessError("The Anthropic API refused access for this key or model.") from None
        except anthropic.NotFoundError:
            raise ModelConfigError(f"Model '{self.model_id}' was not found for this API key.") from None
        except (anthropic.RateLimitError, anthropic.OverloadedError, anthropic.InternalServerError):
            raise ModelUnavailableError("The Anthropic API is busy or unavailable; try again shortly.", retryable=True) from None
        except anthropic.APIConnectionError:  # includes timeouts
            raise ModelUnavailableError("Could not reach the Anthropic API (network or timeout).", retryable=True) from None
        except anthropic.APIStatusError as exc:
            raise ModelRequestError(f"The Anthropic API rejected the request (HTTP {exc.status_code}).") from None
        except TypeError as exc:  # the SDK raises TypeError when no credential can be resolved
            if "authentication" in str(exc).lower():
                raise ModelAuthError(MISSING_KEY) from None
            raise
        return parse_messages_response(response)

    def output_cap(self, max_tokens: int) -> int:
        """The max_tokens actually sent: visible-answer limit plus thinking allowance."""
        return max_tokens + self.thinking_allowance

    def build_request(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_tokens": self.output_cap(max_tokens),
            "system": f"{system}\n\nRespond with only the JSON object for `{tool.name}`: {tool.description}",
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": strict_schema(tool.input_schema)}},
        }


def parse_messages_response(response: Any) -> ModelResponse:
    """Extract the JSON object from a structured-output Messages response."""
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        raise ModelRequestError("The model declined to answer this request.")
    if stop == "max_tokens":
        raise ModelOutputError("The model's structured answer was cut off (output token limit).")
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    if text is None:
        raise ModelOutputError("The model did not return a structured answer.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ModelOutputError("The model's answer was not valid JSON.") from None
    if not isinstance(data, dict):
        raise ModelOutputError("The model's structured answer was not a JSON object.")
    usage = getattr(response, "usage", None)
    return ModelResponse(data, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
