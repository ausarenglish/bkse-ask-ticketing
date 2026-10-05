"""Mocked tests for the Anthropic adapter. No network calls, no real API key."""

import json
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from ask_ticketing.anthropic_api import THINKING_ALLOWANCE, AnthropicModel, parse_messages_response, strict_schema
from ask_ticketing.model import (
    ModelAccessError,
    ModelAuthError,
    ModelConfigError,
    ModelOutputError,
    ModelRequestError,
    ModelUnavailableError,
    ToolSpec,
)
from ask_ticketing.prompts import DECIDE_TOOL

TOOL = ToolSpec("decide", "Decide how to answer.", {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]})


def response(text='{"action": "clarify"}', stop="end_turn", blocks=None):
    content = blocks if blocks is not None else [NS(type="thinking", thinking=""), NS(type="text", text=text)]
    return NS(content=content, stop_reason=stop, usage=NS(input_tokens=900, output_tokens=120))


class FakeMessages:
    def __init__(self, result):
        self.result, self.requests = result, []

    def create(self, **request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def model_with(result):
    messages = FakeMessages(result)
    return AnthropicModel(client=NS(messages=messages)), messages


def api_error(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("raw provider detail sk-ant-secret", response=httpx2.Response(status, request=req), body=None)


def test_request_uses_structured_output_not_forced_tool_choice():
    model, messages = model_with(response())
    out = model.call_tool(system="SYS", prompt="Q", tool=TOOL, max_tokens=1500)
    req = messages.requests[0]
    assert req["model"] == "claude-sonnet-5-5"
    assert req["max_tokens"] == 1500 + THINKING_ALLOWANCE
    assert "tool_choice" not in req and "tools" not in req
    fmt = req["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert req["output_config"]["effort"] == "medium"
    assert req["system"].startswith("SYS") and req["messages"] == [{"role": "user", "content": "Q"}]
    assert (out.data, out.input_tokens, out.output_tokens) == ({"action": "clarify"}, 900, 120)


def test_strict_schema_closes_every_object_without_mutating_the_original():
    nested = {"type": "object", "properties": {"inner": {"type": "object", "properties": {}}, "items": {"type": "array", "items": {"type": "object"}}}}
    out = strict_schema(nested)
    assert out["additionalProperties"] is False
    assert out["properties"]["inner"]["additionalProperties"] is False
    assert out["properties"]["items"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in nested
    assert strict_schema(DECIDE_TOOL.input_schema)["additionalProperties"] is False


@pytest.mark.parametrize(
    "resp, error",
    [
        (response(text="SELECT * FROM tickets"), ModelOutputError),  # free text is never accepted
        (response(text="[1, 2]"), ModelOutputError),
        (response(blocks=[NS(type="thinking", thinking="")]), ModelOutputError),
        (response(stop="max_tokens"), ModelOutputError),
        (response(stop="refusal"), ModelRequestError),
    ],
)
def test_parse_rejects_bad_or_incomplete_output(resp, error):
    with pytest.raises(error):
        parse_messages_response(resp)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (api_error(anthropic.AuthenticationError, 401), ModelAuthError),
        (api_error(anthropic.PermissionDeniedError, 403), ModelAccessError),
        (api_error(anthropic.NotFoundError, 404), ModelConfigError),
        (api_error(anthropic.RateLimitError, 429), ModelUnavailableError),
        (api_error(anthropic.InternalServerError, 500), ModelUnavailableError),
        (api_error(anthropic.BadRequestError, 400), ModelRequestError),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")), ModelUnavailableError),
    ],
)
def test_sdk_errors_are_translated_without_leaking_details(exc, expected):
    model, _ = model_with(exc)
    with pytest.raises(expected) as info:
        model.call_tool(system="S", prompt="P", tool=TOOL, max_tokens=10)
    assert "sk-ant" not in str(info.value) and "raw provider detail" not in str(info.value)


def test_auth_and_access_errors_are_not_marked_retryable():
    for exc in (api_error(anthropic.AuthenticationError, 401), api_error(anthropic.PermissionDeniedError, 403)):
        model, messages = model_with(exc)
        with pytest.raises((ModelAuthError, ModelAccessError)) as info:
            model.call_tool(system="S", prompt="P", tool=TOOL, max_tokens=10)
        assert info.value.retryable is False and len(messages.requests) == 1


def test_sdk_retries_are_bounded(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    model = AnthropicModel()
    assert model._client.max_retries == 1


def test_workflow_runs_end_to_end_through_the_adapter(db_path):
    """Adapter + workflow + SQL guard together, with a mocked SDK client."""
    from ask_ticketing.workflow import ask

    replies = iter([
        response(text=json.dumps({"action": "sql", "sql": "SELECT COUNT(*) AS n FROM venues", "assumptions": ["All venues."]})),
        response(text=json.dumps({"answer": "There are 2 venues."})),
    ])
    client = NS(messages=NS(create=lambda **_: next(replies)))
    r = ask("How many venues?", AnthropicModel(client=client), db_path)
    assert (r.status, r.rows, r.answer) == ("answered", [[2]], "There are 2 venues.")


def test_settings_are_per_model_and_unverified_models_are_rejected():
    sonnet = AnthropicModel(client=NS(messages=None))
    assert (sonnet.model_id, sonnet.effort, sonnet.usd_per_token) == ("claude-sonnet-5-5", "medium", (2e-6, 1e-5))
    assert AnthropicModel(model_id="claude-opus-5-5", client=NS(messages=None)).usd_per_token == (4e-6, 2e-5)
    with pytest.raises(ModelConfigError):
        AnthropicModel(model_id="claude-unknown-9", client=NS(messages=None))
