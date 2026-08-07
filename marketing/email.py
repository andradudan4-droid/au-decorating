import os

import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_ADDRESS = "AU Decorating <leads@au-decorating.com>"
REPLY_TO = os.environ.get("NOTIFY_TO", "mehmet@au-decorating.com")


def send_followup_email(to_email, name, message):
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set, skipping follow-up email")
        return

    payload = {
        "from": FROM_ADDRESS,
        "to": [to_email],
        "reply_to": REPLY_TO,
        "subject": "Following up on your enquiry - AU Decorating",
        "text": message,
    }
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json=payload,
        timeout=15,
    )
    if response.status_code >= 300:
        print(f"Resend error (follow-up): {response.status_code} {response.text}")
