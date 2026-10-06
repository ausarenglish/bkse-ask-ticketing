"""Export a query result as a downloadable file. Independent of Streamlit.

`Exporter` is the small interface: columns + rows in, file bytes + filename + MIME type out.
`CsvExporter` is the only implementation.

CSV rules (also documented in README):
- Headers are the column names the query returned, unchanged, so units stay explicit
  (`revenue_cents` holds whole US cents; it is never relabelled or converted to dollars).
- Rows are exported exactly as retained by the result, in their returned order.
- Quoting follows RFC 4180 (Python's csv module): fields containing a comma, quote or line
  break are quoted, and quotes are doubled. Lines end with CRLF.
- Encoding is UTF-8 with a byte-order mark, so spreadsheet apps detect Unicode correctly.
- NULL is written as an empty field.
- Text cells (and headers) that start with =, +, -, @, tab or carriage return get a leading
  apostrophe (') so spreadsheet apps don't run them as formulas. Numbers are written as
  numbers and are never altered.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportFile:
    data: bytes
    filename: str
    mime_type: str


class Exporter(Protocol):
    def export(self, columns: Sequence[str], rows: Sequence[Sequence[Any]], *, name: str) -> ExportFile:
        """Serialize the table. `name` is a filename stem (no extension)."""
        ...


def escape_text(text: str) -> str:
    """Neutralize spreadsheet formulas in a text cell."""
    return "'" + text if text.startswith(FORMULA_TRIGGERS) else text


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):  # not produced by SQLite, but keep it unambiguous
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value  # csv writes repr-exact numbers; never escaped
    if isinstance(value, bytes):
        return value.hex()
    return escape_text(str(value))


class CsvExporter:
    mime_type = "text/csv"
    extension = "csv"

    def export(self, columns: Sequence[str], rows: Sequence[Sequence[Any]], *, name: str) -> ExportFile:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([escape_text(str(c)) for c in columns])
        for row in rows:
            writer.writerow([_cell(v) for v in row])
        return ExportFile(buffer.getvalue().encode("utf-8-sig"), f"{name}.{self.extension}", self.mime_type)


def filename_stem(question: str, truncated: bool) -> str:
    """A safe, recognisable filename stem from the question that produced the result."""
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:50].strip("-") or "result"
    return f"ask-ticketing-{slug}" + ("-partial" if truncated else "")


def export_result(result: Any, exporter: Exporter | None = None) -> ExportFile | None:
    """The export for a displayed AskResult, or None when there is nothing tabular to export
    (clarification, unsupported, error, or an answered result with no rows). Uses only the
    stored result: it never calls the model or runs SQL."""
    if getattr(result, "status", None) != "answered" or not getattr(result, "rows", None):
        return None
    exporter = exporter or CsvExporter()
    return exporter.export(result.columns, result.rows, name=filename_stem(result.question, result.truncated))
