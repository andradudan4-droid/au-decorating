from unittest.mock import MagicMock

import app as app_module


def test_admin_leads_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.get("/admin/leads")

    assert response.status_code == 401


def test_admin_leads_lists_leads_with_correct_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.marketing_db, "list_leads",
        lambda: [{"id": 1, "name": "James", "phone": "07123456789",
                   "email": None, "status": "contacted"}],
    )
    client = app_module.app.test_client()

    response = client.get("/admin/leads?key=correct-secret")

    assert response.status_code == 200
    assert b"James" in response.data


def test_mark_replied_updates_lead_and_redirects(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    fake_mark_replied = MagicMock()
    monkeypatch.setattr(app_module.marketing_db, "mark_replied", fake_mark_replied)
    client = app_module.app.test_client()

    response = client.post(
        "/admin/leads/7/mark-replied?key=correct-secret"
    )

    fake_mark_replied.assert_called_once_with(7)
    assert response.status_code == 302
