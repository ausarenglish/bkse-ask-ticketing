"""CSV export and table presentation: plain tests, no Streamlit and no model."""

import csv
import io
from types import SimpleNamespace as NS

import pytest

from ask_ticketing.export import CsvExporter, Exporter, ExportFile, escape_text, export_result, filename_stem
from ask_ticketing.presentation import aggregate_note, column_label, column_labels


def parse(export: ExportFile) -> list[list[str]]:
    text = export.data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))


def test_exporter_interface_returns_bytes_filename_and_mime_type():
    exporter: Exporter = CsvExporter()
    out = exporter.export(["a"], [[1]], name="stem")
    assert isinstance(out.data, bytes) and out.filename == "stem.csv" and out.mime_type == "text/csv"


def test_rows_order_and_units_are_kept_exactly():
    columns = ["name", "event_date", "tickets_sold", "revenue_cents"]
    rows = [["Indie Showcase", "2026-09-18", 0, 0], ["Nets vs. Celtics", "2026-09-05", 101, 1234567]]
    out = CsvExporter().export(columns, rows, name="x")
    assert parse(out) == [columns, ["Indie Showcase", "2026-09-18", "0", "0"], ["Nets vs. Celtics", "2026-09-05", "101", "1234567"]]
    assert b"$" not in out.data  # cents stay cents, never relabelled or converted to dollars


def test_quoting_crlf_bom_and_unicode():
    out = CsvExporter().export(["text"], [['comma, "quote"\nline']], name="x")
    assert out.data.startswith("﻿".encode())  # UTF-8 BOM for spreadsheet apps
    assert out.data.decode("utf-8-sig") == 'text\r\n"comma, ""quote""\nline"\r\n'
    unicode = CsvExporter().export(["name"], [["Café Ünïcødé – 東京 🎟"]], name="x")
    assert parse(unicode) == [["name"], ["Café Ünïcødé – 東京 🎟"]]


def test_nulls_are_empty_fields_and_numbers_are_never_escaped():
    out = CsvExporter().export(["a", "b", "c", "d"], [[None, -5, 12.5, 0]], name="x")
    assert parse(out) == [["a", "b", "c", "d"], ["", "-5", "12.5", "0"]]


@pytest.mark.parametrize("text", ["=SUM(A1:A9)", "+1", "-cmd", "@risk", "\tx", "\rx"])
def test_text_that_looks_like_a_formula_is_neutralized(text):
    out = CsvExporter().export(["note"], [[text]], name="x")
    assert parse(out)[1][0] == "'" + text
    assert escape_text("normal text") == "normal text"


def test_headers_are_escaped_too():
    assert parse(CsvExporter().export(["=evil", "ok"], [[1, 2]], name="x"))[0] == ["'=evil", "ok"]


def test_filename_comes_from_the_results_question_and_marks_partial_exports():
    assert filename_stem("Which events earned the most in Sept 2026?", False) == "ask-ticketing-which-events-earned-the-most-in-sept-2026"
    assert filename_stem("???", True) == "ask-ticketing-result-partial"
    assert len(filename_stem("x" * 500, False)) <= len("ask-ticketing-") + 50


def result(status="answered", rows=None, truncated=False, question="Q?"):
    return NS(status=status, question=question, columns=["n"], rows=[[1]] if rows is None else rows, truncated=truncated)


@pytest.mark.parametrize("r", [result("clarify", rows=[]), result("unsupported", rows=[]), result("error", rows=[]), result(rows=[])])
def test_no_export_without_a_successful_tabular_result(r):
    assert export_result(r) is None


def test_export_result_uses_the_stored_result_only():
    out = export_result(result(rows=[[3], [1], [2]], truncated=True, question="Top events?"))
    assert parse(out) == [["n"], ["3"], ["1"], ["2"]] and out.filename == "ask-ticketing-top-events-partial.csv"


# --- Readable on-screen labels and the aggregate note ---------------------------------------

def test_column_labels_are_readable_with_explicit_units():
    assert column_label("revenue_cents") == "Revenue (USD)"
    assert column_label("avg_ticket_price_usd") == "Avg ticket price (USD)"
    assert column_label("event_date") == "Event date" and column_label("matching_events") == "Matching events"
    assert column_labels(["revenue_cents", "revenue_usd"]) == ["Revenue (USD)", "Revenue (USD) [revenue_usd]"]


def test_aggregate_note_only_for_single_totals_without_event_rows():
    assert "2 matching event(s)" in aggregate_note(["tickets_sold", "matching_events"], [[269, 2]])
    assert aggregate_note(["tickets_sold"], [[4793]]) == "This is a single total. The result has no per-event rows."
    assert aggregate_note(["name", "tickets_sold"], [["Indie Showcase", 0]]) is None  # already event-level
    assert aggregate_note(["venue", "revenue_cents"], [["A", 1], ["B", 2]]) is None  # several rows


def test_markdown_literal_escapes_text_but_keeps_numbers_and_nulls():
    from ask_ticketing.presentation import markdown_literal

    assert markdown_literal("$22,397.00") == r"\$22\,397\.00"
    assert markdown_literal("a*b_c") == r"a\*b\_c"
    assert markdown_literal("line1\nline2") == "line1 line2"
    assert markdown_literal(None) == "" and markdown_literal(12) == 12 and markdown_literal(3.5) == 3.5
