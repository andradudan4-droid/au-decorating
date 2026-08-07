from unittest.mock import MagicMock

from marketing import sms


def test_send_followup_sms_uses_twilio_client(monkeypatch):
    monkeypatch.setattr(sms.os.environ, "get", {
        "TWILIO_ACCOUNT_SID": "sid123",
        "TWILIO_AUTH_TOKEN": "token123",
        "TWILIO_FROM_NUMBER": "+447000000000",
    }.get)
    fake_client = MagicMock()
    fake_client_cls = MagicMock(return_value=fake_client)
    monkeypatch.setattr(sms, "Client", fake_client_cls)

    sms.send_followup_sms("+447123456789", "Just checking in!")

    fake_client_cls.assert_called_once_with("sid123", "token123")
    fake_client.messages.create.assert_called_once_with(
        to="+447123456789", from_="+447000000000", body="Just checking in!"
    )


def test_send_followup_sms_normalises_uk_national_number_to_e164(monkeypatch):
    """Leads are stored as "07123 456789"; Twilio rejects anything but E.164."""
    monkeypatch.setattr(sms.os.environ, "get", {
        "TWILIO_ACCOUNT_SID": "sid123",
        "TWILIO_AUTH_TOKEN": "token123",
        "TWILIO_FROM_NUMBER": "+447000000000",
    }.get)
    fake_client = MagicMock()
    monkeypatch.setattr(sms, "Client", MagicMock(return_value=fake_client))

    sms.send_followup_sms("07123 456789", "Just checking in!")

    fake_client.messages.create.assert_called_once_with(
        to="+447123456789", from_="+447000000000", body="Just checking in!"
    )


def test_to_e164_normalises_uk_formats():
    assert sms.to_e164("07123 456789") == "+447123456789"
    assert sms.to_e164("07123-456789") == "+447123456789"
    assert sms.to_e164("+44 7123 456789") == "+447123456789"
    assert sms.to_e164("447123456789") == "+447123456789"
    assert sms.to_e164("(07123) 456789") == "+447123456789"


def test_send_followup_sms_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(sms.os.environ, "get", {}.get)
    fake_client_cls = MagicMock()
    monkeypatch.setattr(sms, "Client", fake_client_cls)

    sms.send_followup_sms("+447123456789", "Just checking in!")

    fake_client_cls.assert_not_called()
