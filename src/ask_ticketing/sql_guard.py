"""Read-only, bounded execution of model-generated SQL.

Safety is enforced by SQLite itself, not by inspecting the SQL text:
- the database file is opened read-only (`mode=ro`) with `PRAGMA query_only`;
- an authorizer allows only SELECT, reads of the application tables, ordinary
  functions and recursive CTEs, and denies everything else (writes, DDL,
  ATTACH, PRAGMA, reads of sqlite_master, ...);
- the sqlite3 driver refuses more than one statement per execute();
- a progress handler aborts queries that exceed a time or work budget;
- at most `max_rows` rows are fetched, and truncation is reported.
The SQL is executed exactly as given; the application never rewrites it.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_TABLES = frozenset({"venues", "events", "tickets"})
DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})
MAX_SQL_CHARS = 5000
_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_PROGRESS_EVERY = 1000  # VM instructions between progress-handler calls
_DENIAL_MARKERS = ("not authorized", "authorization denied", "prohibited", "readonly", "query_only")
_UNSAFE_MESSAGE = "The SQL tried an operation that is not allowed (read-only access to venues, events, tickets)."


class QueryError(Exception):
    """SQL that SQLite could not run (syntax, unknown column, ...). Repairable."""


class UnsafeQueryError(Exception):
    """SQL that tried something outside the read-only allowlist. Not retried."""


class QueryLimitError(Exception):
    """The query exceeded the time or work budget. Not retried."""


class DatabaseMissingError(Exception):
    pass


@dataclass(frozen=True)
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool  # True if more than len(rows) rows existed


def _authorizer(action, arg1, arg2, db_name, _source):
    if action == sqlite3.SQLITE_SELECT or action == _RECURSIVE:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        if db_name == "main" and arg1 in ALLOWED_TABLES:
            return sqlite3.SQLITE_OK
        # Table referenced without extracting a column (COUNT(*), CTE names): no data is read here.
        if db_name is None and not arg2 and not (arg1 or "").lower().startswith(("sqlite_", "pragma_")):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in DENIED_FUNCTIONS else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def check_sql(sql: str) -> str:
    """Cheap pre-checks before touching the database. Returns the SQL to execute."""
    text = (sql or "").strip()
    if not text:
        raise QueryError("The SQL was empty.")
    if len(text) > MAX_SQL_CHARS:
        raise UnsafeQueryError(f"The SQL is longer than {MAX_SQL_CHARS} characters.")
    return text


def run_query(
    db_path: str | Path,
    sql: str,
    *,
    max_rows: int = 200,
    timeout_s: float = 2.0,
    max_steps: int = 20_000_000,
) -> QueryResult:
    path = Path(db_path)
    if not path.is_file():
        raise DatabaseMissingError(f"Database not found at {path}. Run `uv run ask-ticketing-generate-data`.")
    text = check_sql(sql)
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + timeout_s
        budget = {"steps": 0}

        def progress() -> int:
            budget["steps"] += _PROGRESS_EVERY
            return int(time.monotonic() > deadline or budget["steps"] > max_steps)

        conn.set_progress_handler(progress, _PROGRESS_EVERY)
        try:
            cursor = conn.execute(text)
            if cursor.description is None:
                raise UnsafeQueryError("Only queries that return rows are allowed.")
            fetched = cursor.fetchmany(max_rows + 1)
        except sqlite3.ProgrammingError as exc:
            if "one statement" in str(exc):
                raise UnsafeQueryError("Only a single SQL statement is allowed.") from None
            raise QueryError(str(exc)) from None
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            if "interrupted" in msg:
                raise QueryLimitError(f"The query exceeded the execution budget ({timeout_s}s / {max_steps:,} steps).") from None
            if any(k in msg for k in _DENIAL_MARKERS):
                raise UnsafeQueryError(_UNSAFE_MESSAGE) from None
            raise QueryError(msg) from None
        except sqlite3.DatabaseError as exc:
            if any(k in str(exc) for k in _DENIAL_MARKERS):
                raise UnsafeQueryError(_UNSAFE_MESSAGE) from None
            raise QueryError(str(exc)) from None
        columns = [d[0] for d in cursor.description]
        truncated = len(fetched) > max_rows
        return QueryResult(text, columns, [list(r) for r in fetched[:max_rows]], truncated)
    finally:
        conn.close()
