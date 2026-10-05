"""Amazon Bedrock adapter for the `ModelProvider` port (Converse API, structured outputs).

Structured output uses Converse `outputConfig.textFormat` (JSON schema), the mechanism
AWS documents for schema-constrained output. Forced tool choice is not used: current
Claude models reject it. The returned JSON is still validated by the application.

Model choice (checked against AWS model cards and Anthropic's Bedrock docs, 2026-10-04):
Claude Sonnet 5.5 and Opus 5.5 are documented as NOT supporting structured outputs on
Bedrock, so the default is Claude Sonnet 4.6 (structured outputs listed as supported),
called through its global cross-region inference profile.

Credentials come from the standard AWS credential chain (environment variables, shared
config/credentials files, SSO/login, container or instance roles). A named profile is
optional (ASK_TICKETING_AWS_PROFILE). This adapter never changes AWS resources.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
import botocore.exceptions as bx
from botocore.config import Config

from ask_ticketing.anthropic_api import strict_schema
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

DEFAULT_REGION = "us-east-1"  # used only when neither settings nor AWS config name a region
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# On-demand list prices (USD per million input/output tokens), verified from the official
# AWS Price List API (offer AmazonBedrockFoundationModels, us-east-1, published 2026-09-30,
# "Claude Sonnet 4.6 (Amazon Bedrock Edition)"). Keyed by (inference profile, region):
# any other combination is UNVERIFIED, has no price here, and the dollar-budgeted
# evaluation scripts refuse to run it.
VERIFIED_PRICES = {
    ("global.anthropic.claude-sonnet-4-6", "us-east-1"): (3.00, 15.00),  # "Global"
    ("us.anthropic.claude-sonnet-4-6", "us-east-1"): (3.30, 16.50),  # "Regional CRIS"
}
SUPPORTED_MODEL_IDS = {"global.anthropic.claude-sonnet-4-6", "us.anthropic.claude-sonnet-4-6", "eu.anthropic.claude-sonnet-4-6"}

_AUTH_CODES = {"ExpiredTokenException", "UnrecognizedClientException", "InvalidSignatureException", "IncompleteSignature"}
_UNAVAILABLE_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ModelErrorException",
}
_CREDENTIALS_HINT = (
    "Configure AWS credentials through the standard AWS credential chain (for example `aws configure`, "
    "`aws sso login`, or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY), or name a profile with "
    'ASK_TICKETING_AWS_PROFILE (README: "Choose your provider").'
)
_ACCESS_HINT = (
    "Check that this account can invoke the model in this region: Bedrock model access for Anthropic models "
    "(including Anthropic's first-time-use form), IAM permission bedrock:InvokeModel, and a valid AWS Marketplace "
    'payment method (README: "Choose your provider").'
)


class BedrockModel:
    effort = None  # not sent: Sonnet 4.6 runs without extended thinking here

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        region: str | None = None,
        profile: str | None = None,
        max_retries: int = 1,
        read_timeout: int = 90,
        client: Any = None,
    ):
        self.model_id = model_id
        self.max_retries = max_retries
        if client is not None:
            self._client = client
            self._set_region(region or DEFAULT_REGION)
            return
        source = f"AWS profile '{profile}'" if profile else "the standard AWS credential chain"
        try:
            session = boto3.Session(profile_name=profile, region_name=region)
        except bx.ProfileNotFound:
            raise ModelConfigError(f"AWS profile '{profile}' (ASK_TICKETING_AWS_PROFILE) is not configured on this machine. " + _CREDENTIALS_HINT) from None
        try:
            credentials = session.get_credentials()
        except bx.MissingDependencyException:
            raise ModelConfigError("These AWS credentials need the AWS CRT library; run `uv sync --locked` (it installs boto3[crt]).") from None
        except bx.BotoCoreError:
            raise ModelAuthError(f"AWS credentials from {source} could not be loaded or have expired. " + _CREDENTIALS_HINT) from None
        if credentials is None:
            raise ModelAuthError(f"The selected provider is Amazon Bedrock, but no AWS credentials were found in {source}. " + _CREDENTIALS_HINT)
        self._set_region(session.region_name or DEFAULT_REGION)
        self._client = session.client(
            "bedrock-runtime",
            region_name=self.region,
            config=Config(
                retries={"total_max_attempts": 1 + max_retries, "mode": "standard"},
                read_timeout=read_timeout,
                connect_timeout=10,
            ),
        )

    def _set_region(self, region: str) -> None:
        self.region = region
        prices = VERIFIED_PRICES.get((self.model_id, region))
        self.usd_per_token = tuple(p / 1e6 for p in prices) if prices else None  # None: pricing unverified

    def output_cap(self, max_tokens: int) -> int:
        """The maxTokens actually sent (no thinking allowance: thinking is not enabled)."""
        return max_tokens

    def build_request(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> dict[str, Any]:
        return {
            "modelId": self.model_id,
            "system": [{"text": f"{system}\n\nRespond with only the JSON object for `{tool.name}`: {tool.description}"}],
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": self.output_cap(max_tokens)},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {"jsonSchema": {"schema": json.dumps(strict_schema(tool.input_schema)), "name": tool.name}},
                }
            },
        }

    def call_tool(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> ModelResponse:
        try:
            response = self._client.converse(**self.build_request(system=system, prompt=prompt, tool=tool, max_tokens=max_tokens))
        except bx.ClientError as exc:
            raise self._translate_client_error(exc) from None
        except (bx.NoCredentialsError, bx.CredentialRetrievalError, bx.TokenRetrievalError):
            raise ModelAuthError("AWS credentials are missing or expired. " + _CREDENTIALS_HINT) from None
        except bx.MissingDependencyException:
            raise ModelConfigError("These AWS credentials need the AWS CRT library; run `uv sync --locked` (it installs boto3[crt]).") from None
        except (bx.EndpointConnectionError, bx.ConnectTimeoutError, bx.ReadTimeoutError):
            raise ModelUnavailableError("Could not reach Amazon Bedrock (network or timeout).", retryable=True) from None
        return parse_converse_response(response)

    def _translate_client_error(self, exc: bx.ClientError):
        error = exc.response.get("Error", {})
        code, message = error.get("Code", ""), error.get("Message", "")
        if code == "AccessDeniedException" or "not allowed for this account" in message:
            return ModelAccessError("Amazon Bedrock refused access for this account or model. " + _ACCESS_HINT)
        if code in _AUTH_CODES:
            return ModelAuthError("AWS credentials were rejected or have expired. " + _CREDENTIALS_HINT)
        if code == "ResourceNotFoundException" and "use case" in message.lower():
            # Bedrock reports a missing Anthropic first-time-use form as "not found" (observed live, 2026-10-04).
            return ModelAccessError("Amazon Bedrock requires Anthropic's use-case details form for this account before this model can be used. " + _ACCESS_HINT)
        if code == "ResourceNotFoundException":
            return ModelConfigError(
                f"Amazon Bedrock could not find model '{self.model_id}' in this region. Check ASK_TICKETING_MODEL_ID and ASK_TICKETING_REGION."
            )
        if code in _UNAVAILABLE_CODES:
            return ModelUnavailableError(f"Amazon Bedrock is temporarily unavailable ({code}).", retryable=True)
        if code == "ValidationException" and self.model_id not in SUPPORTED_MODEL_IDS:
            return ModelConfigError(
                f"Amazon Bedrock rejected the request for model '{self.model_id}'. This app needs Bedrock structured outputs; "
                f"the default is {DEFAULT_MODEL_ID} (offline-tested only)."
            )
        return ModelRequestError(f"Amazon Bedrock rejected the request ({code}).")


def parse_converse_response(response: dict[str, Any]) -> ModelResponse:
    """Extract the JSON object from a structured-output Converse response."""
    stop = response.get("stopReason")
    if stop == "max_tokens":
        raise ModelOutputError("The model's structured answer was cut off (output token limit).")
    if stop in ("content_filtered", "guardrail_intervened"):
        raise ModelRequestError("The model declined to answer this request.")
    if stop in ("malformed_model_output", "malformed_tool_use"):
        raise ModelOutputError("The model did not return a valid structured answer.")
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = next((block["text"] for block in content if isinstance(block.get("text"), str)), None)
    if text is None:
        raise ModelOutputError("The model did not return a structured answer.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ModelOutputError("The model's answer was not valid JSON.") from None
    if not isinstance(data, dict):
        raise ModelOutputError("The model's structured answer was not a JSON object.")
    usage = response.get("usage", {})
    return ModelResponse(data, usage.get("inputTokens", 0) or 0, usage.get("outputTokens", 0) or 0)
