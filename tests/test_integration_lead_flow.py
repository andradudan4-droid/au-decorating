"""Integration test across the _lead_fields -> insert_lead seam.

Every other test mocks the layer directly beneath it, so the key-name contract
between app.py's _lead_fields() output and db.insert_lead()'s expected dict keys
(Name, Phone, Email, Area, Job) is otherwise untested - a rename on either side
would break production with a green suite.
"""

import app as app_module
from marketing import db
from tests.test_db import FakeConnection, FakeCursor


class InterpolatingFakeCursor(FakeCursor):
    """FakeCursor that actually resolves psycopg2's %(name)s placeholders.

    The plain FakeCursor only records (sql, params), so a missing key would pass
    silently - defeating the point of this test. Python's % operator on a dict
    raises KeyError for an unknown key, matching psycopg2's behaviour.
    """

    def execute(self, sql, params=None):
        if isinstance(params, dict):
            sql % params  # raises KeyError if _lead_fields drops an expected key
        super().execute(sql, params)


def test_lead_fields_output_satisfies_insert_lead_contract(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "summarise_lead",
        lambda conversation: (
            "Name: James\n"
            "Job / work wanted: Kitchen painting\n"
            "Location / area: Southsea\n"
        ),
    )
    conversation = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Hi, I need my kitchen painted."},
        {"role": "assistant", "content": "Happy to help - what's your name and number?"},
        {
            "role": "user",
            "content": "It's James, I'm in Southsea PO4 8DT, "
                       "call me on 07123 456789 or james@example.com",
        },
    ]

    # The real thing - real regex extraction, real dict assembly.
    fields = app_module._lead_fields(conversation)

    cursor = InterpolatingFakeCursor(fetchone_result={"id": 42})
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    # Would raise KeyError if the key names on either side drifted apart.
    lead_id = db.insert_lead(fields)

    assert lead_id == 42
    sql, params = cursor.executed[0]
    assert "INSERT INTO leads" in sql
    assert params["Name"] == "James"
    assert params["Phone"] == "07123 456789"
    assert params["Email"] == "james@example.com"
    assert params["Area"] == "Southsea"
    assert params["Job"] == "Kitchen painting"
    assert conn.committed


def test_lead_fields_with_nothing_captured_still_satisfies_contract(monkeypatch):
    """A failed summariser yields all-None fields; insert_lead must still work."""
    monkeypatch.setattr(app_module, "summarise_lead", lambda conversation: None)
    conversation = [{"role": "user", "content": "just browsing, thanks"}]

    fields = app_module._lead_fields(conversation)

    cursor = InterpolatingFakeCursor(fetchone_result={"id": 7})
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    assert db.insert_lead(fields) == 7
    _, params = cursor.executed[0]
    assert params["Name"] is None
    assert params["Phone"] is None
