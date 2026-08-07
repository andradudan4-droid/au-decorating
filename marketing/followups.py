from datetime import datetime, timedelta, timezone

from marketing import content_engine, db, email, sms

CADENCE_HOURS = [24, 3 * 24, 7 * 24]  # step 1, 2, 3

# Minimum gap between two sends to the same lead. Safely under the 24h
# first-step cadence, so it never bites during normal hourly operation, but it
# stops a badly backlogged lead (e.g. the scheduler was off for a week) from
# receiving all three steps on three consecutive ticks.
MIN_GAP_HOURS = 20


def leads_due(now=None):
    now = now or datetime.now(timezone.utc)
    due = []
    for lead in db.get_leads_with_followup_counts():
        step_index = lead["followup_count"]
        if step_index >= len(CADENCE_HOURS):
            continue
        threshold = lead["created_at"] + timedelta(hours=CADENCE_HOURS[step_index])
        if now < threshold:
            continue
        last_contacted_at = lead.get("last_contacted_at")
        if last_contacted_at and now < last_contacted_at + timedelta(hours=MIN_GAP_HOURS):
            continue
        due.append((lead, step_index + 1))
    return due


def send_followup(lead, step):
    # Fall back to generic wording rather than leaking literal "None" into a
    # customer-facing message when the chat never captured a name or job.
    message = content_engine.generate(
        "follow_up",
        {
            "name": lead["name"] or "there",
            "job": lead["job"] or "your enquiry",
            "step": step,
        },
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
        # One failing lead must not stall the queue. send_followup runs before
        # record_followup, so an unhandled error would leave followup_count
        # unchanged - putting the same lead first in line every tick, forever,
        # and blocking every lead behind it.
        try:
            send_followup(lead, step)
            sent += 1
        except Exception as e:
            print(f"Follow-up failed for lead {lead.get('id')} step {step}: {e}")
    return sent
