from unittest.mock import MagicMock

from marketing import rank_tracking


def _fake_response(json_data, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status = MagicMock()
    return response


def test_check_ranking_returns_position_when_business_found(monkeypatch):
    monkeypatch.setattr(rank_tracking, "SERPAPI_API_KEY", "fake-key")
    fake_get = MagicMock(
        return_value=_fake_response(
            {
                "local_results": {
                    "places": [
                        {"position": 1, "title": "Some Other Painter"},
                        {"position": 2, "title": "AU Decorating"},
                        {"position": 3, "title": "Another Decorator"},
                    ]
                }
            }
        )
    )
    monkeypatch.setattr(rank_tracking.requests, "get", fake_get)

    result = rank_tracking.check_ranking("painter portsmouth")

    assert result == 2
    call_kwargs = fake_get.call_args.kwargs
    assert call_kwargs["params"]["q"] == "painter portsmouth"
    assert call_kwargs["params"]["api_key"] == "fake-key"


def test_check_ranking_returns_none_when_business_not_found(monkeypatch):
    monkeypatch.setattr(rank_tracking, "SERPAPI_API_KEY", "fake-key")
    fake_get = MagicMock(
        return_value=_fake_response(
            {"local_results": {"places": [{"position": 1, "title": "Some Other Painter"}]}}
        )
    )
    monkeypatch.setattr(rank_tracking.requests, "get", fake_get)

    result = rank_tracking.check_ranking("painter portsmouth")

    assert result is None


def test_check_ranking_returns_none_when_no_local_results(monkeypatch):
    monkeypatch.setattr(rank_tracking, "SERPAPI_API_KEY", "fake-key")
    fake_get = MagicMock(return_value=_fake_response({}))
    monkeypatch.setattr(rank_tracking.requests, "get", fake_get)

    result = rank_tracking.check_ranking("painter portsmouth")

    assert result is None


def test_check_ranking_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(rank_tracking, "SERPAPI_API_KEY", None)
    fake_get = MagicMock()
    monkeypatch.setattr(rank_tracking.requests, "get", fake_get)

    result = rank_tracking.check_ranking("painter portsmouth")

    assert result is None
    fake_get.assert_not_called()


def test_keywords_list_has_exactly_the_eight_agreed_terms():
    assert rank_tracking.KEYWORDS == [
        "painter portsmouth",
        "decorator portsmouth",
        "painters and decorators portsmouth",
        "house painter portsmouth",
        "painter waterlooville",
        "decorator waterlooville",
        "painters and decorators waterlooville",
        "interior painter portsmouth",
    ]
