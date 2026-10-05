import hashlib

import pytest

from ask_ticketing.sql_guard import (
    DatabaseMissingError,
    QueryError,
    QueryLimitError,
    UnsafeQueryError,
    run_query,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM tickets WHERE status = 'sold'",
        "WITH s AS (SELECT event_id, COUNT(*) AS n FROM tickets GROUP BY event_id) SELECT MAX(n) FROM s",
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 5) SELECT SUM(x) FROM c",
        "SELECT e.name, v.name FROM events e JOIN venues v ON v.venue_id = e.venue_id LIMIT 1;",
        "-- comment\nSELECT date('2026-10-01', '-1 month') AS d",
    ],
)
def test_allows_legitimate_selects(db_path, sql):
    result = run_query(db_path, sql)
    assert result.rows and result.sql == sql.strip()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO venues VALUES (9, 'x')",
        "UPDATE tickets SET status = 'sold'",
        "DELETE FROM tickets",
        "DROP TABLE tickets",
        "CREATE TABLE x (a)",
        "SELECT 1; DELETE FROM tickets",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA table_info(tickets)",
        "PRAGMA query_only = OFF",
        "SELECT * FROM pragma_table_info('tickets')",
        "SELECT sql FROM sqlite_master",
        "SELECT (SELECT COUNT(*) FROM sqlite_master)",
        "SELECT load_extension('x')",
        "VACUUM",
        "REPLACE INTO venues VALUES (1, 'x')",
        "SELECT 1 " + "x" * 6000,
    ],
)
def test_rejects_unsafe_sql(db_path, sql):
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with pytest.raises(UnsafeQueryError):
        run_query(db_path, sql)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_invalid_sql_is_repairable_error(db_path):
    with pytest.raises(QueryError, match="no such column"):
        run_query(db_path, "SELECT nope FROM tickets")
    with pytest.raises(QueryError):
        run_query(db_path, "   ")


def test_execution_budget_is_enforced(db_path):
    runaway = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(QueryLimitError):
        run_query(db_path, runaway, timeout_s=0.2)
    with pytest.raises(QueryLimitError):
        run_query(db_path, runaway, max_steps=100_000)


def test_row_cap_reports_truncation(db_path):
    r = run_query(db_path, "SELECT ticket_id FROM tickets ORDER BY ticket_id", max_rows=5)
    assert r.rows == [[1], [2], [3], [4], [5]] and r.truncated
    r = run_query(db_path, "SELECT venue_id FROM venues", max_rows=5)
    assert len(r.rows) == 2 and not r.truncated


def test_missing_database(tmp_path):
    with pytest.raises(DatabaseMissingError):
        run_query(tmp_path / "nope.db", "SELECT 1")
    assert not (tmp_path / "nope.db").exists()  # read-only open never creates the file
