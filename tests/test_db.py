from unittest.mock import MagicMock, call

from marketing import db


class FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_init_schema_creates_tables(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.init_schema()

    executed_sql = " ".join(sql for sql, _ in cursor.executed)
    assert "CREATE TABLE IF NOT EXISTS leads" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS lead_followups" in executed_sql
    assert conn.committed
    assert conn.closed


def test_insert_lead_returns_new_id(monkeypatch):
    cursor = FakeCursor(fetchone_result={"id": 42})
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    lead_id = db.insert_lead(
        {"Name": "James", "Phone": "07123 456789", "Email": None,
         "Area": "Southsea", "Job": "Kitchen painting"}
    )

    assert lead_id == 42
    sql, params = cursor.executed[0]
    assert "INSERT INTO leads" in sql
    assert params["Name"] == "James"
    assert conn.committed
    assert conn.closed


def test_list_leads_returns_rows(monkeypatch):
    rows = [{"id": 1, "name": "James"}, {"id": 2, "name": "Priya"}]
    cursor = FakeCursor(fetchall_result=rows)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.list_leads()

    assert result == rows
    assert conn.closed


def test_get_leads_with_followup_counts_queries_active_leads(monkeypatch):
    rows = [{"id": 1, "name": "James", "followup_count": 0}]
    cursor = FakeCursor(fetchall_result=rows)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.get_leads_with_followup_counts()

    assert result == rows
    sql, _ = cursor.executed[0]
    assert "lead_followups" in sql
    assert "status = 'contacted'" in sql
    assert conn.closed


def test_record_followup_inserts_and_updates_last_contacted(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.record_followup(7, 1, "email", "Hey James, following up!")

    assert len(cursor.executed) == 2
    insert_sql, insert_params = cursor.executed[0]
    assert "INSERT INTO lead_followups" in insert_sql
    assert insert_params == {
        "lead_id": 7, "step": 1, "channel": "email",
        "content": "Hey James, following up!",
    }
    update_sql, update_params = cursor.executed[1]
    assert "UPDATE leads SET last_contacted_at" in update_sql
    assert update_params == {"id": 7}
    assert conn.committed
    assert conn.closed


def test_mark_replied_updates_status(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.mark_replied(7)

    sql, params = cursor.executed[0]
    assert "UPDATE leads SET status = 'replied'" in sql
    assert params == {"id": 7}
    assert conn.committed
    assert conn.closed
