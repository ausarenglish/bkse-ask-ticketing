"""UI tests with a scripted model. They check the interface, not live NL-to-SQL accuracy."""

from pathlib import Path

import pytest
from fakes import ScriptedModel
from streamlit.testing.v1 import AppTest

from ask_ticketing.model import ModelAccessError, ModelUnavailableError

APP = str(Path(__file__).parents[1] / "src" / "ask_ticketing" / "app.py")
SQL = "SELECT COUNT(*) AS tickets_sold, SUM(price_cents) AS revenue_cents FROM tickets WHERE status = 'sold' AND event_id = 1"


def start(model, db_path):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_model_override"] = model
    at.session_state["_db_override"] = str(db_path)
    return at.run()


def ask(at, question):
    at.text_area(key="question").input(question)
    at.button[-1].click()  # the form's Ask button is the last button
    return at.run()


def test_initial_layout_and_examples_do_not_call_the_model(db_path):
    model = ScriptedModel()
    at = start(model, db_path)
    assert at.title[0].value == "Ask Ticketing"
    assert any("Synthetic ticketing data · As of October 1, 2026" in c.value for c in at.caption)
    at.button(key="example_0").click().run()
    assert at.text_area(key="question").value.startswith("How many tickets were sold for Brooklyn Nets")
    assert model.calls == [] and not at.error


def test_answer_rows_and_sql_render_and_reruns_do_not_call_again(db_path):
    model = ScriptedModel({"action": "sql", "sql": SQL, "assumptions": ["Event-date basis."]}, {"answer": "Event 1 sold some tickets."})
    at = ask(start(model, db_path), "How did event 1 do?")
    assert len(model.calls) == 2
    assert any("Event 1 sold some tickets." in m.value for m in at.markdown)
    assert any("Event-date basis." in m.value for m in at.markdown)
    table = at.dataframe[0].value
    assert list(table.columns) == ["tickets_sold", "revenue_usd"] and str(table.iloc[0, 1]).startswith("$")
    assert at.code[0].value == SQL
    at.run()  # ordinary rerun (e.g. opening the SQL expander)
    at.button(key="example_1").click().run()  # interacting with the page
    assert len(model.calls) == 2  # no duplicate model calls
    assert at.code[0].value == SQL  # last result is still shown


def test_clarification_keeps_question_editable(db_path):
    model = ScriptedModel({"action": "clarify", "message": "Purchase date or event date?", "assumptions": []})
    at = ask(start(model, db_path), "How many tickets did we sell last month?")
    assert any("Purchase date or event date?" in i.value for i in at.info)
    assert at.text_area(key="question").value == "How many tickets did we sell last month?"
    assert not at.dataframe and not at.code


def test_empty_result_state(db_path):
    model = ScriptedModel({"action": "sql", "sql": "SELECT name FROM events WHERE event_date = '1999-01-01'", "assumptions": []})
    at = ask(start(model, db_path), "Events in 1999?")
    assert any("No rows matched" in i.value for i in at.info) and not at.dataframe
    assert len(model.calls) == 1


def test_truncation_notice(db_path):
    model = ScriptedModel({"action": "sql", "sql": "SELECT ticket_id FROM tickets ORDER BY ticket_id", "assumptions": []}, {"answer": "Tickets listed."})
    at = ask(start(model, db_path), "List all tickets")
    assert any("Showing only the first 200 rows" in w.value for w in at.warning)
    assert len(at.dataframe[0].value) == 200


@pytest.mark.parametrize("error", [ModelUnavailableError("Amazon Bedrock is temporarily unavailable (ThrottlingException).", retryable=True)])
def test_provider_unavailable_is_concise(db_path, error):
    at = ask(start(ScriptedModel(error), db_path), "How many tickets?")
    text = at.error[0].value
    assert "currently unavailable" in text
    assert "ThrottlingException" not in text and "Bedrock" not in text


def test_access_refusal_shows_the_providers_fix(db_path):
    """Access problems are fixed by whoever configures the provider, so the adapter's safe message is shown."""
    error = ModelAccessError("Amazon Bedrock refused access for this account or model. Check that this account can invoke the model.")
    at = ask(start(ScriptedModel(error), db_path), "How many tickets?")
    assert "Model provider setup needed" in at.error[0].value and "Amazon Bedrock refused access" in at.error[0].value


@pytest.mark.parametrize("provider, needle", [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"), ("bedrock", "no AWS credentials were found")])
def test_missing_credentials_disable_ask_with_provider_specific_fix(db_path, monkeypatch, tmp_path, provider, needle):
    """No model override: the real provider factory runs at startup and finds no credentials.
    The AWS environment is isolated (no inherited credentials, no instance-metadata lookups)."""
    import anthropic
    import boto3
    import openai

    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "ASK_TICKETING_MODEL_ID", "ASK_TICKETING_AWS_PROFILE",
                "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("ASK_TICKETING_PROVIDER", provider)

    def no_client(*args, **kwargs):
        raise AssertionError("no SDK client may be created without credentials")

    monkeypatch.setattr(anthropic, "Anthropic", no_client)
    monkeypatch.setattr(openai, "OpenAI", no_client)
    monkeypatch.setattr(boto3.Session, "client", no_client)

    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_db_override"] = str(db_path)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Ask Ticketing"  # the page still starts
    assert "Model provider setup needed" in at.error[0].value and needle in at.error[0].value
    assert "Choose your provider" in at.error[0].value
    assert at.button[-1].disabled  # the form's Ask button
    at.button(key="example_0").click().run()  # examples still fill the box
    assert at.text_area(key="question").value.startswith("How many tickets") and not at.exception


def test_ask_is_enabled_when_the_provider_is_configured(db_path):
    at = start(ScriptedModel(), db_path)
    assert not at.button[-1].disabled and not at.error


def test_empty_question_does_not_call_model(db_path):
    model = ScriptedModel()
    at = ask(start(model, db_path), "   ")
    assert model.calls == [] and any("Type a question first" in w.value for w in at.warning)


def test_dollar_amounts_are_escaped_so_markdown_does_not_render_math(db_path):
    """Regression: two '$' amounts in one answer were rendered as a LaTeX span in the live UI."""
    model = ScriptedModel({"action": "sql", "sql": SQL, "assumptions": ["Costs $1 or more."]},
                          {"answer": "Barclays earned $63,472.00 and Harborview $10,680.00."})
    at = ask(start(model, db_path), "Revenue by venue?")
    assert any(r"\$63,472.00 and Harborview \$10,680.00" in m.value for m in at.markdown)
    assert any(r"\$1 or more" in m.value for m in at.markdown)
