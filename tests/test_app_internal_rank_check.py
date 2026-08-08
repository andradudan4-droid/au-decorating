from unittest.mock import MagicMock

import app as app_module


def test_rank_check_rejects_missing_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/rank-check")

    assert response.status_code == 401


def test_rank_check_checks_every_keyword_and_records_results(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(app_module.rank_tracking, "SERPAPI_API_KEY", "fake-key")
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

    # A legitimate None (SerpApi answered, AU Decorating just isn't in the local
    # pack) is a real result, not a failure - this run must still be a 200.
    assert response.status_code == 200
    assert response.get_json() == {"keywords_checked": 2, "keywords_failed": 0}
    fake_record.assert_any_call("painter portsmouth", 2)
    fake_record.assert_any_call("decorator portsmouth", None)


def test_rank_check_continues_after_a_failing_keyword(monkeypatch):
    # marketing/rank_tracking.py's check_ranking() calls response.raise_for_status()
    # with no try/except of its own — a SerpApi outage or rate-limit response on
    # one keyword must not stop the rest of the batch from being checked. This
    # mirrors the C2 fix from the lead follow-up plan (one failure must not
    # permanently stall everything behind it).
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(app_module.rank_tracking, "SERPAPI_API_KEY", "fake-key")
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

    # The other two keywords still got checked and recorded (isolation held),
    # but the run reports 500 so `curl -sf` in the weekly Action turns red
    # rather than masking a real SerpApi failure as a quiet week.
    assert response.status_code == 500
    assert response.get_json() == {"keywords_checked": 2, "keywords_failed": 1}
    fake_record.assert_any_call("painter portsmouth", 2)
    fake_record.assert_any_call("painter waterlooville", 5)
    assert fake_record.call_count == 2


def test_rank_check_returns_500_when_serpapi_key_not_configured(monkeypatch):
    # With no key, check_ranking() returns None for every keyword without making
    # an HTTP call. Recording those would fill /admin/rankings with rows that
    # read as "Not in top 3" but actually mean "never configured", so the route
    # must bail out before the loop instead of writing misleading NULLs.
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(app_module.rank_tracking, "SERPAPI_API_KEY", None)
    fake_record = MagicMock()
    monkeypatch.setattr(app_module.marketing_db, "record_ranking", fake_record)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/rank-check", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "SERPAPI_API_KEY not configured"}
    fake_record.assert_not_called()
