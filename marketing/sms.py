import os

from twilio.rest import Client


def send_followup_sms(to_phone, message):
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not (sid and token and from_number):
        print("Twilio not configured, skipping follow-up SMS")
        return

    client = Client(sid, token)
    client.messages.create(to=to_phone, from_=from_number, body=message)
