from unittest.mock import MagicMock

import app as app_module


def test_send_lead_email_returns_fields(monkeypatch):
    monkeypatch.setattr(app_module, "_post_resend", MagicMock())
    monkeypatch.setattr(
        app_module,
        "summarise_lead",
        lambda conversation: "Name: James\nJob / work wanted: Kitchen painting",
    )
    conversation = [
        {"role": "user", "content": "It's James, kitchen needs painting, "
                                     "call me on 07123 456789"},
    ]

    fields = app_module.send_lead_email(conversation)

    assert fields["Name"] == "James"
    assert fields["Job"] == "Kitchen painting"
    assert fields["Phone"] == "07123 456789"


def _setup_chat(monkeypatch, session_id, fake_insert_lead, fake_find_active_lead=None,
                fake_post_resend=None):
    """Wire up a /chat call with the outside world mocked out."""
    monkeypatch.setattr(app_module, "_post_resend", fake_post_resend or MagicMock())
    monkeypatch.setattr(app_module.marketing_db, "insert_lead", fake_insert_lead)
    monkeypatch.setattr(
        app_module.marketing_db, "find_active_lead",
        fake_find_active_lead or MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        app_module, "client_chat",
        lambda **kwargs: MagicMock(
            choices=[MagicMock(message=MagicMock(
                content="Thanks James, that's everything!\n[[READY]]"
            ))]
        ),
    )
    monkeypatch.setattr(
        app_module, "summarise_lead",
        lambda conversation: "Name: James\nJob / work wanted: Kitchen painting",
    )

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["session_id"] = session_id
    app_module.all_conversations[session_id] = [
        {"role": "system", "content": app_module.SYSTEM_PROMPT}
    ]
    return client


def test_chat_notification_persists_lead(monkeypatch):
    fake_insert_lead = MagicMock(return_value=99)
    client = _setup_chat(monkeypatch, "test-session", fake_insert_lead)

    client.post(
        "/chat",
        json={"message": "It's James, kitchen needs painting, call 07123 456789"},
    )

    fake_insert_lead.assert_called_once()
    called_fields = fake_insert_lead.call_args.args[0]
    assert called_fields["Name"] == "James"


def test_declined_lead_is_emailed_but_not_enrolled_in_followups(monkeypatch):
    """A visitor who says "not interested" must not be chased by the sequence."""
    fake_insert_lead = MagicMock(return_value=99)
    fake_post_resend = MagicMock()
    client = _setup_chat(
        monkeypatch, "declined-session", fake_insert_lead,
        fake_post_resend=fake_post_resend,
    )

    client.post(
        "/chat",
        json={"message": "no thanks, not interested. I'm on 07123 456789 anyway"},
    )

    # No follow-up enrolment...
    fake_insert_lead.assert_not_called()
    # ...but Mehmet is still emailed exactly as before.
    fake_post_resend.assert_called()


def test_polite_signoff_is_still_enrolled_in_followups(monkeypatch):
    """A keen customer wrapping up politely is NOT a decline - still follow up.

    "that's all" / "all good" / "im good" all match CLOSING_RE, which governs
    when a lead is emailed. Only the narrower DECLINE_RE opts someone out of
    the follow-up sequence.
    """
    fake_insert_lead = MagicMock(return_value=99)
    fake_post_resend = MagicMock()
    client = _setup_chat(
        monkeypatch, "signoff-session", fake_insert_lead,
        fake_post_resend=fake_post_resend,
    )

    client.post(
        "/chat",
        json={"message": "that's all thanks! I'm James, call me on 07123 456789"},
    )

    fake_insert_lead.assert_called_once()
    assert fake_insert_lead.call_args.args[0]["Name"] == "James"
    fake_post_resend.assert_called()


def test_decline_and_closing_regexes_classify_phrases_correctly():
    """DECLINE_RE is a strict subset of CLOSING_RE: declines only, no sign-offs."""
    declines = ["not interested", "no longer interested", "no thanks", "no thank you"]
    signoffs = ["that's all", "that's everything", "all good", "im good",
                "nothing else", "goodbye", "thanks that's great"]

    for phrase in declines:
        assert app_module._looks_like_decline(phrase), phrase
        # Every decline is still a closing phrase - email behaviour unchanged.
        assert app_module._looks_like_closing(phrase), phrase

    for phrase in signoffs:
        assert not app_module._looks_like_decline(phrase), phrase
        # ...but still triggers the email safety net exactly as before.
        assert app_module._looks_like_closing(phrase), phrase


def test_duplicate_lead_is_not_inserted_again(monkeypatch):
    """A repeat chat from the same person must not start a second sequence."""
    fake_insert_lead = MagicMock(return_value=99)
    fake_find_active_lead = MagicMock(return_value={"id": 42, "name": "James"})
    fake_post_resend = MagicMock()
    client = _setup_chat(
        monkeypatch, "dup-session", fake_insert_lead,
        fake_find_active_lead=fake_find_active_lead,
        fake_post_resend=fake_post_resend,
    )

    client.post(
        "/chat",
        json={"message": "It's James, kitchen needs painting, call 07123 456789"},
    )

    fake_find_active_lead.assert_called_once_with("07123 456789", None)
    fake_insert_lead.assert_not_called()
    # The email to Mehmet is unaffected.
    fake_post_resend.assert_called()
