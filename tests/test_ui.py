"""UI tests with a scripted model. They check the interface, not live NL-to-SQL accuracy."""

from pathlib import Path

import pytest
from fakes import ScriptedModel
from streamlit.testing.v1 import AppTest

from ask_ticketing.model import ModelAccessError, ModelUnavailableError

APP = str(Path(__file__).parents[1] / "src" / "ask_ticketing" / "app.py")


def shown(table):
    """The supporting table as a reader sees it: st.table renders Markdown, so the app escapes
    punctuation with backslashes; undo that to compare displayed text."""
    import re

    unescape = lambda v: re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", v) if isinstance(v, str) else v
    return table.rename(columns=unescape).map(unescape)
SQL = "SELECT COUNT(*) AS tickets_sold, SUM(price_cents) AS revenue_cents FROM tickets WHERE status = 'sold' AND event_id = 1"


def start(model, db_path):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_model_override"] = model
    at.session_state["_db_override"] = str(db_path)
    return at.run()


def ask(at, question):
    at.text_area(key="question").input(question)
    at.button(key="ask").click()
    return at.run()


def test_initial_layout_and_examples_do_not_call_the_model(db_path):
    model = ScriptedModel()
    at = start(model, db_path)
    assert at.header[0].value == "Ask Ticketing"
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
    assert not at.dataframe  # no interactive grid, so no built-in toolbar download
    table = shown(at.table[0].value)
    assert list(table.columns) == ["Tickets sold", "Revenue (USD)"] and str(table.iloc[0, 1]).startswith("$")
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
    assert not at.table and not at.code


def test_empty_result_state(db_path):
    model = ScriptedModel({"action": "sql", "sql": "SELECT name FROM events WHERE event_date = '1999-01-01'", "assumptions": []})
    at = ask(start(model, db_path), "Events in 1999?")
    assert any("No rows matched" in i.value for i in at.info) and not at.table
    assert len(model.calls) == 1


def test_truncation_notice(db_path):
    model = ScriptedModel({"action": "sql", "sql": "SELECT ticket_id FROM tickets ORDER BY ticket_id", "assumptions": []}, {"answer": "Tickets listed."})
    at = ask(start(model, db_path), "List all tickets")
    assert any("Showing only the first 200 rows" in w.value for w in at.warning)
    assert len(at.table[0].value) == 200


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
    assert at.header[0].value == "Ask Ticketing"  # the page still starts
    assert "Model provider setup needed" in at.error[0].value and needle in at.error[0].value
    assert "Choose your provider" in at.error[0].value
    assert at.button(key="ask").disabled
    at.button(key="example_0").click().run()  # examples still fill the box
    assert at.text_area(key="question").value.startswith("How many tickets") and not at.exception


def test_ask_is_enabled_when_the_provider_is_configured(db_path):
    at = start(ScriptedModel(), db_path)
    assert not at.button(key="ask").disabled and not at.error


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


# --- Saved questions -------------------------------------------------------------------------
# A ScriptedModel with no steps raises if it is ever called, so `model.calls == []` proves that
# saving, selecting and removing never reach the model.

import hashlib
import json


def saved_key(prefix, text):
    return f"{prefix}_{hashlib.sha1(text.encode()).hexdigest()[:12]}"


SAVE_LABEL = "Save question"
SAVED_STATUS = "Already in saved questions"


def captions(at):
    return [c.value for c in at.caption]


def sidebar_questions(at):
    """Saved-question texts as a reader sees them in the sidebar (escaping undone)."""
    import re

    texts = [m.value for m in at.sidebar.markdown]
    return [re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", t) for t in texts
            if t not in ("", "**Saved questions**") and not t.startswith("This synthetic dataset covers")]


def type_question(at, text):
    at.text_area(key="question").input(text)
    return at.run()


def test_save_gives_immediate_feedback_and_never_calls_the_model(db_path, isolated_saved_questions):
    model = ScriptedModel()
    at = start(model, db_path)
    # Save stays clickable (a disabled button would swallow a click made right after typing),
    # but blank input is rejected with a message and nothing is written.
    assert not at.button(key="save_question").disabled
    at.button(key="save_question").click().run()
    assert any("Type a question before saving it." in w.value for w in at.warning)
    at = type_question(at, "   ")
    at.button(key="save_question").click().run()
    assert any("Type a question before saving it." in w.value for w in at.warning)
    assert not isolated_saved_questions.exists()
    at = type_question(at, "Which venue had the most refunds?")
    at.button(key="save_question").click().run()
    assert at.button(key="save_question").label == SAVE_LABEL  # the action never turns into a status
    assert "✓ Question saved" in captions(at)  # one-time confirmation, separate from the button
    assert json.loads(isolated_saved_questions.read_text())["questions"] == ["Which venue had the most refunds?"]
    assert "Which venue had the most refunds?" in sidebar_questions(at)
    assert at.text_area(key="question").value == "Which venue had the most refunds?"  # not cleared
    at.run()  # any later rerun: the confirmation is gone, the quiet membership status remains
    assert "✓ Question saved" not in captions(at) and SAVED_STATUS in captions(at)
    assert at.button(key="save_question").label == SAVE_LABEL
    assert model.calls == [] and not at.exception


def test_editing_the_text_clears_the_saved_indicator(db_path):
    at = type_question(start(ScriptedModel(), db_path), "Revenue by venue in 2026?")
    at.button(key="save_question").click().run()
    at = type_question(at, "Revenue by venue in 2025?")
    save = at.button(key="save_question")
    assert save.label == SAVE_LABEL and not save.disabled
    assert not any("saved" in c.lower() for c in captions(at))  # no status for unsaved text
    at = type_question(at, "Revenue by venue in 2026?")  # back to the saved text
    assert SAVED_STATUS in captions(at) and at.button(key="save_question").label == SAVE_LABEL


def test_duplicates_are_not_added(db_path, isolated_saved_questions):
    isolated_saved_questions.write_text(json.dumps({"version": 1, "questions": ["Top events by revenue"]}))
    at = type_question(start(ScriptedModel(), db_path), "  top EVENTS by   revenue ")
    assert SAVED_STATUS in captions(at) and at.button(key="save_question").label == SAVE_LABEL  # recognised
    at.button(key="save_question").click().run()  # clicking again is harmless
    assert "Already in saved questions. Not added again." in captions(at)
    # Stored wording is the user's original text; the duplicate check never rewrites it.
    assert json.loads(isolated_saved_questions.read_text())["questions"] == ["Top events by revenue"]


def test_selecting_fills_the_input_without_submitting_and_keeps_the_last_result(db_path, isolated_saved_questions):
    saved = "How many tickets were sold for Indie Showcase?"
    isolated_saved_questions.write_text(json.dumps({"version": 1, "questions": [saved]}))
    model = ScriptedModel({"action": "sql", "sql": SQL, "assumptions": []}, {"answer": "Event 1 sold some tickets."})
    at = ask(start(model, db_path), "How did event 1 do?")
    assert len(model.calls) == 2
    at.button(key=saved_key("saved_use", saved)).click().run()
    assert at.text_area(key="question").value == saved
    assert SAVED_STATUS in captions(at)  # membership status...
    assert "✓ Question saved" not in captions(at)  # ...without pretending a new save happened
    assert len(model.calls) == 2  # selecting did not ask
    # The previous result stays visible and is still labelled with the question it answered.
    assert any("Question: How did event 1 do?" in c.value for c in at.caption)


def test_remove_and_persistence_across_restarts(db_path, isolated_saved_questions):
    model = ScriptedModel()
    at = type_question(start(model, db_path), "First saved question")
    at.button(key="save_question").click().run()
    at = type_question(at, "Second saved question")
    at.button(key="save_question").click().run()

    restarted = start(ScriptedModel(), db_path)  # a new session reads the same file
    shown = sidebar_questions(restarted)
    assert shown.index("Second saved question") < shown.index("First saved question")  # newest first

    restarted.button(key=saved_key("saved_remove", "First saved question")).click().run()
    assert json.loads(isolated_saved_questions.read_text())["questions"] == ["Second saved question"]
    assert "First saved question" not in sidebar_questions(restarted)
    assert model.calls == []


def test_saving_and_selecting_work_without_model_credentials(db_path, monkeypatch, tmp_path, isolated_saved_questions):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "ASK_TICKETING_PROVIDER", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_db_override"] = str(db_path)
    at = type_question(at.run(), "Liberty home games in August 2026?")
    assert at.button(key="ask").disabled and "Model provider setup needed" in at.error[0].value
    at.button(key="save_question").click().run()
    assert "✓ Question saved" in captions(at)
    at = type_question(at, "")
    at.button(key=saved_key("saved_use", "Liberty home games in August 2026?")).click().run()
    assert at.text_area(key="question").value == "Liberty home games in August 2026?" and not at.exception


def test_malformed_storage_is_reported_and_left_untouched(db_path, isolated_saved_questions):
    isolated_saved_questions.write_text("{broken")
    at = type_question(start(ScriptedModel(), db_path), "A question")
    assert any("Saved questions unavailable" in w.value and "left unchanged" in w.value for w in at.warning)
    assert at.button(key="save_question").disabled  # can't overwrite the file
    assert isolated_saved_questions.read_text() == "{broken" and not at.exception


def test_unwritable_storage_shows_a_warning_instead_of_failing(db_path, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_model_override"] = ScriptedModel()
    at.session_state["_db_override"] = str(db_path)
    at.session_state["_saved_path_override"] = str(blocker / "saved.json")
    at = type_question(at.run(), "A question")
    at.button(key="save_question").click().run()
    assert any("couldn't be written" in w.value for w in at.warning)
    assert at.button(key="save_question").label == "Save question" and not at.exception


# --- Input preservation, supporting context and CSV export -----------------------------------

from ask_ticketing import workflow
from ask_ticketing.export import export_result
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage

EVENTS_SQL = ("SELECT e.name, e.event_date, COUNT(t.ticket_id) AS tickets_sold, COALESCE(SUM(t.price_cents), 0) AS revenue_cents "
              "FROM events e LEFT JOIN tickets t ON t.event_id = e.event_id AND t.status = 'sold' "
              "WHERE e.event_date BETWEEN '2026-09-01' AND '2026-09-30' GROUP BY e.event_id ORDER BY revenue_cents DESC, e.event_id")


@pytest.fixture
def sql_runs(monkeypatch):
    """Counts SQL executions through the workflow's only execution entry point."""
    calls = []
    real = workflow.run_query

    def counting(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs.get("sql"))
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, "run_query", counting)
    return calls


@pytest.fixture
def downloads(monkeypatch):
    """Captures the exact bytes, MIME type and filename Streamlit would serve for a download."""
    captured = []
    real = MemoryMediaFileStorage.load_and_get_id

    def spy(self, path_or_data, mimetype, kind, filename=None):
        captured.append({"data": path_or_data, "mime": mimetype, "filename": filename})
        return real(self, path_or_data, mimetype, kind, filename)

    monkeypatch.setattr(MemoryMediaFileStorage, "load_and_get_id", spy)
    return captured


def test_first_example_no_longer_adds_the_refund_clause(db_path):
    at = start(ScriptedModel(), db_path)
    at.button(key="example_0").click().run()
    assert at.text_area(key="question").value == "How many tickets were sold for Brooklyn Nets home games with an event date in September 2026?"


def test_ask_never_rewrites_the_input_and_assumptions_stay_separate(db_path):
    typed = "  Which events in September 2026 earned the most?  Keep my wording.  "
    model = ScriptedModel({"action": "sql", "sql": EVENTS_SQL, "assumptions": ["Refunded tickets are excluded (status = 'sold')."]},
                          {"answer": "Scripted summary."})
    at = ask(start(model, db_path), typed)
    assert at.text_area(key="question").value == typed  # exactly as typed, spacing included
    assert "Question: Which events in September 2026 earned the most?  Keep my wording." in [c.value for c in at.caption]
    assert any(m.value == "**Assumptions**" for m in at.markdown)
    assert any("Refunded tickets are excluded" in m.value for m in at.markdown)
    assert "Refunded" not in at.text_area(key="question").value


def test_event_context_is_readable_and_csv_matches_the_returned_rows(db_path, sql_runs, downloads):
    model = ScriptedModel({"action": "sql", "sql": EVENTS_SQL, "assumptions": []}, {"answer": "Scripted summary."})
    at = ask(start(model, db_path), "Which events in September 2026 earned the most?")
    table = shown(at.table[0].value)
    assert list(table.columns) == ["Name", "Event date", "Tickets sold", "Revenue (USD)"]
    assert len(at.get("download_button")) == 1  # Download CSV is the only export control
    result = at.session_state["result"]
    assert list(table["Name"]) == [r[0] for r in result.rows]  # same rows, same order
    assert not any("no per-event rows" in c.value for c in at.caption)  # event-level, so no aggregate note
    csv_download = downloads[-1]
    assert csv_download["mime"] == "text/csv" and csv_download["filename"] == "ask-ticketing-which-events-in-september-2026-earned-the-most.csv"
    assert csv_download["data"] == export_result(result).data
    text = csv_download["data"].decode("utf-8-sig").splitlines()
    assert text[0] == "name,event_date,tickets_sold,revenue_cents" and len(text) == len(result.rows) + 1
    assert at.download_button(key="download_csv").proto.ignore_rerun  # clicking doesn't even rerun the script
    assert len(model.calls) == 2 and len(sql_runs) == 1


def test_aggregate_without_event_rows_says_so(db_path):
    sql = ("SELECT COUNT(DISTINCT e.event_id) AS matching_events, COUNT(t.ticket_id) AS tickets_sold FROM events e "
           "LEFT JOIN tickets t ON t.event_id = e.event_id AND t.status = 'sold' WHERE e.home_team = 'Brooklyn Nets' "
           "AND e.event_date BETWEEN '2026-09-01' AND '2026-09-30'")
    model = ScriptedModel({"action": "sql", "sql": sql, "assumptions": [], "event_filtered_total": True}, {"answer": "269 tickets."})
    at = ask(start(model, db_path), "Nets tickets in September 2026?")
    assert any("total across 2 matching event(s)" in c.value and "no per-event rows" in c.value for c in at.caption)


def test_edited_input_cannot_relabel_or_replace_the_previous_export(db_path, sql_runs, downloads):
    model = ScriptedModel({"action": "sql", "sql": EVENTS_SQL, "assumptions": []}, {"answer": "Scripted summary."})
    at = ask(start(model, db_path), "Which events earned the most?")
    first = downloads[-1]
    at = type_question(at, "A completely different question I have not asked")
    at.button(key="save_question").click().run()  # other interactions rerun the page
    latest = downloads[-1]
    assert latest["filename"] == first["filename"] == "ask-ticketing-which-events-earned-the-most.csv"
    assert latest["data"] == first["data"]
    assert any('Download CSV exports the' in c and "Which events earned the most?" in c for c in captions(at))
    assert "Question: Which events earned the most?" in captions(at)  # the answer keeps its own question
    assert len(model.calls) == 2 and len(sql_runs) == 1  # no model call or SQL from re-rendering the download


def test_truncated_results_disclose_a_partial_download(db_path, downloads):
    model = ScriptedModel({"action": "sql", "sql": "SELECT ticket_id FROM tickets ORDER BY ticket_id", "assumptions": []}, {"answer": "Tickets listed."})
    at = ask(start(model, db_path), "List every ticket")
    assert any("Showing only the first 200 rows" in w.value and "The download is partial too" in w.value for w in at.warning)
    assert downloads[-1]["filename"].endswith("-partial.csv")
    assert len(downloads[-1]["data"].decode("utf-8-sig").splitlines()) == 201


@pytest.mark.parametrize("steps, question", [
    (({"action": "clarify", "message": "Purchase or event date?", "assumptions": []},), "Sales last month?"),
    (({"action": "unsupported", "message": "No customer data.", "assumptions": []},), "Top customers?"),
    ((ModelAccessError("refused"),), "Anything?"),
    (({"action": "sql", "sql": "SELECT name FROM events WHERE event_date = '1999-01-01'", "assumptions": []},), "Events in 1999?"),
])
def test_no_download_for_non_tabular_results(db_path, steps, question):
    at = ask(start(ScriptedModel(*steps), db_path), question)
    assert not at.get("download_button")


def test_table_text_is_shown_literally_not_as_markdown(db_path):
    """Event names come from data; Markdown, HTML or $...$ in them must display as plain text."""
    sql = "SELECT '**bold** [link](http://x) <b>h</b> $1 and $2 | x' AS name, 5 AS tickets_sold"
    at = ask(start(ScriptedModel({"action": "sql", "sql": sql, "assumptions": []}, {"answer": "One row."}), db_path), "Odd text?")
    raw = at.table[0].value.iloc[0, 0]
    assert raw == r"\*\*bold\*\* \[link\]\(http\:\/\/x\) \<b\>h\<\/b\> \$1 and \$2 \| x"
    assert shown(at.table[0].value).iloc[0, 0] == "**bold** [link](http://x) <b>h</b> $1 and $2 | x"


# --- Clear (start fresh) ---------------------------------------------------------------------

def clear(at):
    at.button(key="clear").click()
    return at.run()


def test_clear_button_is_visible_with_help_text(db_path):
    button = start(ScriptedModel(), db_path).button(key="clear")
    assert button.label == "× Clear" and not button.disabled
    assert button.help == "Clear this question and its result. Saved questions are kept."


def test_clear_before_asking_empties_the_input_without_calling_anything(db_path, sql_runs):
    model = ScriptedModel()
    at = clear(type_question(start(model, db_path), "Half-typed question"))
    assert at.text_area(key="question").value == ""
    assert model.calls == [] and sql_runs == [] and not at.exception


def test_save_clear_select_keeps_the_saved_question_and_refills_it(db_path, sql_runs, isolated_saved_questions):
    model = ScriptedModel()
    at = type_question(start(model, db_path), "Which venue had the most refunds?")
    at.button(key="save_question").click().run()
    assert at.text_area(key="question").value == "Which venue had the most refunds?"  # Save doesn't clear
    stored = isolated_saved_questions.read_bytes()
    at = clear(at)
    assert at.text_area(key="question").value == ""
    assert not any("saved" in c.lower() for c in captions(at)) and at.button(key="save_question").label == SAVE_LABEL
    assert isolated_saved_questions.read_bytes() == stored  # storage untouched
    assert sidebar_questions(at) == ["Which venue had the most refunds?"]
    at.button(key=saved_key("saved_use", "Which venue had the most refunds?")).click().run()
    assert at.text_area(key="question").value == "Which venue had the most refunds?"
    assert SAVED_STATUS in captions(at) and at.button(key="save_question").label == SAVE_LABEL
    assert model.calls == [] and sql_runs == []


def test_clear_after_an_answer_removes_result_table_sql_and_download(db_path, sql_runs, downloads):
    model = ScriptedModel({"action": "sql", "sql": EVENTS_SQL, "assumptions": ["Event-date basis."]}, {"answer": "Scripted summary."})
    at = ask(start(model, db_path), "Which events earned the most?")
    assert at.table and at.get("download_button") and at.code
    at = clear(at)
    assert at.text_area(key="question").value == ""
    assert not at.table and not at.get("download_button") and not at.code
    assert not any("Question:" in c.value for c in at.caption)
    assert not any("Scripted summary." in m.value or "Event-date basis." in m.value for m in at.markdown)
    assert "result" not in at.session_state
    assert len(model.calls) == 2 and len(sql_runs) == 1  # nothing re-ran


def test_clear_after_an_error_result(db_path):
    at = ask(start(ScriptedModel(ModelUnavailableError("busy", retryable=True)), db_path), "Anything?")
    assert any("currently unavailable" in e.value for e in at.error)
    at = clear(at)
    assert not at.error and at.text_area(key="question").value == ""


def test_clear_works_without_credentials_and_keeps_setup_guidance(db_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "ASK_TICKETING_PROVIDER", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_db_override"] = str(db_path)
    at = clear(type_question(at.run(), "Liberty home games in August 2026?"))
    assert at.text_area(key="question").value == ""
    assert "Model provider setup needed" in at.error[0].value and at.button(key="ask").disabled
    assert not at.exception


def test_clear_keeps_storage_warnings_and_never_touches_a_malformed_file(db_path, isolated_saved_questions):
    isolated_saved_questions.write_text("{broken")
    at = clear(type_question(start(ScriptedModel(), db_path), "A question"))
    assert any("Saved questions unavailable" in w.value for w in at.warning)
    assert isolated_saved_questions.read_text() == "{broken"


# --- Desktop layout pass: examples, full text, separate save status --------------------------

from test_examples_data import UPCOMING_SQL, VENUE_REVENUE_SQL

EXAMPLE_TEXTS = {
    0: "How many tickets were sold for Brooklyn Nets home games with an event date in September 2026?",
    1: ("For each event after October 1, 2026, show its name, event date, venue, sold ticket count, remaining capacity, "
        "sell-through percentage, and average sold-ticket price. Exclude refunded tickets from sales and price calculations. "
        "Include events with no sales, showing their average sold-ticket price as unavailable. Order by event date, then event ID."),
    2: ("For events dated August 1 through August 31, 2026, show ticket revenue by venue, excluding refunded tickets. "
        "Order by revenue descending, then venue name."),
}


def test_compact_examples_fill_the_exact_question_without_calling_anything(db_path, sql_runs):
    model = ScriptedModel()
    at = start(model, db_path)
    assert [at.button(key=f"example_{i}").label for i in range(3)] == ["Nets ticket sales", "Upcoming event overview", "Revenue by venue"]
    for i, text in EXAMPLE_TEXTS.items():
        at.button(key=f"example_{i}").click().run()
        assert at.text_area(key="question").value == text  # full wording, not the label
    assert model.calls == [] and sql_runs == [] and "result" not in at.session_state


def test_what_can_i_ask_is_a_popover_beside_the_input_not_in_the_sidebar(db_path):
    at = start(ScriptedModel(), db_path)
    popovers = at.main.get("popover")
    assert len(popovers) == 1 and popovers[0].proto.popover.label == "What can I ask?"
    assert not at.sidebar.get("popover") and not any("What can I ask" in e.label for e in at.sidebar.expander)
    text = popovers[0].get("markdown")[0].value
    for topic in ("events, dates, venues and categories", "ticket prices and revenue", "sold and refunded ticket counts",
                  "capacity, remaining inventory and sell-through", "sales by purchase date or event date"):
        assert topic in text
    assert "It has no ticket types, VIP tiers, seat sections, customers or sales channels." in text


def test_empty_saved_state_is_concise(db_path):
    assert "No saved questions yet. Type a question and choose Save question." in [c.value for c in start(ScriptedModel(), db_path).sidebar.caption]


def test_typing_then_immediately_saving_works_on_the_first_click(db_path, isolated_saved_questions):
    at = start(ScriptedModel(), db_path)
    at.text_area(key="question").input("Typed and saved in one go")
    at.button(key="save_question").click()
    at.run()  # one rerun carries both the typed text and the click, as in the browser
    assert json.loads(isolated_saved_questions.read_text())["questions"] == ["Typed and saved in one go"]
    assert "✓ Question saved" in captions(at)


def test_long_questions_stay_complete_in_sidebar_input_and_answer_caption(db_path, isolated_saved_questions):
    long_question = EXAMPLE_TEXTS[1]
    model = ScriptedModel({"action": "sql", "sql": UPCOMING_SQL, "assumptions": ["Refunds excluded."]}, {"answer": "Ten upcoming events."})
    at = type_question(start(model, db_path), long_question)
    at.button(key="save_question").click().run()
    at = ask(at, long_question)
    assert sidebar_questions(at) == [long_question]  # full saved text, no truncation
    assert at.text_area(key="question").value == long_question
    assert f"Question: {long_question}" in captions(at)


def test_upcoming_overview_keeps_null_average_distinct_from_zero(db_path, downloads, sql_runs):
    model = ScriptedModel({"action": "sql", "sql": UPCOMING_SQL, "assumptions": []}, {"answer": "Ten upcoming events."})
    at = ask(start(model, db_path), EXAMPLE_TEXTS[1])
    table = shown(at.table[0].value)
    assert list(table.columns) == ["Name", "Event date", "Venue", "Tickets sold", "Remaining capacity", "Sell through pct",
                                   "Avg ticket price (USD)"]
    no_sales = table[table["Tickets sold"] == 0]
    assert len(no_sales) == 1 and no_sales.iloc[0]["Avg ticket price (USD)"] == ""  # unavailable, not $0.00
    csv_rows = downloads[-1]["data"].decode("utf-8-sig").splitlines()
    assert csv_rows[0] == "name,event_date,venue,tickets_sold,remaining_capacity,sell_through_pct,avg_ticket_price_cents"
    assert sum(1 for r in csv_rows[1:] if r.endswith(",")) == 1  # the null average is an empty CSV field
    assert len(at.get("download_button")) == 1 and len(sql_runs) == 1


def test_revenue_by_venue_renders_dollars_and_exports_cents(db_path, downloads):
    model = ScriptedModel({"action": "sql", "sql": VENUE_REVENUE_SQL, "assumptions": []}, {"answer": "Barclays led."})
    at = ask(start(model, db_path), EXAMPLE_TEXTS[2])
    table = shown(at.table[0].value)
    assert table.values.tolist() == [["Barclays Center", "$63,472.00"], ["Harborview Arena", "$10,680.00"]]
    assert downloads[-1]["data"].decode("utf-8-sig").splitlines()[1:] == ["Barclays Center,6347200", "Harborview Arena,1068000"]


# --- Compact saved-question entries ------------------------------------------------------------

LONG_SAVED = [
    "For each event after October 1, 2026, show its name, event date, venue, sold ticket count, remaining capacity, "
    "sell-through percentage, and average sold-ticket price. Exclude refunded tickets.",
    "For events dated August 1 through August 31, 2026, show ticket revenue by venue, excluding refunded tickets. "
    "Order by revenue descending, then venue name.",
    "Short one?",
]


def test_saved_questions_are_collapsed_previews_that_expand_to_the_exact_text(db_path, isolated_saved_questions, sql_runs):
    raw = json.dumps({"version": 1, "questions": LONG_SAVED}, indent=2) + "\n"
    isolated_saved_questions.write_text(raw)
    model = ScriptedModel()
    at = start(model, db_path)
    entries = at.sidebar.expander
    assert len(entries) == 3 and all(not e.proto.expanded for e in entries)  # collapsed by default
    labels = [e.label.replace("\\", "") for e in entries]
    assert labels[0] == "Short one?"  # newest first; short text is not shortened
    assert labels[1].endswith("…") and len(labels[1]) <= 61 and LONG_SAVED[1].startswith(labels[1][:-1])
    assert labels[2].endswith("…") and LONG_SAVED[0].startswith(labels[2][:-1])
    assert sidebar_questions(at) == list(reversed(LONG_SAVED))  # full original wording inside each entry
    at.button(key=saved_key("saved_use", LONG_SAVED[0])).click().run()
    assert at.text_area(key="question").value == LONG_SAVED[0]  # complete text loaded, not the preview
    assert isolated_saved_questions.read_text() == raw  # preview shortening never touched storage
    assert model.calls == [] and sql_runs == []


def test_save_clear_remove_still_work_with_collapsed_entries(db_path, isolated_saved_questions, sql_runs):
    model = ScriptedModel()
    at = start(model, db_path)
    at.text_area(key="question").input(LONG_SAVED[1])
    at.button(key="save_question").click()
    at.run()  # immediate save
    assert "✓ Question saved" in captions(at) and at.button(key="save_question").label == SAVE_LABEL
    at = clear(at)
    assert at.text_area(key="question").value == "" and len(at.sidebar.expander) == 1
    at.button(key=saved_key("saved_remove", LONG_SAVED[1])).click().run()
    assert json.loads(isolated_saved_questions.read_text())["questions"] == [] and not at.sidebar.expander
    assert model.calls == [] and sql_runs == []
