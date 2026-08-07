from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

from marketing import followups


def _lead(followup_count=0, created_hours_ago=0, email="james@example.com", phone=None,
          last_contacted_hours_ago=None, lead_id=1, name="James", job="Kitchen painting"):
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(hours=created_hours_ago)
    # A lead that has never been followed up has last_contacted_at == created_at
    # (the column defaults to now() on insert and only record_followup moves it).
    if last_contacted_hours_ago is None:
        last_contacted_at = created_at
    else:
        last_contacted_at = now - timedelta(hours=last_contacted_hours_ago)
    return {
        "id": lead_id,
        "name": name,
        "job": job,
        "email": email,
        "phone": phone,
        "status": "contacted",
        "created_at": created_at,
        "last_contacted_at": last_contacted_at,
        "followup_count": followup_count,
    }


def test_leads_due_includes_lead_past_first_threshold(monkeypatch):
    lead = _lead(followup_count=0, created_hours_ago=25)
    monkeypatch.setattr(followups.db, "get_leads_with_followup_counts", lambda: [lead])

    due = followups.leads_due()

    assert due == [(lead, 1)]


def test_leads_due_excludes_lead_before_threshold(monkeypatch):
    lead = _lead(followup_count=0, created_hours_ago=5)
    monkeypatch.setattr(followups.db, "get_leads_with_followup_counts", lambda: [lead])

    assert followups.leads_due() == []


def test_leads_due_excludes_lead_past_final_step(monkeypatch):
    lead = _lead(followup_count=3, created_hours_ago=200)
    monkeypatch.setattr(followups.db, "get_leads_with_followup_counts", lambda: [lead])

    assert followups.leads_due() == []


def test_leads_due_excludes_lead_contacted_within_minimum_gap(monkeypatch):
    """A badly backlogged lead must not get consecutive steps on consecutive ticks."""
    lead = _lead(followup_count=1, created_hours_ago=500, last_contacted_hours_ago=2)
    monkeypatch.setattr(followups.db, "get_leads_with_followup_counts", lambda: [lead])

    # The created_at-based step-2 threshold (72h) passed long ago...
    assert lead["created_at"] + timedelta(hours=followups.CADENCE_HOURS[1]) < \
        datetime.now(timezone.utc)
    # ...but it was contacted 2 hours ago, so it is held back.
    assert followups.leads_due() == []


def test_leads_due_includes_backlogged_lead_once_minimum_gap_elapsed(monkeypatch):
    lead = _lead(followup_count=1, created_hours_ago=500, last_contacted_hours_ago=25)
    monkeypatch.setattr(followups.db, "get_leads_with_followup_counts", lambda: [lead])

    assert followups.leads_due() == [(lead, 2)]


def test_send_followup_generates_and_sends_email_and_records(monkeypatch):
    lead = _lead(followup_count=0, created_hours_ago=25, email="james@example.com", phone=None)
    fake_generate = MagicMock(return_value="Just checking in, James!")
    fake_send_email = MagicMock()
    fake_send_sms = MagicMock()
    fake_record = MagicMock()
    monkeypatch.setattr(followups.content_engine, "generate", fake_generate)
    monkeypatch.setattr(followups.email, "send_followup_email", fake_send_email)
    monkeypatch.setattr(followups.sms, "send_followup_sms", fake_send_sms)
    monkeypatch.setattr(followups.db, "record_followup", fake_record)

    followups.send_followup(lead, 1)

    fake_generate.assert_called_once_with(
        "follow_up", {"name": "James", "job": "Kitchen painting", "step": 1}
    )
    fake_send_email.assert_called_once_with(
        "james@example.com", "James", "Just checking in, James!"
    )
    fake_send_sms.assert_not_called()
    fake_record.assert_called_once_with(1, 1, "email", "Just checking in, James!")


def test_send_followup_uses_sms_when_no_email(monkeypatch):
    lead = _lead(followup_count=0, created_hours_ago=25, email=None, phone="+447123456789")
    monkeypatch.setattr(followups.content_engine, "generate", MagicMock(return_value="hi"))
    fake_send_email = MagicMock()
    fake_send_sms = MagicMock()
    monkeypatch.setattr(followups.email, "send_followup_email", fake_send_email)
    monkeypatch.setattr(followups.sms, "send_followup_sms", fake_send_sms)
    monkeypatch.setattr(followups.db, "record_followup", MagicMock())

    followups.send_followup(lead, 1)

    fake_send_email.assert_not_called()
    fake_send_sms.assert_called_once_with("+447123456789", "hi")


def test_send_followup_falls_back_to_generic_wording_when_fields_missing(monkeypatch):
    """None must never reach a customer-facing message."""
    lead = _lead(followup_count=0, created_hours_ago=25, name=None, job=None)
    fake_generate = MagicMock(return_value="Just checking in!")
    monkeypatch.setattr(followups.content_engine, "generate", fake_generate)
    monkeypatch.setattr(followups.email, "send_followup_email", MagicMock())
    monkeypatch.setattr(followups.sms, "send_followup_sms", MagicMock())
    monkeypatch.setattr(followups.db, "record_followup", MagicMock())

    followups.send_followup(lead, 1)

    fake_generate.assert_called_once_with(
        "follow_up", {"name": "there", "job": "your enquiry", "step": 1}
    )


def test_run_due_followups_sends_each_due_lead_and_returns_count(monkeypatch):
    due_lead = _lead(followup_count=0, created_hours_ago=25)
    monkeypatch.setattr(followups, "leads_due", lambda now=None: [(due_lead, 1)])
    fake_send = MagicMock()
    monkeypatch.setattr(followups, "send_followup", fake_send)

    result = followups.run_due_followups()

    assert result == 1
    fake_send.assert_called_once_with(due_lead, 1)


def test_run_due_followups_continues_after_a_failing_lead(monkeypatch):
    """One broken lead must not stall the whole queue."""
    failing_lead = _lead(lead_id=1, followup_count=0, created_hours_ago=25)
    ok_lead = _lead(lead_id=2, followup_count=0, created_hours_ago=25)
    monkeypatch.setattr(
        followups, "leads_due", lambda now=None: [(failing_lead, 1), (ok_lead, 1)]
    )

    def fake_send(lead, step):
        if lead["id"] == 1:
            raise RuntimeError("Twilio exploded")

    fake_send_mock = MagicMock(side_effect=fake_send)
    monkeypatch.setattr(followups, "send_followup", fake_send_mock)

    result = followups.run_due_followups()

    # The second lead was still attempted, and only the success counted.
    assert result == 1
    assert fake_send_mock.call_args_list == [call(failing_lead, 1), call(ok_lead, 1)]
