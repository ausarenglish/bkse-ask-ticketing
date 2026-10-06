"""Readable labels for the on-screen supporting table. UI only: the model's prompts and the CSV
export keep the raw column names, and nothing here adds or infers data."""

from __future__ import annotations

import re
from typing import Any, Sequence

# Columns that identify individual events in a result row (names the schema and our SQL use).
EVENT_COLUMNS = {"event_id", "name", "event_name", "event", "event_date", "date"}


# st.table renders every cell and header as Markdown. Backslash-escaping all ASCII punctuation
# makes CommonMark show each character literally (no emphasis, links, code, HTML, tables or
# $...$ math), so query text is displayed exactly as data.
_MARKDOWN_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")


def markdown_literal(value: Any) -> Any:
    """A display-safe table value: numbers unchanged, NULL as an empty cell, text escaped.
    Line breaks become spaces so a cell can't break the table layout (the CSV keeps them)."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = " ".join(str(value).splitlines())
    return _MARKDOWN_PUNCTUATION.sub(r"\\\1", text)


def column_label(column: str) -> str:
    """'revenue_cents' -> 'Revenue (USD)' (the table shows those values as dollars),
    'event_date' -> 'Event date', 'matching_events' -> 'Matching events'."""
    base, unit = column, ""
    for suffix in ("_cents", "_usd"):
        if column.endswith(suffix):
            base, unit = column[: -len(suffix)], " (USD)"
            break
    words = base.replace("_", " ").strip()
    return (words[:1].upper() + words[1:] if words else column) + unit


def column_labels(columns: Sequence[str]) -> list[str]:
    """Labels for display, kept unique (falls back to the raw name on a clash)."""
    labels: list[str] = []
    for column in columns:
        label = column_label(column)
        labels.append(label if label not in labels else f"{label} [{column}]")
    return labels


def has_event_rows(columns: Sequence[str]) -> bool:
    return any(c.lower() in EVENT_COLUMNS for c in columns)


def aggregate_note(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str | None:
    """A note for single-row totals that contain no event-level rows, so the table isn't read
    as an event breakdown. None when the result already lists events or has several rows."""
    if len(rows) != 1 or has_event_rows(columns):
        return None
    lower = [c.lower() for c in columns]
    if "matching_events" in lower:
        count = rows[0][lower.index("matching_events")]
        return (f"This is a total across {count} matching event(s). The result has no per-event rows; "
                "ask for a breakdown by event to see them.")
    return "This is a single total. The result has no per-event rows."
