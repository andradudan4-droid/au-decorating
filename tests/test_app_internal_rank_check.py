from unittest.mock import MagicMock

import app as app_module


def test_rank_check_rejects_missing_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/rank-check")

    assert response.status_code == 401


def test_rank_check_checks_every_keyword_and_records_results(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.rank_tracking, "KEYWORDS", ["painter portsmouth", "decorator portsmouth"]
    )
    fake_check = MagicMock(side_effect=[2, None])
    fake_record = MagicMock()
    monkeypatch.setattr(app_module.rank_tracking, "check_ranking", fake_check)
    monkeypatch.setattr(app_module.marketing_db, "record_ranking", fake_record)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/rank-check", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"keywords_checked": 2}
    fake_record.assert_any_call("painter portsmouth", 2)
    fake_record.assert_any_call("decorator portsmouth", None)


def test_rank_check_continues_after_a_failing_keyword(monkeypatch):
    # marketing/rank_tracking.py's check_ranking() calls response.raise_for_status()
    # with no try/except of its own — a SerpApi outage or rate-limit response on
    # one keyword must not stop the rest of the batch from being checked. This
    # mirrors the C2 fix from the lead follow-up plan (one failure must not
    # permanently stall everything behind it).
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.rank_tracking, "KEYWORDS",
        ["painter portsmouth", "decorator portsmouth", "painter waterlooville"],
    )
    fake_check = MagicMock(side_effect=[2, Exception("SerpApi rate limited"), 5])
    fake_record = MagicMock()
    monkeypatch.setattr(app_module.rank_tracking, "check_ranking", fake_check)
    monkeypatch.setattr(app_module.marketing_db, "record_ranking", fake_record)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/rank-check", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"keywords_checked": 2}
    fake_record.assert_any_call("painter portsmouth", 2)
    fake_record.assert_any_call("painter waterlooville", 5)
    assert fake_record.call_count == 2
