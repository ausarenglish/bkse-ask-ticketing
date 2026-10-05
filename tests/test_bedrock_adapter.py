"""Mocked tests for the Bedrock adapter. No AWS calls; credentials are isolated per test."""

import json

import botocore.exceptions as bx
import pytest

from ask_ticketing.bedrock import DEFAULT_MODEL_ID, BedrockModel, parse_converse_response
from ask_ticketing.model import (
    ModelAccessError,
    ModelAuthError,
    ModelConfigError,
    ModelOutputError,
    ModelRequestError,
    ModelUnavailableError,
    ToolSpec,
)

TOOL = ToolSpec("decide", "Decide how to answer.", {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]})


@pytest.fixture
def no_aws(monkeypatch, tmp_path):
    """An empty AWS environment: no env credentials, no config files, no instance metadata."""
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
                "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
                "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    return tmp_path


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def converse(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def converse_response(content, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop, "usage": {"inputTokens": 120, "outputTokens": 30}}


def client_error(code, message):
    return bx.ClientError({"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": 400}}, "Converse")


def test_request_uses_structured_output_not_forced_tool_choice():
    client = FakeClient(converse_response([{"text": '{"action": "clarify"}'}]))
    out = BedrockModel(client=client).call_tool(system="S", prompt="P", tool=TOOL, max_tokens=50)
    req = client.requests[0]
    assert req["modelId"] == DEFAULT_MODEL_ID == "global.anthropic.claude-sonnet-4-6"
    assert "toolConfig" not in req
    fmt = req["outputConfig"]["textFormat"]
    assert fmt["type"] == "json_schema" and fmt["structure"]["jsonSchema"]["name"] == "decide"
    schema = json.loads(fmt["structure"]["jsonSchema"]["schema"])  # AWS takes the schema as a JSON string
    assert schema["additionalProperties"] is False and schema["required"] == ["action"]
    assert req["system"][0]["text"].startswith("S") and req["messages"][0]["content"] == [{"text": "P"}]
    assert req["inferenceConfig"] == {"maxTokens": 50}
    assert (out.data, out.input_tokens, out.output_tokens) == ({"action": "clarify"}, 120, 30)


@pytest.mark.parametrize(
    "resp, error",
    [
        (converse_response([{"text": "SELECT * FROM tickets"}]), ModelOutputError),  # free text is never accepted
        (converse_response([{"text": "[1]"}]), ModelOutputError),
        (converse_response([]), ModelOutputError),
        (converse_response([{"text": '{"action": "sql"'}], stop="max_tokens"), ModelOutputError),
        (converse_response([], stop="malformed_model_output"), ModelOutputError),
        (converse_response([], stop="content_filtered"), ModelRequestError),
        (converse_response([], stop="guardrail_intervened"), ModelRequestError),
    ],
)
def test_parse_rejects_unstructured_truncated_or_filtered_output(resp, error):
    with pytest.raises(error):
        parse_converse_response(resp)


@pytest.mark.parametrize(
    "error, expected",
    [
        (client_error("ValidationException", "Error 002: Access to Bedrock models is not allowed for this account"), ModelAccessError),
        (client_error("AccessDeniedException", "You don't have access to the model"), ModelAccessError),
        (client_error("ExpiredTokenException", "The security token included in the request is expired"), ModelAuthError),
        (client_error("UnrecognizedClientException", "The security token included in the request is invalid"), ModelAuthError),
        (client_error("ResourceNotFoundException", "Model not found"), ModelConfigError),
        (client_error("ResourceNotFoundException", "Model use case details have not been submitted for this account."), ModelAccessError),
        (client_error("ThrottlingException", "Too many requests"), ModelUnavailableError),
        (client_error("ModelErrorException", "Upstream error"), ModelUnavailableError),
        (client_error("ValidationException", "Malformed input request"), ModelRequestError),
        (bx.NoCredentialsError(), ModelAuthError),
        (bx.EndpointConnectionError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com"), ModelUnavailableError),
    ],
)
def test_provider_errors_are_translated(error, expected):
    with pytest.raises(expected) as info:
        BedrockModel(client=FakeClient(error=error)).call_tool(system="S", prompt="P", tool=TOOL, max_tokens=10)
    assert "security token" not in str(info.value) and "Error 002" not in str(info.value)  # raw provider text is not passed through


def test_access_error_names_the_prerequisites():
    with pytest.raises(ModelAccessError) as info:
        BedrockModel(client=FakeClient(error=client_error("AccessDeniedException", "x"))).call_tool(system="S", prompt="P", tool=TOOL, max_tokens=10)
    assert "model access" in str(info.value) and "bedrock:InvokeModel" in str(info.value)


def test_validation_error_on_an_unlisted_model_points_to_the_default():
    model = BedrockModel(model_id="global.anthropic.claude-sonnet-5-5", client=FakeClient(error=client_error("ValidationException", "x")))
    with pytest.raises(ModelConfigError) as info:
        model.call_tool(system="S", prompt="P", tool=TOOL, max_tokens=10)
    assert DEFAULT_MODEL_ID in str(info.value)
    assert model.usd_per_token is None  # no claimed prices for unlisted models


def test_missing_credentials_fail_at_setup_with_a_bedrock_specific_message(no_aws):
    with pytest.raises(ModelAuthError) as info:
        BedrockModel()
    assert "Amazon Bedrock" in str(info.value) and "ASK_TICKETING_AWS_PROFILE" in str(info.value)


def test_unknown_profile_is_a_config_error(no_aws):
    with pytest.raises(ModelConfigError) as info:
        BedrockModel(profile="definitely-not-a-configured-profile")
    assert "definitely-not-a-configured-profile" in str(info.value)


def test_standard_chain_named_profile_and_region_are_honoured(no_aws, monkeypatch):
    (no_aws / "credentials").write_text("[reviewer]\naws_access_key_id = AKIATESTNOTREAL0000\naws_secret_access_key = test-not-real\n")
    (no_aws / "config").write_text("[profile reviewer]\nregion = eu-west-1\n")
    model = BedrockModel(profile="reviewer", max_retries=0)
    assert model.region == "eu-west-1" and model._client.meta.region_name == "eu-west-1"
    assert model._client.meta.config.retries["total_max_attempts"] == 1
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATESTNOTREAL0001")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-not-real")
    default_chain = BedrockModel(region="us-west-2")  # env credentials, no profile, explicit region
    assert default_chain.region == "us-west-2"
    assert BedrockModel().region == "us-east-1"  # fallback when nothing names a region


def test_workflow_runs_end_to_end_through_the_adapter(db_path):
    """Adapter + workflow + SQL guard together, with a mocked Bedrock client."""
    from ask_ticketing.workflow import ask

    decide = {"action": "sql", "sql": "SELECT COUNT(*) AS n FROM venues", "assumptions": ["All venues."], "event_filtered_total": False}
    replies = iter([converse_response([{"text": json.dumps(decide)}]), converse_response([{"text": json.dumps({"answer": "There are 2 venues."})}])])

    class Client:
        def converse(self, **_):
            return next(replies)

    r = ask("How many venues?", BedrockModel(client=Client()), db_path)
    assert (r.status, r.rows, r.answer) == ("answered", [[2]], "There are 2 venues.")


def test_prices_are_set_only_for_verified_profile_and_region_pairs():
    assert BedrockModel(client=FakeClient()).usd_per_token == pytest.approx((3e-6, 15e-6))  # global profile, us-east-1
    assert BedrockModel(model_id="us.anthropic.claude-sonnet-4-6", client=FakeClient()).usd_per_token == pytest.approx((3.3e-6, 16.5e-6))
    assert BedrockModel(region="eu-west-1", client=FakeClient()).usd_per_token is None  # same profile, unverified region
    assert BedrockModel(model_id="eu.anthropic.claude-sonnet-4-6", region="eu-west-1", client=FakeClient()).usd_per_token is None


def test_dollar_budgeted_evaluation_refuses_unverified_pricing(monkeypatch):
    import run_eval

    monkeypatch.setattr("ask_ticketing.providers.build_model", lambda settings: BedrockModel(region="eu-west-1", client=FakeClient()))
    with pytest.raises(SystemExit) as info:
        run_eval.guarded_model(0.50)
    assert "Pricing is unverified" in str(info.value)
