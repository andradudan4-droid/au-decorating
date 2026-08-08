from unittest.mock import MagicMock

import app as app_module


def test_rank_check_rejects_missing_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/rank-check")

    assert response.status_code == 401


def test_rank_check_returns_500_when_serpapi_key_not_configured(monkeypatch):
    # With no key, check_ranking() returns None for every keyword without making
    # an HTTP call. Starting the batch anyway would fill /admin/rankings with
    # rows that read as "Not in top 3" but actually mean "never configured", so
    # the route must bail out before starting the background thread instead of
    # writing misleading NULLs. This check stays synchronous.
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(app_module.rank_tracking, "SERPAPI_API_KEY", None)
    fake_thread_cls = MagicMock()
    monkeypatch.setattr(app_module.threading, "Thread", fake_thread_cls)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/rank-check", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "SERPAPI_API_KEY not configured"}
    fake_thread_cls.assert_not_called()


def test_rank_check_starts_background_batch_with_correct_secret(monkeypatch):
    # The route itself must return fast (no sequential SerpApi calls on the
    # request thread) so 8 keywords' worth of slow local-pack lookups can't
    # trip the web server's request timeout or block the live customer chat.
    # The actual checking happens in _run_rank_check_batch, run off-thread.
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    monkeypatch.setattr(app_module.rank_tracking, "SERPAPI_API_KEY", "fake-key")
    fake_thread = MagicMock()
    fake_thread_cls = MagicMock(return_value=fake_thread)
    monkeypatch.setattr(app_module.threading, "Thread", fake_thread_cls)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/rank-check", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 202
    assert response.get_json() == {"status": "started"}
    fake_thread_cls.assert_called_once_with(
        target=app_module._run_rank_check_batch, daemon=True
    )
    fake_thread.start.assert_called_once()


def test_run_rank_check_batch_checks_every_keyword_and_records_results(monkeypatch):
    monkeypatch.setattr(
        app_module.rank_tracking, "KEYWORDS", ["painter portsmouth", "decorator portsmouth"]
    )
    fake_check = MagicMock(side_effect=[2, None])
    fake_record = MagicMock()
    monkeypatch.setattr(app_module.rank_tracking, "check_ranking", fake_check)
    monkeypatch.setattr(app_module.marketing_db, "record_ranking", fake_record)

    app_module._run_rank_check_batch()

    # A legitimate None (SerpApi answered, AU Decorating just isn't in the local
    # pack) is a real result, not a failure - it's still recorded.
    fake_record.assert_any_call("painter portsmouth", 2)
    fake_record.assert_any_call("decorator portsmouth", None)
    assert fake_record.call_count == 2


def test_run_rank_check_batch_continues_after_a_failing_keyword(monkeypatch):
    # marketing/rank_tracking.py's check_ranking() calls response.raise_for_status()
    # with no try/except of its own — a SerpApi outage or rate-limit response on
    # one keyword must not stop the rest of the batch from being checked. This
    # mirrors the C2 fix from the lead follow-up plan (one failure must not
    # permanently stall everything behind it).
    monkeypatch.setattr(
        app_module.rank_tracking, "KEYWORDS",
        ["painter portsmouth", "decorator portsmouth", "painter waterlooville"],
    )
    fake_check = MagicMock(side_effect=[2, Exception("SerpApi rate limited"), 5])
    fake_record = MagicMock()
    monkeypatch.setattr(app_module.rank_tracking, "check_ranking", fake_check)
    monkeypatch.setattr(app_module.marketing_db, "record_ranking", fake_record)

    app_module._run_rank_check_batch()

    # The other two keywords still got checked and recorded (isolation held).
    fake_record.assert_any_call("painter portsmouth", 2)
    fake_record.assert_any_call("painter waterlooville", 5)
    assert fake_record.call_count == 2
