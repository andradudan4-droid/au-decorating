from unittest.mock import MagicMock

import app as app_module


def test_tick_rejects_missing_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/tick")

    assert response.status_code == 401


def test_tick_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/tick", headers={"X-Tick-Secret": "wrong"})

    assert response.status_code == 401


def test_tick_runs_followups_with_correct_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    fake_run = MagicMock(return_value=3)
    monkeypatch.setattr(app_module.followups, "run_due_followups", fake_run)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/tick", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"followups_sent": 3}
    fake_run.assert_called_once()
