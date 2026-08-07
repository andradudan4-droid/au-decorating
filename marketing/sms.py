import os
import re

from twilio.rest import Client


def to_e164(phone):
    """Normalise a UK phone number to E.164, which is what Twilio requires.

    Leads are captured in UK national format ("07123 456789"); Twilio rejects
    that with error 21211, so every number must be converted before sending.
    """
    if not phone:
        return phone
    cleaned = re.sub(r"[\s\-()]", "", phone)
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("0"):
        return "+44" + cleaned[1:]
    if cleaned.startswith("44"):
        return "+" + cleaned
    return cleaned


def send_followup_sms(to_phone, message):
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not (sid and token and from_number):
        print("Twilio not configured, skipping follow-up SMS")
        return

    client = Client(sid, token)
    client.messages.create(to=to_e164(to_phone), from_=from_number, body=message)
