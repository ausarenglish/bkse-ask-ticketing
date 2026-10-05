"""Mocked tests for the OpenAI adapter. No network calls, no real API key."""

import json
from types import SimpleNamespace as NS

import httpx2
import openai
import pytest
from openai.types.responses import Response, ResponseOutputMessage, ResponseReasoningItem, ResponseUsage

from ask_ticketing.model import (
    ModelAccessError,
    ModelAuthError,
    ModelConfigError,
    ModelOutputError,
    ModelRequestError,
    ModelUnavailableError,
    ToolSpec,
)
from ask_ticketing.openai_api import MISSING_KEY, OpenAIModel, parse_responses_response, strict_openai_schema
from ask_ticketing.prompts import DECIDE_TOOL, SUMMARIZE_TOOL
from ask_ticketing.workflow import Decision

TOOL = ToolSpec("decide", "Decide how to answer.", {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]})
USAGE = {"input_tokens": 900, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "output_tokens": 300,
         "output_tokens_details": {"reasoning_tokens": 200}, "total_tokens": 1200}


def message(*parts):
    return ResponseOutputMessage.model_validate({"id": "msg_1", "type": "message", "role": "assistant", "status": "completed", "content": list(parts)})


def text_part(text):
    return {"type": "output_text", "text": text, "annotations": []}


def response(*output, status="completed", incomplete=None):
    """A real SDK Response object (constructed offline), so attribute access matches the SDK."""
    if not output:
        output = (message(text_part('{"action": "clarify"}')),)
    return Response.model_construct(
        id="resp_1", object="response", status=status, output=list(output), usage=ResponseUsage.model_validate(USAGE),
        incomplete_details=NS(reason=incomplete) if incomplete else None,
    )


class FakeResponses:
    def __init__(self, result):
        self.result, self.requests = result, []

    def create(self, **request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def model_with(result):
    responses = FakeResponses(result)
    return OpenAIModel(client=NS(responses=responses)), responses


def status_error(cls, status, body=None):
    req = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return cls("raw provider detail sk-proj-secret", response=httpx2.Response(status, request=req), body=body)


def test_request_uses_strict_json_schema_on_the_responses_api():
    model, responses = model_with(response())
    out = model.call_tool(system="SYS", prompt="Q", tool=TOOL, max_tokens=1500)
    req = responses.requests[0]
    assert req["model"] == "gpt-6.1-sol"
    assert req["max_output_tokens"] == 1500 + model.reasoning_allowance  # reasoning tokens count toward the cap
    assert req["reasoning"] == {"effort": "medium"} and "temperature" not in req
    assert req["store"] is False and "tools" not in req and "tool_choice" not in req
    fmt = req["text"]["format"]
    assert (fmt["type"], fmt["name"], fmt["strict"]) == ("json_schema", "decide", True)
    assert fmt["schema"]["additionalProperties"] is False
    assert req["instructions"].startswith("SYS") and req["input"] == "Q"
    assert (out.data, out.input_tokens, out.output_tokens) == ({"action": "clarify"}, 900, 300)


def test_strict_schema_requires_every_field_and_makes_optional_ones_nullable():
    schema, optional = strict_openai_schema(DECIDE_TOOL.input_schema)
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False
    assert optional == {"sql", "message"}
    assert schema["properties"]["sql"]["type"] == ["string", "null"]
    assert schema["properties"]["action"]["type"] == "string"  # required fields stay non-null
    assert "null" not in json.dumps(DECIDE_TOOL.input_schema)  # original untouched
    enum_schema, _ = strict_openai_schema({"type": "object", "properties": {"k": {"type": "string", "enum": ["a"]}}, "required": []})
    assert enum_schema["properties"]["k"] == {"type": ["string", "null"], "enum": ["a", None]}
    summary, _ = strict_openai_schema(SUMMARIZE_TOOL.input_schema)
    assert summary["required"] == ["answer"]


def test_nulls_for_optional_fields_are_dropped_so_the_app_contract_is_unchanged():
    raw = {"action": "clarify", "sql": None, "message": "Purchase or event date?", "assumptions": [], "event_filtered_total": False}
    out = parse_responses_response(response(message(text_part(json.dumps(raw)))), DECIDE_TOOL)
    assert "sql" not in out.data and Decision.model_validate(out.data).message == "Purchase or event date?"


def test_reasoning_items_are_skipped():
    reasoning = ResponseReasoningItem.model_validate({"id": "rs_1", "type": "reasoning", "summary": []})
    assert parse_responses_response(response(reasoning, message(text_part('{"action": "sql"}'))), TOOL).data == {"action": "sql"}


@pytest.mark.parametrize(
    "resp, error",
    [
        (response(message({"type": "refusal", "refusal": "I can't help with that."})), ModelRequestError),
        (response(status="incomplete", incomplete="max_output_tokens"), ModelOutputError),
        (response(status="incomplete", incomplete="content_filter"), ModelRequestError),
        (response(status="failed"), ModelOutputError),
        (response(message(text_part("SELECT * FROM tickets"))), ModelOutputError),  # free text is never accepted
        (response(message(text_part("[1, 2]"))), ModelOutputError),
        (response(ResponseReasoningItem.model_validate({"id": "rs_1", "type": "reasoning", "summary": []})), ModelOutputError),
    ],
)
def test_refusal_incomplete_and_malformed_output_are_explicit_errors(resp, error):
    with pytest.raises(error):
        parse_responses_response(resp, TOOL)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (status_error(openai.AuthenticationError, 401), ModelAuthError),
        (status_error(openai.PermissionDeniedError, 403), ModelAccessError),
        (status_error(openai.NotFoundError, 404), ModelConfigError),
        (status_error(openai.RateLimitError, 429, {"type": "insufficient_quota", "code": "credit_balance_exhausted"}), ModelAccessError),
        (status_error(openai.RateLimitError, 429, {"type": "requests", "code": "slow_down"}), ModelUnavailableError),
        (status_error(openai.InternalServerError, 500), ModelUnavailableError),
        (status_error(openai.BadRequestError, 400), ModelRequestError),
        (openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses")), ModelUnavailableError),
    ],
)
def test_sdk_errors_are_translated_without_leaking_details(exc, expected):
    model, responses = model_with(exc)
    with pytest.raises(expected) as info:
        model.call_tool(system="S", prompt="P", tool=TOOL, max_tokens=10)
    assert "sk-proj" not in str(info.value) and "raw provider detail" not in str(info.value)
    assert len(responses.requests) == 1


def test_missing_key_is_a_provider_specific_auth_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelAuthError) as info:
        OpenAIModel()
    assert "OPENAI_API_KEY" in str(info.value) and str(info.value) == MISSING_KEY


def test_sdk_retries_are_bounded_and_unverified_models_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-not-real")
    assert OpenAIModel()._client.max_retries == 1
    assert OpenAIModel(max_retries=0)._client.max_retries == 0
    with pytest.raises(ModelConfigError):
        OpenAIModel(model_id="gpt-unknown-9", client=NS(responses=None))


def test_workflow_runs_end_to_end_through_the_adapter(db_path):
    """Adapter + workflow + SQL guard together, with a mocked SDK client."""
    from ask_ticketing.workflow import ask

    decide = {"action": "sql", "sql": "SELECT COUNT(*) AS n FROM venues", "message": None, "assumptions": ["All venues."], "event_filtered_total": False}
    replies = iter([response(message(text_part(json.dumps(decide)))), response(message(text_part(json.dumps({"answer": "There are 2 venues."}))))])
    client = NS(responses=NS(create=lambda **_: next(replies)))
    r = ask("How many venues?", OpenAIModel(client=client), db_path)
    assert (r.status, r.rows, r.answer) == ("answered", [[2]], "There are 2 venues.")
