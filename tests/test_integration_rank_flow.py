"""Integration tests across the rank-tracking stack.

Every other test for this feature mocks the layer directly beneath it: the route
tests mock `rank_tracking` and `marketing_db` wholesale, and the `marketing_db`
tests use tests.test_db.FakeCursor, which only records `(sql, params)` without
ever resolving the placeholders. So the contracts *between* the layers - the
INSERT's column names, the SELECT's column names, the `%(name)s` placeholders
versus the params dict keys, and the key names /admin/rankings reads off each
row - are untested. A rename on any one side would ship with a green suite.

This mirrors tests/test_integration_lead_flow.py's InterpolatingFakeCursor idea
and takes it a step further: `record_ranking`'s INSERT and `get_latest_rankings`'s
SELECT are different shapes, and the interesting bug is one drifting from the
other, so the fake here is a tiny in-memory engine for the keyword_rankings
table. It (a) really runs `sql % params`, so a placeholder/params mismatch raises
exactly as psycopg2 would, (b) validates every column named in the real INSERT
and SELECT against the real CREATE TABLE from db.init_schema(), and (c) actually
stores and returns rows, applying the DISTINCT ON (keyword) + latest-first
semantics, so the round trip is a real one rather than a canned fetchall.
"""

import datetime
import re
from unittest.mock import MagicMock

import app as app_module
from marketing import db, rank_tracking
from tests.test_db import FakeConnection

TABLE = "keyword_rankings"

PLACEHOLDER_RE = re.compile(r"%\((\w+)\)s")


def _paren_body(sql, start):
    """Return the text inside the parenthesised group that opens at/after `start`."""
    open_at = sql.index("(", start)
    depth = 0
    for i in range(open_at, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_at + 1:i]
    raise AssertionError(f"unbalanced parentheses in: {sql}")


class KeywordRankingsCursor:
    """A minimal but genuinely-executing stand-in for the keyword_rankings table.

    Only understands the handful of statements marketing/db.py actually issues
    against this table; anything else is recorded and ignored, so init_schema()'s
    other CREATE TABLEs pass through harmlessly.
    """

    def __init__(self):
        self.executed = []
        self.rows = []
        self.schema_columns = None
        self._result = []
        self._clock = datetime.datetime(2026, 8, 10, 6, 0, 0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _next_timestamp(self):
        self._clock += datetime.timedelta(minutes=1)
        return self._clock

    def _check_columns(self, columns, context):
        if self.schema_columns is None:
            return
        unknown = [c for c in columns if c not in self.schema_columns]
        assert not unknown, (
            f"{context} references column(s) {unknown} that do not exist in the "
            f"real {TABLE} schema {sorted(self.schema_columns)}"
        )

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

        if isinstance(params, dict):
            # Raises KeyError for a placeholder with no matching param, exactly
            # like psycopg2 would - the whole point of interpolating for real.
            sql % params

        stripped = sql.strip()
        if f"CREATE TABLE IF NOT EXISTS {TABLE}" in stripped:
            self._load_schema(stripped)
        elif stripped.upper().startswith("INSERT INTO") and TABLE in stripped:
            self._insert(stripped, params)
        elif stripped.upper().startswith("SELECT") and TABLE in stripped:
            self._select(stripped)

    def _load_schema(self, sql):
        body = _paren_body(sql, sql.index(TABLE))
        columns = []
        for line in body.splitlines():
            tokens = line.strip().rstrip(",").split()
            if len(tokens) >= 2 and tokens[0].isidentifier():
                columns.append(tokens[0])
        self.schema_columns = set(columns)

    def _insert(self, sql, params):
        columns = [c.strip() for c in _paren_body(sql, sql.index(TABLE)).split(",")]
        self._check_columns(columns, "INSERT")

        values_at = sql.upper().index("VALUES")
        placeholders = PLACEHOLDER_RE.findall(_paren_body(sql, values_at))
        assert len(placeholders) == len(columns), (
            f"INSERT lists {len(columns)} columns but {len(placeholders)} values"
        )

        row = {name: None for name in self.schema_columns}
        row["id"] = len(self.rows) + 1
        row["checked_at"] = self._next_timestamp()  # DEFAULT now()
        for column, placeholder in zip(columns, placeholders):
            row[column] = params[placeholder]
        self.rows.append(row)

    def _select(self, sql):
        select_body = sql[sql.upper().index("SELECT") + len("SELECT"):sql.upper().index("FROM")]
        distinct_on = None
        match = re.search(r"DISTINCT\s+ON\s*\(", select_body, re.IGNORECASE)
        if match:
            distinct_on = _paren_body(select_body, match.start()).strip()
            select_body = select_body[select_body.index(")", match.start()) + 1:]
        columns = [c.strip() for c in select_body.split(",") if c.strip()]
        self._check_columns(columns, "SELECT")
        if distinct_on:
            self._check_columns([distinct_on], "DISTINCT ON")

        # ORDER BY keyword, checked_at DESC -> newest row wins per keyword.
        ordered = sorted(self.rows, key=lambda r: r["checked_at"], reverse=True)
        seen = set()
        result = []
        for row in ordered:
            if distinct_on:
                if row[distinct_on] in seen:
                    continue
                seen.add(row[distinct_on])
            result.append({c: row[c] for c in columns})
        result.sort(key=lambda r: r.get(distinct_on) if distinct_on else 0)
        self._result = result

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


def _connected(monkeypatch):
    """Wire marketing.db onto a fake that knows the real keyword_rankings schema."""
    cursor = KeywordRankingsCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    db.init_schema()  # real DDL - teaches the fake the real column names
    assert cursor.schema_columns == {"id", "keyword", "position", "checked_at"}
    return cursor, conn


def _serpapi_response(places):
    response = MagicMock()
    response.json.return_value = {"local_results": {"places": places}}
    response.raise_for_status = MagicMock()
    return response


def test_record_ranking_and_get_latest_rankings_round_trip(monkeypatch):
    """The real INSERT and the real SELECT must agree with each other and the schema."""
    cursor, conn = _connected(monkeypatch)

    db.record_ranking("painter portsmouth", 2)
    db.record_ranking("painter waterlooville", None)  # legitimately not in top 3
    db.record_ranking("painter portsmouth", 1)  # newer result supersedes the 2

    assert conn.committed

    rankings = db.get_latest_rankings()

    assert [r["keyword"] for r in rankings] == [
        "painter portsmouth",
        "painter waterlooville",
    ]
    by_keyword = {r["keyword"]: r for r in rankings}
    # DISTINCT ON (keyword) ORDER BY keyword, checked_at DESC -> latest only.
    assert by_keyword["painter portsmouth"]["position"] == 1
    # A NULL position survives the insert, the ordering and the projection
    # without erroring, and stays distinguishable from a real position.
    assert by_keyword["painter waterlooville"]["position"] is None
    assert by_keyword["painter waterlooville"]["checked_at"] is not None

    inserts = [(s, p) for s, p in cursor.executed if "INSERT INTO keyword_rankings" in s]
    assert len(inserts) == 3
    assert inserts[-1][1] == {"keyword": "painter portsmouth", "position": 1}


class _SynchronousThread:
    """Stands in for threading.Thread so the background batch runs inline,
    in test order, instead of on a real (non-deterministic) thread."""

    def __init__(self, target, daemon=True):
        self._target = target

    def start(self):
        self._target()


def test_rank_check_endpoint_result_reaches_admin_rankings_page(monkeypatch):
    """/internal/rank-check -> (background batch) -> record_ranking ->
    get_latest_rankings -> /admin/rankings.

    Only the true outer boundaries are mocked: SerpApi's HTTP call, the DB
    connection, and threading.Thread itself (swapped for a synchronous
    stand-in so the background batch completes before the assertions run,
    without changing anything about what code actually executes). Everything
    between - the route, check_ranking()'s JSON parsing, both SQL statements
    and the /admin/rankings rendering - is the real code.
    """
    cursor, _ = _connected(monkeypatch)
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "admin-secret")
    monkeypatch.setattr(app_module.threading, "Thread", _SynchronousThread)
    monkeypatch.setattr(rank_tracking, "SERPAPI_API_KEY", "fake-key")
    monkeypatch.setattr(
        rank_tracking, "KEYWORDS", ["painter portsmouth", "painter waterlooville"]
    )
    monkeypatch.setattr(
        rank_tracking.requests,
        "get",
        MagicMock(
            side_effect=[
                _serpapi_response(
                    [
                        {"position": 1, "title": "Some Other Painter"},
                        {"position": 2, "title": "AU Decorating Ltd"},
                    ]
                ),
                # AU Decorating absent from the local pack: a real, quiet result.
                _serpapi_response([{"position": 1, "title": "Some Other Painter"}]),
            ]
        ),
    )
    client = app_module.app.test_client()

    check = client.post(
        "/internal/rank-check", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert check.status_code == 202
    assert check.get_json() == {"status": "started"}
    assert len(cursor.rows) == 2

    page = client.get("/admin/rankings?key=admin-secret")

    assert page.status_code == 200
    body = page.data.decode()
    # The position SerpApi reported for the keyword the endpoint checked is what
    # the admin page shows - end to end, through the real SQL contract.
    assert "painter portsmouth" in body
    assert "<td>2</td>" in body
    # The genuine None stays readable as "not ranked", not as a fake position.
    assert "painter waterlooville" in body
    assert "Not in top 3" in body
