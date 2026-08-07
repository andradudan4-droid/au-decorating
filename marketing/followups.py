from datetime import datetime, timedelta, timezone

from marketing import content_engine, db, email, sms

CADENCE_HOURS = [24, 3 * 24, 7 * 24]  # step 1, 2, 3


def leads_due(now=None):
    now = now or datetime.now(timezone.utc)
    due = []
    for lead in db.get_leads_with_followup_counts():
        step_index = lead["followup_count"]
        if step_index >= len(CADENCE_HOURS):
            continue
        threshold = lead["created_at"] + timedelta(hours=CADENCE_HOURS[step_index])
        if now >= threshold:
            due.append((lead, step_index + 1))
    return due


def send_followup(lead, step):
    message = content_engine.generate(
        "follow_up",
        {"name": lead["name"], "job": lead["job"], "step": step},
    )
    if lead["email"]:
        email.send_followup_email(lead["email"], lead["name"], message)
        channel = "email"
    else:
        sms.send_followup_sms(lead["phone"], message)
        channel = "sms"
    db.record_followup(lead["id"], step, channel, message)


def run_due_followups(now=None):
    sent = 0
    for lead, step in leads_due(now):
        send_followup(lead, step)
        sent += 1
    return sent
