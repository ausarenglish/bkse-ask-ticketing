"""OpenAI adapter for the `ModelProvider` port (Responses API, strict structured outputs).

Structured output uses `text.format` with a strict JSON schema, OpenAI's documented
mechanism for schema-constrained output. Strict mode needs every property listed as
required, so optional fields are sent as nullable and nulls are dropped again before
the application validates the result (pydantic), keeping the same contract as the
other adapters. Refusals and incomplete responses are reported explicitly.

Model settings were checked against developers.openai.com (models, pricing, structured
outputs, reasoning guides) on 2026-10-04.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

import openai

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

DEFAULT_MODEL_ID = "gpt-6.1-sol"

# Reasoning tokens count toward max_output_tokens, so each call gets `reasoning_allowance`
# on top of the workflow's visible-answer limit. Prices: standard tier list prices.
MODEL_SETTINGS = {
    "gpt-6.1-sol": {"effort": "medium", "reasoning_allowance": 8000, "usd_per_mtok": (2.00, 10.00)},
}
_KEY_HINT = 'Export your OpenAI API key as OPENAI_API_KEY (README: "Choose your provider").'
MISSING_KEY = "The selected provider is OpenAI, but OPENAI_API_KEY is not set. " + _KEY_HINT
_QUOTA_CODES = {"insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}


def strict_openai_schema(schema: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """Return a strict-mode copy of `schema` and the top-level fields that were optional.

    Every object gets additionalProperties: false and lists all properties as required;
    properties that were optional become nullable."""
    out = copy.deepcopy(schema)
    optional_top = set(out.get("properties", {})) - set(out.get("required", []))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                props = node.setdefault("properties", {})
                required = set(node.get("required", []))
                for name, prop in props.items():
                    if name not in required:
                        _make_nullable(prop)
                node["required"] = list(props)
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(out)
    return out, optional_top


def _make_nullable(prop: dict[str, Any]) -> None:
    kind = prop.get("type")
    if isinstance(kind, str):
        prop["type"] = [kind, "null"]
    elif isinstance(kind, list) and "null" not in kind:
        prop["type"] = [*kind, "null"]
    if "enum" in prop and None not in prop["enum"]:
        prop["enum"] = [*prop["enum"], None]


class OpenAIModel:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        effort: str | None = None,
        max_retries: int = 1,
        timeout: float = 90.0,
        client: Any = None,
    ):
        if model_id not in MODEL_SETTINGS:
            raise ModelConfigError(
                f"OpenAI model '{model_id}' has no verified settings in openai_api.MODEL_SETTINGS. The default is {DEFAULT_MODEL_ID} (offline-tested only)."
            )
        settings = MODEL_SETTINGS[model_id]
        self.model_id = model_id
        self.effort = effort or settings["effort"]
        self.reasoning_allowance = settings["reasoning_allowance"]
        self.usd_per_token = tuple(p / 1e6 for p in settings["usd_per_mtok"])
        self.max_retries = max_retries
        if client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise ModelAuthError(MISSING_KEY)
            try:
                client = openai.OpenAI(max_retries=max_retries, timeout=timeout)
            except openai.OpenAIError:
                raise ModelAuthError(MISSING_KEY) from None
        self._client = client

    def output_cap(self, max_tokens: int) -> int:
        """The max_output_tokens actually sent: visible-answer limit plus reasoning allowance."""
        return max_tokens + self.reasoning_allowance

    def build_request(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> dict[str, Any]:
        schema, _ = strict_openai_schema(tool.input_schema)
        return {
            "model": self.model_id,
            "instructions": f"{system}\n\nRespond with only the JSON object for `{tool.name}`: {tool.description}",
            "input": prompt,
            "max_output_tokens": self.output_cap(max_tokens),
            "reasoning": {"effort": self.effort},
            "text": {"format": {"type": "json_schema", "name": tool.name, "schema": schema, "strict": True}},
            "store": False,
        }

    def call_tool(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> ModelResponse:
        try:
            response = self._client.responses.create(**self.build_request(system=system, prompt=prompt, tool=tool, max_tokens=max_tokens))
        except openai.AuthenticationError:
            raise ModelAuthError("The OpenAI API key is missing or invalid. " + _KEY_HINT) from None
        except openai.PermissionDeniedError:
            raise ModelAccessError(f"The OpenAI API refused access to model '{self.model_id}' for this key or project.") from None
        except openai.NotFoundError:
            raise ModelConfigError(f"OpenAI model '{self.model_id}' was not found for this API key.") from None
        except openai.RateLimitError as exc:
            if getattr(exc, "type", None) == "insufficient_quota" or getattr(exc, "code", None) in _QUOTA_CODES:
                raise ModelAccessError("The OpenAI account has no remaining quota or reached its spend limit. Check billing for this API key's project.") from None
            raise ModelUnavailableError("The OpenAI API is rate-limiting requests; try again shortly.", retryable=True) from None
        except openai.InternalServerError:
            raise ModelUnavailableError("The OpenAI API is busy or unavailable; try again shortly.", retryable=True) from None
        except openai.APIConnectionError:  # includes timeouts
            raise ModelUnavailableError("Could not reach the OpenAI API (network or timeout).", retryable=True) from None
        except openai.APIStatusError as exc:
            raise ModelRequestError(f"The OpenAI API rejected the request (HTTP {exc.status_code}).") from None
        return parse_responses_response(response, tool)


def parse_responses_response(response: Any, tool: ToolSpec) -> ModelResponse:
    """Extract the JSON object from a structured-output Responses API response."""
    status = getattr(response, "status", None)
    if status == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "content_filter":
            raise ModelRequestError("The model declined to answer this request.")
        raise ModelOutputError("The model's structured answer was cut off (output token limit).")
    if status != "completed":
        raise ModelOutputError("The model did not complete a structured answer.")
    text = None
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue  # e.g. reasoning items
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                raise ModelRequestError("The model declined to answer this request.")
            if getattr(part, "type", None) == "output_text" and text is None:
                text = part.text
    if text is None:
        raise ModelOutputError("The model did not return a structured answer.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ModelOutputError("The model's answer was not valid JSON.") from None
    if not isinstance(data, dict):
        raise ModelOutputError("The model's structured answer was not a JSON object.")
    _, optional = strict_openai_schema(tool.input_schema)
    data = {k: v for k, v in data.items() if not (k in optional and v is None)}
    usage = getattr(response, "usage", None)
    return ModelResponse(data, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
