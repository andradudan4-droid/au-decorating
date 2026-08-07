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


def test_chat_notification_persists_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_post_resend", MagicMock())
    fake_insert_lead = MagicMock(return_value=99)
    monkeypatch.setattr(app_module.marketing_db, "insert_lead", fake_insert_lead)
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
        sess["session_id"] = "test-session"
    app_module.all_conversations["test-session"] = [
        {"role": "system", "content": app_module.SYSTEM_PROMPT}
    ]

    client.post(
        "/chat",
        json={"message": "It's James, kitchen needs painting, call 07123 456789"},
    )

    fake_insert_lead.assert_called_once()
    called_fields = fake_insert_lead.call_args.args[0]
    assert called_fields["Name"] == "James"
