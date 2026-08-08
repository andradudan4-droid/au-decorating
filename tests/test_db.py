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


def test_get_connection_sets_connect_timeout(monkeypatch):
    """A slow or unreachable DB must not hang startup or a live request."""
    fake_connect = MagicMock(return_value="conn")
    monkeypatch.setattr(db.psycopg2, "connect", fake_connect)
    monkeypatch.setenv("DATABASE_URL", "postgres://example/db")

    assert db.get_connection() == "conn"

    assert fake_connect.call_args.kwargs["connect_timeout"] == 5


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


def test_get_leads_with_followup_counts_selects_last_contacted_at(monkeypatch):
    """leads_due needs last_contacted_at to enforce the minimum gap between sends."""
    cursor = FakeCursor(fetchall_result=[])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.get_leads_with_followup_counts()

    sql, _ = cursor.executed[0]
    assert "l.last_contacted_at" in sql


def test_find_active_lead_matches_by_phone(monkeypatch):
    existing = {"id": 5, "name": "James", "phone": "07123 456789", "email": None}
    cursor = FakeCursor(fetchone_result=existing)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.find_active_lead("07123 456789", None)

    assert result == existing
    sql, params = cursor.executed[0]
    assert "FROM leads" in sql
    assert "status = 'contacted'" in sql
    assert "LIMIT 1" in sql
    assert params == {"phone": "07123 456789", "email": None}
    assert conn.closed


def test_find_active_lead_matches_by_email(monkeypatch):
    existing = {"id": 6, "name": "Priya", "phone": None, "email": "priya@example.com"}
    cursor = FakeCursor(fetchone_result=existing)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.find_active_lead(None, "priya@example.com")

    assert result == existing
    _, params = cursor.executed[0]
    assert params == {"phone": None, "email": "priya@example.com"}
    assert conn.closed


def test_find_active_lead_returns_none_when_no_match(monkeypatch):
    cursor = FakeCursor(fetchone_result=None)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    assert db.find_active_lead("07999 999999", "nobody@example.com") is None
    assert conn.closed


def test_find_active_lead_skips_query_when_no_contact_details(monkeypatch):
    """All-NULL contact details would otherwise match arbitrary NULL-phone rows."""
    cursor = FakeCursor(fetchone_result={"id": 1})
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    assert db.find_active_lead(None, None) is None
    assert cursor.executed == []


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


def test_init_schema_creates_content_items_table(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.init_schema()

    executed_sql = " ".join(sql for sql, _ in cursor.executed)
    assert "CREATE TABLE IF NOT EXISTS content_items" in executed_sql


def test_insert_content_item_returns_new_id(monkeypatch):
    cursor = FakeCursor(fetchone_result={"id": 5})
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    item_id = db.insert_content_item(
        "gbp_post", "repainted a terrace in Southsea", "Just finished..."
    )

    assert item_id == 5
    sql, params = cursor.executed[0]
    assert "INSERT INTO content_items" in sql
    assert params["content_type"] == "gbp_post"
    assert params["generated_text"] == "Just finished..."
    assert conn.committed
    assert conn.closed


def test_list_content_items_returns_rows(monkeypatch):
    rows = [{"id": 1, "content_type": "gbp_post"}]
    cursor = FakeCursor(fetchall_result=rows)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.list_content_items()

    assert result == rows
    sql, params = cursor.executed[0]
    assert "SELECT * FROM content_items" in sql
    assert params["limit"] == 20
    assert conn.closed
