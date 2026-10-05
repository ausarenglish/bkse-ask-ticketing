"""Provider selection and setup errors. Offline: no model calls, test-only credentials."""

import pytest

from ask_ticketing import cli
from ask_ticketing.anthropic_api import AnthropicModel
from ask_ticketing.bedrock import BedrockModel
from ask_ticketing.model import ModelAuthError, ModelConfigError
from ask_ticketing.openai_api import OpenAIModel
from ask_ticketing.providers import PROVIDERS, ProviderSettings, build_model

PROVIDER_VARS = ("ASK_TICKETING_PROVIDER", "ASK_TICKETING_MODEL_ID", "ASK_TICKETING_REGION", "ASK_TICKETING_AWS_PROFILE", "ASK_TICKETING_MAX_RETRIES")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in (*PROVIDER_VARS, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "AWS_PROFILE", "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_default_provider_is_anthropic_with_no_aws_requirement(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    model = build_model()
    assert isinstance(model, AnthropicModel) and model.model_id == "claude-sonnet-5-5"


def test_model_override_and_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("ASK_TICKETING_MODEL_ID", "claude-opus-5-5")
    assert build_model().model_id == "claude-opus-5-5"
    assert build_model(ProviderSettings.from_env(model_id="claude-sonnet-5-5", max_retries=0)).max_retries == 0


def test_each_provider_is_selected_explicitly(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-not-real")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATESTNOTREAL0000")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-not-real")
    expected = {"anthropic": (AnthropicModel, "claude-sonnet-5-5"), "bedrock": (BedrockModel, "global.anthropic.claude-sonnet-4-6"),
                "openai": (OpenAIModel, "gpt-6.1-sol")}
    assert set(expected) == set(PROVIDERS)
    for name, (cls, model_id) in expected.items():
        monkeypatch.setenv("ASK_TICKETING_PROVIDER", name)
        model = build_model()
        assert type(model) is cls and model.model_id == model_id and model.max_retries == 1


@pytest.mark.parametrize("provider, needle", [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"), ("bedrock", "AWS credentials")])
def test_missing_credentials_name_the_selected_provider_and_never_fall_back(monkeypatch, provider, needle):
    # Other providers' credentials are present: selection must still not switch to them.
    for var, value in {"ANTHROPIC_API_KEY": "sk-ant-test-not-real", "OPENAI_API_KEY": "sk-proj-test-not-real"}.items():
        if needle != var:
            monkeypatch.setenv(var, value)
    with pytest.raises(ModelAuthError) as info:
        build_model(ProviderSettings(provider=provider))
    assert needle in str(info.value) and "Choose your provider" in str(info.value)


def test_unknown_provider_is_a_config_error():
    with pytest.raises(ModelConfigError) as info:
        build_model(ProviderSettings(provider="nope"))
    assert "anthropic, bedrock, openai" in str(info.value)


def test_cli_reports_missing_key_without_traceback(monkeypatch, capsys):
    monkeypatch.setenv("ASK_TICKETING_PROVIDER", "openai")
    assert cli.main(["How many tickets?"]) == 2
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err and "Traceback" not in err


def test_cli_provider_flag_overrides_env(monkeypatch, capsys):
    monkeypatch.setenv("ASK_TICKETING_PROVIDER", "openai")
    assert cli.main(["How many tickets?", "--provider", "anthropic"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_anthropic_request_time_credential_failure_is_mapped(monkeypatch):
    """Safety net: if the SDK cannot resolve credentials at request time it raises TypeError."""
    from types import SimpleNamespace as NS

    from ask_ticketing.prompts import SUMMARIZE_TOOL

    def create(**_):
        raise TypeError('"Could not resolve authentication method. Expected either api_key or auth_token to be set."')

    model = AnthropicModel(client=NS(messages=NS(create=create)))
    with pytest.raises(ModelAuthError) as info:
        model.call_tool(system="S", prompt="P", tool=SUMMARIZE_TOOL, max_tokens=10)
    assert "ANTHROPIC_API_KEY" in str(info.value)
