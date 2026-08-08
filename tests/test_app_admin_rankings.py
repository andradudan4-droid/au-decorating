import app as app_module


def test_admin_rankings_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.get("/admin/rankings")

    assert response.status_code == 401


def test_admin_rankings_shows_position_with_correct_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.marketing_db, "get_latest_rankings",
        lambda: [{"keyword": "painter portsmouth", "position": 2,
                   "checked_at": "2026-08-10 06:00:00"}],
    )
    client = app_module.app.test_client()

    response = client.get("/admin/rankings?key=correct-secret")

    assert response.status_code == 200
    assert b"painter portsmouth" in response.data
    assert b">2<" in response.data


def test_admin_rankings_shows_not_in_top_3_for_null_position(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.marketing_db, "get_latest_rankings",
        lambda: [{"keyword": "painter waterlooville", "position": None,
                   "checked_at": "2026-08-10 06:00:00"}],
    )
    client = app_module.app.test_client()

    response = client.get("/admin/rankings?key=correct-secret")

    assert b"Not in top 3" in response.data
