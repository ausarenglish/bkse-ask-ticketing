from fakes import ScriptedModel

from ask_ticketing.model import ModelAccessError, ModelAuthError, ModelOutputError
from ask_ticketing.workflow import ask

COUNT_SQL = "SELECT COUNT(*) AS tickets_sold FROM tickets WHERE status = 'sold'"


def sql_step(sql, assumptions=("assumption",)):
    return {"action": "sql", "sql": sql, "assumptions": list(assumptions)}


def summary(text="Summary."):
    return {"answer": text}


def test_successful_question_runs_sql_and_summarizes(db_path, dataset):
    model = ScriptedModel(sql_step(COUNT_SQL, ["Counts non-refunded tickets."]), summary("4,793 tickets were sold."))
    r = ask("How many tickets were sold overall?", model, db_path)
    assert r.status == "answered"
    assert r.sql == COUNT_SQL  # exactly what ran
    assert r.rows == [[sum(t.status == "sold" for t in dataset.tickets)]]
    assert r.answer == "4,793 tickets were sold."
    assert r.assumptions == ["Counts non-refunded tickets."]
    assert model.tools_called == ["decide", "answer"]
    # Summary is grounded in actual rows, with money pre-formatted in Python.
    assert '"rows": [[4793]]' in model.calls[1]["prompt"]


def test_money_is_formatted_before_summary(db_path):
    model = ScriptedModel(sql_step("SELECT SUM(price_cents) AS revenue_cents FROM tickets WHERE status = 'sold' AND event_id = 1"), summary())
    ask("Revenue for event 1?", model, db_path)
    assert '"revenue_usd"' in model.calls[1]["prompt"] and "$" in model.calls[1]["prompt"]


def test_clarification_branch_never_executes_sql(db_path):
    model = ScriptedModel({"action": "clarify", "message": "Purchase date or event date?", "sql": "DELETE FROM tickets", "assumptions": []})
    r = ask("How many tickets did we sell last month?", model, db_path)
    assert (r.status, r.answer, r.sql, r.rows) == ("clarify", "Purchase date or event date?", None, [])
    assert model.tools_called == ["decide"]


def test_unsupported_branch(db_path):
    model = ScriptedModel({"action": "unsupported", "message": "There is no customer data.", "assumptions": []})
    r = ask("Which customers bought the most?", model, db_path)
    assert (r.status, r.answer, r.sql) == ("unsupported", "There is no customer data.", None)


def test_empty_result_is_not_reported_as_zero(db_path):
    model = ScriptedModel(sql_step("SELECT event_id, name FROM events WHERE event_date BETWEEN '2026-01-01' AND '2026-01-02'"))
    r = ask("Events on Jan 1 2026?", model, db_path)
    assert r.status == "answered" and r.rows == [] and r.columns == ["event_id", "name"]
    assert "No rows matched" in r.answer and "not a total of zero" in r.answer
    assert model.tools_called == ["decide"]  # no model summary of an empty result


def test_zero_aggregate_is_a_real_result(db_path):
    model = ScriptedModel(sql_step("SELECT COUNT(*) AS tickets_sold FROM tickets WHERE status = 'sold' AND purchase_date > '2026-10-01'"), summary("Zero tickets."))
    r = ask("Tickets bought after the as-of date?", model, db_path)
    assert r.rows == [[0]] and r.answer == "Zero tickets."
    assert model.tools_called == ["decide", "answer"]


def test_truncation_is_stated(db_path):
    model = ScriptedModel(sql_step("SELECT ticket_id FROM tickets ORDER BY ticket_id"), summary("Here are tickets."))
    r = ask("List tickets", model, db_path, max_rows=3)
    assert r.truncated and len(r.rows) == 3
    assert "Only the first 3 rows are shown" in r.answer
    assert '"result_truncated": true' in model.calls[1]["prompt"]


def test_unsafe_sql_is_rejected_without_repair(db_path):
    model = ScriptedModel(sql_step("DELETE FROM tickets"))
    r = ask("Delete everything", model, db_path)
    assert (r.status, r.error_kind, r.repairs) == ("error", "unsafe_sql", 0)
    assert r.sql == "DELETE FROM tickets" and model.tools_called == ["decide"]


def test_execution_limit_is_reported(db_path):
    runaway = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    r = ask("Count forever", ScriptedModel(sql_step(runaway)), db_path, timeout_s=0.2)
    assert (r.status, r.error_kind) == ("error", "limit")


def test_one_repair_for_invalid_sql(db_path):
    model = ScriptedModel(sql_step("SELECT nope FROM tickets"), sql_step(COUNT_SQL), summary())
    r = ask("How many tickets were sold?", model, db_path)
    assert (r.status, r.repairs, r.sql) == ("answered", 1, COUNT_SQL)
    assert "no such column: nope" in model.calls[1]["prompt"]


def test_invalid_sql_twice_gives_up(db_path):
    model = ScriptedModel(sql_step("SELECT nope FROM tickets"), sql_step("SELECT still_nope FROM tickets"))
    r = ask("q", model, db_path)
    assert (r.status, r.error_kind, r.repairs) == ("error", "invalid_sql", 1)
    assert len(model.calls) == 2


def test_one_repair_for_malformed_output(db_path):
    model = ScriptedModel({"action": "sql", "assumptions": []}, sql_step(COUNT_SQL), summary())  # first lacks sql
    r = ask("q", model, db_path)
    assert (r.status, r.repairs) == ("answered", 1)
    assert "failed validation" in model.calls[1]["prompt"]


def test_malformed_output_twice_gives_up(db_path):
    model = ScriptedModel(ModelOutputError("no tool use"), {"action": "maybe", "assumptions": []})
    r = ask("q", model, db_path)
    assert (r.status, r.error_kind, r.repairs) == ("error", "malformed_output", 1)
    assert len(model.calls) == 2


def test_provider_access_error_is_not_retried(db_path):
    model = ScriptedModel(ModelAccessError("Amazon Bedrock refused access for this account or model."))
    r = ask("q", model, db_path)
    assert (r.status, r.error_kind, r.repairs) == ("error", "access", 0) and len(model.calls) == 1
    assert "refused access" in r.error


def test_expired_credentials_error(db_path):
    r = ask("q", ScriptedModel(ModelAuthError("AWS credentials are missing or expired. Configure AWS credentials (see README).")), db_path)
    assert r.error_kind == "auth" and "Configure AWS credentials" in r.error


def test_summary_failure_still_returns_rows(db_path):
    model = ScriptedModel(sql_step(COUNT_SQL), ModelOutputError("bad summary"))
    r = ask("q", model, db_path)
    assert r.status == "answered" and r.rows and "summary is unavailable" in r.answer


def test_missing_database(tmp_path):
    model = ScriptedModel()
    r = ask("q", model, tmp_path / "missing.db")
    assert (r.status, r.error_kind) == ("error", "database_missing") and model.calls == []


def test_cli_render_shows_answer_sql_rows_and_notice(db_path):
    from ask_ticketing.cli import render

    model = ScriptedModel(sql_step("SELECT e.name, e.capacity * 100 AS cap_cents FROM venues v JOIN events e USING (venue_id) ORDER BY e.event_id LIMIT 2"), summary("Two events."))
    text = render(ask("q", model, db_path))
    assert text.startswith("Synthetic demo data (as of 2026-10-01).")
    assert "Answer: Two events." in text and "SQL executed:" in text and "cap_usd" in text and "Rows (2):" in text
