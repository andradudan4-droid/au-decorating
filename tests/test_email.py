from unittest.mock import MagicMock

from marketing import email


def test_send_followup_email_posts_to_resend(monkeypatch):
    monkeypatch.setattr(email, "RESEND_API_KEY", "fake-key")
    monkeypatch.setattr(email, "REPLY_TO", "mehmet@au-decorating.com")
    fake_response = MagicMock(status_code=200)
    fake_post = MagicMock(return_value=fake_response)
    monkeypatch.setattr(email.requests, "post", fake_post)

    email.send_followup_email("james@example.com", "James", "Just checking in!")

    fake_post.assert_called_once()
    _, kwargs = fake_post.call_args
    payload = kwargs["json"]
    assert payload["to"] == ["james@example.com"]
    assert payload["reply_to"] == "mehmet@au-decorating.com"
    assert payload["text"] == "Just checking in!"


def test_send_followup_email_skips_without_api_key(monkeypatch):
    monkeypatch.setattr(email, "RESEND_API_KEY", None)
    fake_post = MagicMock()
    monkeypatch.setattr(email.requests, "post", fake_post)

    email.send_followup_email("james@example.com", "James", "Just checking in!")

    fake_post.assert_not_called()
