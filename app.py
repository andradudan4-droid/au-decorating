from flask import Flask, request, jsonify, render_template_string, session, Response
import os
import re
import uuid
import html
import base64
import threading
import time
import requests
from groq import Groq

app = Flask(__name__)
app.secret_key = "dev-secret-key-change-this-later"
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True
# Photos are resized in the browser before upload, so payloads are small.
# This is a safety cap to reject anything abnormally large.
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB

client = Groq(
    api_key=os.environ.get("GROQ_API_KEY")
)

# --- Email notification settings ---
# Render's free tier blocks direct SMTP (the old Gmail approach), so we
# use Resend instead, which sends over normal HTTPS - not blocked.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
NOTIFY_TO = os.environ.get("NOTIFY_TO", "mehmet@au-decorating.com")

# --- Photo upload settings ---------------------------------------------------
# Customers can attach photos of the job; these get emailed with the lead.
# Resizing happens in the browser, so what reaches us here is already small.
MAX_IMAGES_PER_SESSION = 6
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # per image, after base64 decode

# --- Contact-info extraction -------------------------------------------------
# The lead email is triggered purely by detecting a real phone number or email
# in the conversation (server-side), so we never depend on the AI to flag a lead.

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Matches UK mobile/landline numbers: 07xxx, 01xxx, 02xxx, +447xxx etc.
# No capturing groups so findall returns plain strings.
PHONE_RE = re.compile(r"(?<!\d)(?:\+44|0)\d[\d\s\-\.]{8,11}(?!\d)")
# Full UK postcode, e.g. PO5 3AB, SW1A 1AA, M1 1AE (space optional).
POSTCODE_RE = re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}\b")


def _customer_text(conversation):
    """All of the customer's own messages joined together."""
    return " ".join(
        m["content"] for m in conversation if m.get("role") == "user"
    )


def find_email(conversation):
    match = EMAIL_RE.search(_customer_text(conversation))
    return match.group(0) if match else None


def find_phone(conversation):
    text = _customer_text(conversation)
    for candidate in PHONE_RE.findall(text):
        # candidate is always a plain string - no capturing groups in PHONE_RE
        digits = re.sub(r"\D", "", candidate)
        # Reject 00-prefixed numbers (international dialling prefix, not a UK number)
        if digits.startswith("00"):
            continue
        if digits.startswith("44"):
            digits = "0" + digits[2:]
        if len(digits) == 11 and digits.startswith("0"):
            # Format as 07xxx xxxxxx (5 + 6)
            return f"{digits[:5]} {digits[5:]}"
    return None


def find_postcode(conversation):
    match = POSTCODE_RE.search(_customer_text(conversation))
    if not match:
        return None
    # Tidy to canonical form: uppercase, single space before the last 3 chars.
    raw = re.sub(r"\s+", "", match.group(0)).upper()
    return raw[:-3] + " " + raw[-3:]


def has_contact_info(conversation):
    """True only if we genuinely have a way to contact this person back."""
    return bool(find_email(conversation) or find_phone(conversation))


# Phrases that signal the customer is wrapping up - used only as a safety net so
# a lead is never lost if the assistant forgets its closing tag.
CLOSING_RE = re.compile(
    r"\b(no longer interested|not interested|no thanks|no thank you|"
    r"that'?s all|that'?s it|that'?s everything|nothing else|all good|"
    r"that'?s great thank|thanks that'?s|goodbye|bye for now|no more|"
    r"i'?m good|im good)\b",
    re.I,
)


def _looks_like_closing(text):
    return bool(CLOSING_RE.search(text or ""))


def _transcript(conversation):
    lines = []
    for msg in conversation:
        if msg["role"] == "user":
            lines.append(f"Customer: {msg['content']}")
        elif msg["role"] == "assistant":
            lines.append(f"Assistant: {msg['content']}")
    return "\n\n".join(lines)


# Prompt that turns a raw chat into a tidy, Checkatrade-style lead.
LEAD_SUMMARY_PROMPT = """You are turning a website chat into a clean lead for a
painting & decorating company owner. Read the conversation and output EXACTLY
these labelled lines and nothing else. Fill each in from what the customer
actually said; write "Not specified" if they didn't say. Keep each line short.

Name:
Job / work wanted:
Property type (domestic or commercial):
Approx budget (in GBP £; note if it's a total or a per-room / per-m2 rate):
Preferred timing:
Urgency (1-5 where 1=no rush, 5=urgent - infer from what they said):
Location / area:
Other notes:"""


def summarise_lead(conversation):
    """Uses the model to extract a tidy, organised lead from the chat."""
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": LEAD_SUMMARY_PROMPT},
                {"role": "user", "content": _transcript(conversation)},
            ],
            max_tokens=250,
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"Lead summary failed: {e}")
        return None


def _post_resend(subject, text, html_body=None, attachments=None):
    """Low-level send via Resend's HTTPS API (Render's free tier blocks SMTP).

    Sends a plain-text part plus an optional HTML part. `attachments` is a list
    of dicts like {"filename": ..., "b64": <base64>}.
    """
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set, skipping email")
        return

    payload = {
        # Sent from the verified au-decorating.com domain (SPF/DKIM set up in
        # Resend), so mail lands in the inbox, not spam.
        "from": "AU Decorating Website <leads@au-decorating.com>",
        "to": [NOTIFY_TO],
        "subject": subject,
        "text": text,
    }
    if html_body:
        payload["html"] = html_body
    if attachments:
        # Resend expects: [{"filename": ..., "content": <base64 string>}]
        payload["attachments"] = [
            {"filename": a["filename"], "content": a["b64"]} for a in attachments
        ]

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json=payload,
            timeout=15,
        )
        if response.status_code >= 300:
            print(f"Resend error: {response.status_code} {response.text}")
    except Exception as e:
        print(f"Failed to send email: {e}")


def _parse_summary(structured):
    """Turn the model's labelled summary lines into a dict keyed by lowercase label."""
    out = {}
    if not structured:
        return out
    for line in structured.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            out[key.strip().lower()] = val.strip()
    return out


def _lead_fields(conversation):
    """A tidy, ordered set of lead fields - reliable regex first, AI summary for the rest."""
    s = _parse_summary(summarise_lead(conversation))

    def pick(*keys):
        for k in keys:
            v = s.get(k)
            if v and v.lower() not in ("not specified", "not provided", "n/a", "none", "-"):
                return v
        return None

    return {
        "Name": pick("name"),
        "Phone": find_phone(conversation),
        "Email": find_email(conversation),
        "Postcode": find_postcode(conversation),
        "Area": pick("location / area", "location", "area"),
        "Job": pick("job / work wanted", "job", "work wanted"),
        "Property": pick("property type (domestic or commercial)", "property type", "property"),
        "Budget": pick("approx budget", "budget"),
        "Preferred timing": pick("preferred timing", "timing"),
        "Urgency": pick("urgency (1-5 where 1=no rush, 5=urgent - infer from what they said)", "urgency"),
        "Notes": pick("other notes", "notes"),
    }


def _row(label, value):
    if not value:
        return ""
    return (
        '<tr>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #eee;color:#8a8a8a;'
        f'font-size:13px;white-space:nowrap;vertical-align:top;width:130px">{html.escape(label)}</td>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #eee;color:#1a1a1a;'
        f'font-size:14px;font-weight:600">{html.escape(str(value))}</td>'
        '</tr>'
    )


def _transcript_html(conversation):
    rows = []
    for msg in conversation:
        if msg["role"] == "user":
            who, color, bg = "Customer", "#0a0a0a", "#f5f4f0"
        elif msg["role"] == "assistant":
            who, color, bg = "AU Assistant", "#9a7d1a", "#ffffff"
        else:
            continue
        text = html.escape(msg["content"]).replace("\n", "<br>")
        rows.append(
            f'<div style="margin:0 0 12px">'
            f'<div style="font-size:11px;letter-spacing:.05em;text-transform:uppercase;'
            f'color:{color};font-weight:700;margin-bottom:4px">{who}</div>'
            f'<div style="background:{bg};border:1px solid #ececec;border-radius:10px;'
            f'padding:11px 14px;font-size:14px;color:#2a2a2a;line-height:1.5">{text}</div>'
            f'</div>'
        )
    return "".join(rows)


def _urgency_badge(urgency_str):
    """Return an HTML urgency badge based on the 1-5 score."""
    if not urgency_str:
        return ""
    # Extract just the digit if present
    m = re.search(r"[1-5]", str(urgency_str))
    if not m:
        return ""
    score = int(m.group(0))
    colours = {
        1: ("#e8f5e9", "#2e7d32", "1 — No rush"),
        2: ("#f1f8e9", "#558b2f", "2 — Low"),
        3: ("#fff8e1", "#f57f17", "3 — Moderate"),
        4: ("#fff3e0", "#e65100", "4 — Fairly urgent"),
        5: ("#ffebee", "#b71c1c", "5 — URGENT — reply ASAP"),
    }
    bg, fg, label = colours.get(score, ("#f5f5f5", "#555", str(score)))
    return (
        f'<div style="margin:0 0 20px">'
        f'<div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;'
        f'color:#999;font-weight:700;margin-bottom:6px">Urgency</div>'
        f'<span style="display:inline-block;background:{bg};color:{fg};border:1px solid {fg};'
        f'border-radius:999px;padding:5px 14px;font-size:13px;font-weight:700">'
        f'{label}</span></div>'
    )


def _lead_email_html(fields, conversation, image_count):
    urgency_val = fields.pop("Urgency", None)
    rows = "".join(_row(k, v) for k, v in fields.items())
    photos_line = ""
    if image_count:
        photos_line = (
            '<p style="margin:0 0 20px;font-size:14px;color:#1a1a1a">'
            f'\U0001F4CE <strong>{image_count} photo(s)</strong> attached to this email.</p>'
        )
    urgency_html = _urgency_badge(urgency_val)
    return (
        '<!DOCTYPE html><html><body style="margin:0;background:#f0efea;padding:24px;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:620px;margin:0 auto;background:#fff;border-radius:14px;'
        'overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.07)">'
        '<div style="background:#0a0a0a;padding:24px 28px">'
        '<div style="color:#D4AF37;font-size:12px;letter-spacing:.18em;text-transform:uppercase;'
        'font-weight:700">AU Decorating</div>'
        '<div style="color:#fff;font-size:21px;font-weight:700;margin-top:5px">'
        'New enquiry from your website</div></div>'
        '<div style="padding:26px 28px">'
        '<p style="margin:0 0 20px;font-size:14px;color:#666">'
        'Here are the details captured by your website assistant:</p>'
        f'{urgency_html}'
        f'{photos_line}'
        '<table style="width:100%;border-collapse:collapse;border:1px solid #eee;'
        f'border-radius:8px;overflow:hidden;margin-bottom:28px">{rows}</table>'
        '<div style="font-size:12px;letter-spacing:.05em;text-transform:uppercase;'
        'color:#999;font-weight:700;margin-bottom:14px">Full conversation</div>'
        f'{_transcript_html(conversation)}'
        '</div>'
        '<div style="background:#faf9f6;padding:16px 28px;border-top:1px solid #eee;'
        'font-size:12px;color:#aaa">Sent automatically by the AU Decorating website assistant. '
        'AU Decorating Limited &middot; Company No. 14912651 &middot; '
        '554 Hertford Road, Enfield, EN3 5ST</div>'
        '</div></body></html>'
    )


def send_lead_email(conversation, images=None):
    """Emails a tidy, professional lead summary (plus transcript and any photos)."""
    images = images or []
    fields = _lead_fields(conversation)
    transcript = _transcript(conversation)

    # Plain-text fallback for any client that won't render HTML.
    text_lines = ["NEW LEAD - AU Decorating", "========================"]
    for k, v in fields.items():
        if v:
            text_lines.append(f"{k}: {v}")
    if images:
        text_lines.append(f"Photos attached: {len(images)}")
    text_lines += ["========================", "", "Full conversation:", "", transcript]
    text_body = "\n".join(text_lines)

    html_body = _lead_email_html(fields, conversation, len(images))

    # Scannable subject: urgency flag + "New lead - Name · Area · 07..."
    urgency_raw = fields.get("Urgency", "")
    urgency_m = re.search(r"[1-5]", str(urgency_raw)) if urgency_raw else None
    urgency_score = int(urgency_m.group(0)) if urgency_m else 0
    urgent_prefix = "🔴 URGENT — " if urgency_score >= 5 else ("🟠 " if urgency_score >= 4 else "")

    contact = fields.get("Phone") or fields.get("Email") or "no number yet"
    bits = [b for b in (fields.get("Name"), fields.get("Area") or fields.get("Postcode")) if b]
    subject = urgent_prefix + "New lead - " + (" \u00b7 ".join(bits + [contact]) if bits else contact)
    _post_resend(
        subject,
        text_body,
        html_body=html_body,
        attachments=images,
    )


def send_photo_followup(conversation, images):
    """If a photo arrives after the lead email was already sent, forward it on
    so it can't get lost."""
    if not images:
        return

    phone = find_phone(conversation) or "Not provided"
    email = find_email(conversation) or "Not provided"
    postcode = find_postcode(conversation) or "Not provided"

    text_body = (
        "ADDITIONAL PHOTO(S) - AU Decorating\n"
        "This relates to a lead you've already been emailed about.\n"
        f"Phone: {phone}\nEmail: {email}\nPostcode: {postcode}\n"
        f"Photos attached: {len(images)}\n"
    )
    html_body = (
        '<!DOCTYPE html><html><body style="margin:0;background:#f0efea;padding:24px;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:620px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;'
        'box-shadow:0 2px 12px rgba(0,0,0,.07)">'
        '<div style="background:#0a0a0a;padding:22px 28px">'
        '<div style="color:#D4AF37;font-size:12px;letter-spacing:.18em;text-transform:uppercase;'
        'font-weight:700">AU Decorating</div>'
        '<div style="color:#fff;font-size:19px;font-weight:700;margin-top:5px">'
        'More photos for an existing lead</div></div>'
        '<div style="padding:24px 28px">'
        f'<p style="margin:0 0 18px;font-size:14px;color:#666">This relates to a lead you\'ve '
        f'already been emailed about. <strong>{len(images)} new photo(s)</strong> attached below.</p>'
        '<table style="width:100%;border-collapse:collapse;border:1px solid #eee;border-radius:8px;'
        f'overflow:hidden">{_row("Phone", phone)}{_row("Email", email)}{_row("Postcode", postcode)}</table>'
        '</div></div></body></html>'
    )
    _post_resend(f"Photo added - lead: {phone}", text_body, html_body=html_body, attachments=images)

SYSTEM_PROMPT = """
You are the virtual assistant for AU Decorating Ltd, a painting and decorating
company based in Portsmouth, run by Mehmet Yildiz. You're the first point of
contact for new enquiries.

About the business:
- 10/10 rating from 45+ verified reviews on Checkatrade
- Services: interior & exterior painting, decorating, flooring, tiling, paving,
  driveway installation, anti-vandal coatings. Domestic and commercial.
- Free estimates — pricing depends on the job, so there are no fixed prices.
- Available every day, flexible scheduling, 24-hour call-out.
- Insurance work undertaken.
- Company No. 14912651 (AU Decorating Limited, incorporated 3 June 2023)

YOUR TONE — this is critical:
Write like a friendly local tradesperson firing off a quick text, NOT like a
customer service chatbot. Keep it short, warm and direct. No filler phrases
like "Great question!", "I'd be happy to help!", "Of course!", "Certainly!",
"I'm happy to assist you today", or any variation. Just get straight to it.
One or two sentences max per message. Ask one thing at a time and wait for
the answer before moving on. Never use bullet points or long paragraphs in chat.

Bad example: "Great! I'd be happy to help you get a free, no-obligation quote
today! Could you please describe the nature of the work you're looking to have
done at your property?"
Good example: "Nice one — what's the job? Painting, tiling, flooring,
something else?"

CONVERSATION FLOW — work through these one at a time, in order:
1. Find out what the job is (painting, tiling, flooring, exterior, etc.)
2. Get a bit more detail on the scope (how many rooms, rough size, any specifics)
3. Ask if it's a domestic or commercial property
4. Offer to take photos via the paperclip in the chat — or they can arrange a
   visit instead. Say something like: "Got a couple of photos of the job? You can
   drop them in here with the paperclip — makes it easier for Mehmet to quote.
   Or we can just arrange a visit if that's easier."
5. Ask for a rough budget — frame it as helpful for tailoring the quote. If they
   don't want to say, that's fine, just move on. When they do give a figure,
   note whether it's their total budget or a per-room / per-m2 rate, and keep
   it in pounds.
   BUDGET SANITY: If their budget seems very low for what they've described
   (e.g. £800 for 30m² of marble tiling in a commercial space), gently flag it
   without being blunt. Something like: "Just so you know, a job like that would
   typically run to a fair bit more than that — Mehmet can give you an accurate
   figure when he takes a look, but worth being aware before we get too far."
   Then carry on collecting the rest.
6. Ask how urgent it is — something like: "How soon are you looking to get this
   done? Is it fairly urgent or no particular rush?" Their answer will be passed
   to Mehmet so he knows how quickly to get back.
7. Get their name, postcode or area, and best contact number or email.
   Quickly repeat the number or email back to check you've typed it right.
8. Once you've worked through everything above, wrap up warmly and confirm
   their enquiry has been sent over to Mehmet, who'll be in touch about a free
   estimate - usually the same day.

IMPORTANT - WHEN TO FINISH: Only add the [[READY]] signal once you have ASKED
about ALL of these: the job, rough size/scope, domestic or commercial, offered
photos or a visit, budget, how urgent it is, their name, their postcode/area,
and their contact details (and confirmed them). It's completely fine if they
decline to answer some - but you must have ASKED each one first. Do NOT add
[[READY]] just because they gave a phone number - keep gently gathering the
rest until the whole list is covered, THEN wrap up and add [[READY]].

[[READY]] is a hidden internal tag stripped automatically — the customer never
sees it. Put it on its own line at the very end of the final wrap-up message.
Never include it mid-conversation or before you're genuinely done.
"""

all_conversations = {}
notified_sessions = set()
chat_activity = {}  # session_id -> [timestamps] for rate limiting
session_images = {}  # session_id -> list of {filename, content_type, b64}


def _decode_image_data_url(data_url):
    """Validate and decode a 'data:image/...;base64,...' string from the browser.

    Returns {"filename", "content_type", "b64"} or None if it's not a valid,
    allowed, reasonably-sized image.
    """
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None
    try:
        header, b64 = data_url.split(",", 1)
    except ValueError:
        return None
    if ";base64" not in header:
        return None

    content_type = header[len("data:"):].split(";", 1)[0].lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        return None

    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None

    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
    return {
        "filename": f"job-photo-{uuid.uuid4().hex[:8]}.{ext}",
        "content_type": content_type,
        # Re-encode cleanly (no stray whitespace) for Resend.
        "b64": base64.b64encode(raw).decode("ascii"),
    }

BASE_STYLE = """
<link rel="icon" type="image/png" href="/static/images/logo.png">
<meta name="theme-color" content="#14110d">
<meta property="og:type" content="website">
<meta property="og:site_name" content="AU Decorating Ltd">
<meta property="og:title" content="AU Decorating Ltd - Portsmouth Painters & Decorators">
<meta property="og:description" content="10/10 rated painters & decorators in Portsmouth. Interior & exterior painting, flooring, tiling, paving & driveways. Free, no-obligation quotes.">
<meta property="og:url" content="https://au-decorating.com">
<meta property="og:image" content="https://au-decorating.com/static/images/terrace-after.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&display=swap" rel="stylesheet">
<!-- Privacy-friendly analytics: create a free plausible.io account, add domain au-decorating.com -->
<script defer data-domain="au-decorating.com" src="https://plausible.io/js/script.js"></script>
<style>
  :root{
    --bg:#14110d; --bg2:#1c1812; --panel:#0f0c08; --ink:#efe9dd; --mut:#a99f8c;
    --gold:#c9a24b; --gold-soft:#e7c977; --line:rgba(201,162,75,.26);
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
    line-height:1.6;-webkit-font-smoothing:antialiased;}
  a{color:var(--gold)}
  img{max-width:100%;display:block}
  .serif{font-family:'Playfair Display',Georgia,serif}

  /* nav */
  nav{position:sticky;top:0;z-index:50;background:rgba(15,12,8,.92);
    backdrop-filter:saturate(140%) blur(8px);border-bottom:1px solid var(--line);
    display:flex;justify-content:space-between;align-items:center;
    padding:14px 26px;flex-wrap:wrap;gap:8px;}
  nav .brand{font-family:'Playfair Display',serif;color:var(--gold);font-size:21px;
    font-weight:700;letter-spacing:.16em;text-decoration:none;display:flex;align-items:center;gap:11px;}
  nav .brand .mark{width:30px;height:30px;border-radius:50%;object-fit:cover;
    border:1px solid var(--line);}
  nav .links{display:flex;flex-wrap:wrap;align-items:center;gap:22px}
  nav .links a{color:var(--ink);text-decoration:none;font-size:13.5px;letter-spacing:.04em;opacity:.85}
  nav .links a:hover{color:var(--gold);opacity:1}
  nav .links .navcta{border:1px solid var(--gold);color:var(--gold);padding:8px 15px;border-radius:999px;opacity:1}
  nav .links .navcta:hover{background:var(--gold);color:var(--panel)}

  /* hero */
  .hero{position:relative;min-height:86vh;display:flex;align-items:center;justify-content:center;
    text-align:center;padding:90px 24px;
    background:linear-gradient(rgba(13,10,7,.55),rgba(13,10,7,.86)),url('/static/images/arched-after.jpg') center/cover no-repeat;}
  .hero .inner{max-width:780px}
  .eyebrow{font-size:12px;letter-spacing:.32em;text-transform:uppercase;color:var(--gold);font-weight:700}
  .hero h1{font-family:'Playfair Display',serif;font-weight:600;font-size:clamp(34px,6vw,58px);
    line-height:1.06;margin:18px 0 16px;color:#fff;}
  .hero p{font-size:clamp(15px,2.2vw,19px);color:#e9e2d4;max-width:560px;margin:0 auto 30px}
  .pill{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--gold);
    color:var(--gold-soft);padding:7px 16px;border-radius:999px;font-size:13px;letter-spacing:.03em;}
  .btns{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-top:8px}
  .btn{display:inline-block;background:var(--gold);color:#17130c;padding:14px 26px;border-radius:999px;
    font-weight:700;text-decoration:none;font-size:15px;letter-spacing:.02em;transition:transform .15s,background .15s}
  .btn:hover{background:var(--gold-soft);transform:translateY(-1px)}
  .btn-ghost{background:transparent;color:var(--gold);border:1px solid var(--gold)}
  .btn-ghost:hover{background:rgba(201,162,75,.12);color:var(--gold-soft)}

  /* sections */
  .band{padding:78px 24px}
  .band--panel{background:var(--bg2);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
  .wrap{max-width:1080px;margin:0 auto}
  .wrap--narrow{max-width:760px}
  .head{margin-bottom:38px}
  .head .eyebrow{display:block;margin-bottom:12px}
  h2.title{font-family:'Playfair Display',serif;font-weight:600;font-size:clamp(26px,4vw,40px);
    margin:0 0 10px;color:var(--ink);line-height:1.12}
  .sub{color:var(--mut);max-width:620px;margin:0;font-size:16px}
  .rule{width:46px;height:2px;background:var(--gold);margin:0 0 22px}

  /* before/after slider */
  .ba-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:26px}
  .ba-card .cap{margin:12px 2px 0;font-size:13.5px;color:var(--mut)}
  .ba-card .cap b{color:var(--ink);font-weight:600}
  .ba{position:relative;--pos:50%;border-radius:14px;overflow:hidden;border:1px solid var(--line);
    background:#000;user-select:none;touch-action:pan-y}
  .ba .layer{position:absolute;inset:0;width:100%;height:100%}
  .ba .layer img{width:100%;height:100%;object-fit:cover}
  .ba .before{clip-path:inset(0 calc(100% - var(--pos)) 0 0)}
  .ba .spacer{position:relative;width:100%}
  .ba .spacer img{width:100%;height:100%;object-fit:cover;opacity:0}
  .ba .tag{position:absolute;top:12px;z-index:4;background:rgba(15,12,8,.78);color:var(--gold-soft);
    border:1px solid var(--line);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
    padding:5px 10px;border-radius:999px}
  .ba .tag.b{left:12px} .ba .tag.a{right:12px}
  .ba .line{position:absolute;top:0;bottom:0;left:var(--pos);width:2px;background:var(--gold);z-index:5;
    transform:translateX(-1px);pointer-events:none}
  .ba .knob{position:absolute;top:50%;left:var(--pos);z-index:6;width:42px;height:42px;border-radius:50%;
    background:var(--gold);transform:translate(-50%,-50%);pointer-events:none;
    display:flex;align-items:center;justify-content:center;box-shadow:0 4px 14px rgba(0,0,0,.5)}
  .ba .knob:before{content:'\\2039\\203A';color:#17130c;font-weight:700;font-size:18px;letter-spacing:1px}
  .ba input[type=range]{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;
    cursor:ew-resize;z-index:7}

  /* work gallery */
  .work-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}
  figure.work{margin:0;border-radius:12px;overflow:hidden;border:1px solid var(--line);background:#000;position:relative}
  figure.work img{width:100%;height:100%;aspect-ratio:4/5;object-fit:cover;transition:transform .5s ease;display:block}
  figure.work:hover img{transform:scale(1.05)}
  figure.work figcaption{position:absolute;left:0;right:0;bottom:0;
    background:linear-gradient(transparent,rgba(11,8,5,.86));
    color:#f1eadc;font-size:12.5px;padding:26px 12px 11px;letter-spacing:.02em}
  figure.work.tall img{aspect-ratio:4/6}

  /* cards / services */
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px;position:relative}
  .card .n{font-family:'Playfair Display',serif;color:var(--gold);font-size:14px;letter-spacing:.08em}
  .card h3{margin:8px 0 8px;font-size:18px;color:var(--ink)}
  .card p{margin:0;color:var(--mut);font-size:14.5px}

  /* testimonials */
  .tgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}
  .tcard{background:var(--panel);border:1px solid var(--line);border-left:2px solid var(--gold);
    border-radius:12px;padding:22px}
  .stars{color:var(--gold);letter-spacing:3px;font-size:14px;margin-bottom:10px}
  .tcard p{margin:0 0 12px;color:#ddd5c6;font-size:14.5px;font-style:italic}
  .tcard .who{color:var(--mut);font-size:12.5px;font-style:normal;letter-spacing:.04em}

  /* cta */
  .cta{text-align:center}
  .cta h2{margin-bottom:14px}

  /* contact box */
  .contact-box{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:28px}
  .contact-box p{margin:9px 0;color:var(--ink)}
  .contact-box strong{color:var(--gold)}

  /* prose (privacy) */
  .prose{color:#d8d0c1;font-size:15px;line-height:1.75}
  .prose h3{color:var(--gold);font-family:'Playfair Display',serif;font-weight:600;margin:26px 0 6px;font-size:19px}
  .prose a{color:var(--gold-soft)}

  /* footer */
  footer{background:var(--panel);border-top:1px solid var(--line);text-align:center;padding:42px 24px;color:var(--mut);font-size:13.5px}
  footer .flogo{width:84px;margin:0 auto 16px;opacity:.95}
  footer a{color:var(--gold);text-decoration:none}
  footer .areas{font-size:12.5px;opacity:.85;max-width:620px;margin:10px auto 0}

  @media(max-width:640px){
    nav{padding:12px 16px}
    nav .links{gap:14px}
    .band{padding:54px 18px}
    .hero{min-height:78vh;padding:72px 18px}
    .work-grid{grid-template-columns:1fr 1fr;gap:11px}
  }
</style>
"""

NAV = """
<nav>
  <a class="brand" href="/">AU DECORATING</a>
  <div class="links">
    <a href="/#work">Our work</a>
    <a href="/#services">Services</a>
    <a href="/gallery">Gallery</a>
    <a href="/#reviews">Reviews</a>
    <a class="navcta" href="https://wa.me/447376204980" target="_blank">Free quote</a>
  </div>
</nav>
"""

FOOTER = """
<footer>
  <img class="flogo" src="/static/images/logo.png" alt="AU Decorating">
  <div style="color:var(--ink);letter-spacing:.04em;margin-bottom:6px;">AU Decorating Ltd &middot; Portsmouth</div>
  <div>Free estimates every day &middot; flexible scheduling &middot; 24-hour call-out</div>
  <div class="areas">Covering Portsmouth, Southsea, Fareham, Gosport, Havant, Waterlooville, Cosham, Portchester &amp; surrounding areas.</div>
  <div style="margin-top:14px;"><a href="tel:+447376204980">07376 204980</a> &nbsp;&middot;&nbsp; <a href="/privacy">Privacy Policy</a></div>
  <div style="margin-top:10px;font-size:12px;opacity:.6;">
    AU Decorating Limited &middot; Company No. 14912651 &middot; Registered in England &amp; Wales
    &nbsp;&middot;&nbsp;
    <a href="https://www.checkatrade.com/trades/audecoratinglimited" target="_blank" rel="noopener">Checkatrade</a>
    &nbsp;&middot;&nbsp;
    <a href="https://share.google/5aW935xz7J23tAKej" target="_blank" rel="noopener">Google Reviews</a>
  </div>
</footer>
"""

WIDGET_INCLUDE = '<script src="/widget.js"></script>'

SLIDER_JS = """
<script>
(function(){
  function clamp(v){return Math.max(0,Math.min(100,v));}
  document.querySelectorAll('.ba').forEach(function(ba){
    var r=ba.querySelector('input[type=range]');
    function upd(){ ba.style.setProperty('--pos', r.value+'%'); }
    r.addEventListener('input',upd); upd();
  });
})();
</script>
"""

def _ba(before, after, label, ratio):
    return ('<div class="ba-card"><div class="ba" style="--pos:50%;">'
            '<div class="spacer"><img src="/static/images/' + after + '" style="aspect-ratio:' + ratio + '" alt=""></div>'
            '<div class="layer"><img src="/static/images/' + after + '" alt="' + label + ' - after"></div>'
            '<div class="layer before"><img src="/static/images/' + before + '" alt="' + label + ' - before"></div>'
            '<span class="tag b">Before</span><span class="tag a">After</span>'
            '<div class="line"></div><div class="knob"></div>'
            '<input type="range" min="0" max="100" value="50" aria-label="Drag to compare before and after">'
            '</div><div class="cap"><b>' + label + '</b></div></div>')

HOME_PAGE = """
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>AU Decorating Ltd - Portsmouth Painters &amp; Decorators</title>
<meta name="description" content="AU Decorating Ltd - 10/10 rated painters and decorators in Portsmouth. Interior and exterior painting, flooring, tiling, paving and driveways. Free quotes, every day.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": ["LocalBusiness", "HousePainter"],
  "name": "AU Decorating Ltd",
  "image": "https://au-decorating.com/static/images/terrace-after.jpg",
  "logo": "https://au-decorating.com/static/images/logo.png",
  "url": "https://au-decorating.com",
  "telephone": "+447376204980",
  "email": "mehmet@au-decorating.com",
  "priceRange": "\u00a3\u00a3",
  "description": "10/10 rated painters and decorators in Portsmouth. Interior and exterior painting, decorating, flooring, tiling, paving, driveways and anti-vandal coatings. Domestic and commercial. Free, no-obligation quotes.",
  "address": {"@type": "PostalAddress", "addressLocality": "Portsmouth", "addressRegion": "Hampshire", "addressCountry": "GB"},
  "areaServed": [
    {"@type": "City", "name": "Portsmouth"}, {"@type": "City", "name": "Southsea"},
    {"@type": "City", "name": "Fareham"}, {"@type": "City", "name": "Gosport"},
    {"@type": "City", "name": "Havant"}, {"@type": "City", "name": "Waterlooville"},
    {"@type": "City", "name": "Cosham"}, {"@type": "City", "name": "Portchester"}
  ],
  "aggregateRating": {"@type": "AggregateRating", "ratingValue": "10", "bestRating": "10", "ratingCount": "45"},
  "sameAs": ["https://www.checkatrade.com/trades/audecoratinglimited"]
}
</script>
""" + BASE_STYLE + """</head><body>
""" + NAV + """

<header class="hero">
  <div class="inner">
    <span class="pill">&#9733; 10/10 from 45+ reviews on Checkatrade</span>
    <h1 class="serif">A flawless finish,<br>inside and out.</h1>
    <p>Painters &amp; decorators in Portsmouth &mdash; interior &amp; exterior painting, flooring, tiling, paving and driveways. Free, no-obligation quotes.</p>
    <div class="btns">
      <a class="btn" href="https://wa.me/447376204980" target="_blank">Get a free quote</a>
      <a class="btn btn-ghost" href="tel:+447376204980">Call 07376 204980</a>
    </div>
  </div>
</header>

<section class="band" id="work">
  <div class="wrap">
    <div class="head">
      <span class="eyebrow">The transformation</span>
      <div class="rule"></div>
      <h2 class="title serif">Drag to see the difference</h2>
      <p class="sub">Real jobs around Portsmouth. Pull the slider across each one to reveal the before and after.</p>
    </div>
    <div class="ba-grid">
""" + _ba("terrace-before.jpg","terrace-after.jpg","Victorian terrace, full exterior repaint","3/4") + \
       _ba("arched-before.jpg","arched-after.jpg","Render repair &amp; exterior repaint","3/4") + \
       _ba("kitchen-before.jpg","kitchen-after.jpg","Kitchen renovation &amp; splashback","16/9") + """
    </div>
  </div>
</section>

<section class="band band--panel" id="gallery-preview">
  <div class="wrap">
    <div class="head">
      <span class="eyebrow">Recent work</span>
      <div class="rule"></div>
      <h2 class="title serif">Finished to a standard worth showing</h2>
      <p class="sub">A selection of homes and commercial spaces across the area.</p>
    </div>
    <div class="work-grid">
      <figure class="work tall"><img src="/static/images/work-terrace-bay.jpg" alt="Painted Victorian bay, grey and white"><figcaption>Victorian terrace &middot; Portsmouth</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-cottage-1.jpg" alt="Rendered cottage exterior"><figcaption>Rendered cottage exterior</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-salon-corridor.jpg" alt="Salon commercial fit-out"><figcaption>House of Glam &middot; salon fit-out</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-burgundy-1.jpg" alt="Heritage red feature wall"><figcaption>Feature wall &middot; heritage red</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-cottage-porch.jpg" alt="Porch and timber in satin black"><figcaption>Porch &amp; timber &middot; satin black</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-green.jpg" alt="Sage green living room repaint"><figcaption>Living room &middot; full repaint</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-salon-panel.jpg" alt="Slat panel feature wall"><figcaption>Feature slat panelling</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-terrace-relief.jpg" alt="Restored painted period detailing"><figcaption>Restored period detailing</figcaption></figure>
    </div>
    <p style="margin-top:24px"><a href="/gallery">See the full gallery &rarr;</a></p>
  </div>
</section>

<section class="band" id="services">
  <div class="wrap">
    <div class="head">
      <span class="eyebrow">What we do</span>
      <div class="rule"></div>
      <h2 class="title serif">Services</h2>
      <p class="sub">Domestic and commercial &mdash; from a single feature wall to a full fit-out.</p>
    </div>
    <div class="cards">
      <div class="card"><div class="n">01</div><h3>Interior painting</h3><p>Walls, ceilings and woodwork, prepped properly and finished clean.</p></div>
      <div class="card"><div class="n">02</div><h3>Exterior &amp; render</h3><p>Weatherproof finishes, render repair and full elevations.</p></div>
      <div class="card"><div class="n">03</div><h3>Flooring &amp; tiling</h3><p>Wood-effect, tile and splashbacks for kitchens and bathrooms.</p></div>
      <div class="card"><div class="n">04</div><h3>Paving &amp; driveways</h3><p>Outdoor paving and driveway installation.</p></div>
      <div class="card"><div class="n">05</div><h3>Commercial fit-out</h3><p>Salons, shops and offices &mdash; painting, flooring and panelling.</p></div>
      <div class="card"><div class="n">06</div><h3>Insurance work</h3><p>Repairs and redecoration handled as part of insurance claims.</p></div>
    </div>
  </div>
</section>

<section class="band" id="about">
  <div class="wrap" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:40px;align-items:center;">
    <div>
      <span class="eyebrow">Meet the team</span>
      <div class="rule"></div>
      <h2 class="title serif">Run by Mehmet, not a call centre</h2>
      <p class="sub" style="margin-bottom:14px">AU Decorating is owned and run by Mehmet Yildiz, a Portsmouth-based painter &amp; decorator. When you get in touch, you deal directly with the person doing the work &mdash; not a faceless company.</p>
      <p class="sub">Established in 2023, AU Decorating has earned a 10/10 rating from 45+ verified Checkatrade reviews for turning up on time, working tidily, and finishing to a high standard on both domestic and commercial jobs.</p>
    </div>
    <div style="text-align:center">
      <img src="/static/images/mehmet.jpg" onerror="this.onerror=null;this.src='/static/images/logo.png';this.style.maxWidth='220px';" alt="Mehmet Yildiz, AU Decorating" style="width:100%;max-width:360px;border-radius:16px;border:1px solid var(--line)">
      <div style="color:var(--mut);font-size:13px;margin-top:10px">Mehmet Yildiz &middot; Founder</div>
    </div>
  </div>
</section>

<section class="band band--panel" id="reviews">
  <div class="wrap">
    <div class="head">
      <span class="eyebrow">10/10 on Checkatrade</span>
      <div class="rule"></div>
      <h2 class="title serif">What customers say</h2>
      <p class="sub">A 10/10 rating from 45+ verified reviews.</p>
    </div>
    <div class="tgrid">
      <div class="tcard"><div class="stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div><p>"Repaired cracked plaster and water damage, then redecorated quickly and tidily &mdash; even did a few extra small jobs at no extra charge."</p><div class="who">Verified Checkatrade review</div></div>
      <div class="tcard"><div class="stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div><p>"A repeat customer &mdash; hallway decorating was efficient, great value, and finished with an excellent clean-up."</p><div class="who">Verified Checkatrade review</div></div>
      <div class="tcard"><div class="stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div><p>"Careful prep work made a big visible difference. Arrived on time and left everything tidy."</p><div class="who">Verified Checkatrade review</div></div>
      <div class="tcard"><div class="stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div><p>"In touch from the first enquiry through to completion, on time, polite and friendly throughout."</p><div class="who">Verified Checkatrade review</div></div>
    </div>
    <p style="margin-top:22px">
      <a href="https://www.checkatrade.com/trades/audecoratinglimited" target="_blank">See all reviews on Checkatrade &rarr;</a>
      &nbsp;&nbsp;&middot;&nbsp;&nbsp;
      <a href="https://share.google/5aW935xz7J23tAKej" target="_blank">Google Reviews &rarr;</a>
    </p>
  </div>
</section>

<section class="band cta">
  <div class="wrap wrap--narrow">
    <span class="eyebrow">Free, no obligation</span>
    <h2 class="title serif" style="margin-top:12px">Tell us about your project</h2>
    <p class="sub" style="margin:0 auto 24px">Message us a few details (photos welcome) and we'll get back with a quote. Use the chat in the corner, WhatsApp, or call.</p>
    <div class="btns">
      <a class="btn" href="https://wa.me/447376204980" target="_blank">Quote on WhatsApp</a>
      <a class="btn btn-ghost" href="tel:+447376204980">Call us now</a>
    </div>
  </div>
</section>
""" + FOOTER + SLIDER_JS + WIDGET_INCLUDE + """
</body></html>
"""

SERVICES_PAGE = """
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Services - AU Decorating Ltd</title>
<meta name="viewport" content="width=device-width, initial-scale=1">""" + BASE_STYLE + """</head><body>
""" + NAV + """
<section class="band">
  <div class="wrap">
    <div class="head">
      <span class="eyebrow">What we do</span><div class="rule"></div>
      <h2 class="title serif">Our services</h2>
      <p class="sub">Painting, decorating and finishing across Portsmouth and the surrounding area.</p>
    </div>
    <div class="cards">
      <div class="card"><div class="n">01</div><h3>Interior painting</h3><p>Walls, ceilings and woodwork, finished to a high standard.</p></div>
      <div class="card"><div class="n">02</div><h3>Exterior painting</h3><p>Weatherproof finishes that protect and refresh your property.</p></div>
      <div class="card"><div class="n">03</div><h3>Render repair</h3><p>Patching, filling and full re-coats on tired render.</p></div>
      <div class="card"><div class="n">04</div><h3>Wallpapering</h3><p>From feature walls to full-room papering.</p></div>
      <div class="card"><div class="n">05</div><h3>Flooring</h3><p>Installation across a range of flooring types.</p></div>
      <div class="card"><div class="n">06</div><h3>Tiling</h3><p>Bathrooms, kitchens and splashbacks.</p></div>
      <div class="card"><div class="n">07</div><h3>Paving &amp; driveways</h3><p>Outdoor paving and driveway installation.</p></div>
      <div class="card"><div class="n">08</div><h3>Commercial fit-out</h3><p>Salons, shops and offices, including panelling.</p></div>
      <div class="card"><div class="n">09</div><h3>Insurance work</h3><p>Repairs and redecoration as part of insurance claims.</p></div>
    </div>
    <div class="btns" style="margin-top:34px;justify-content:flex-start">
      <a class="btn" href="https://wa.me/447376204980" target="_blank">Get a free quote</a>
    </div>
  </div>
</section>
""" + FOOTER + WIDGET_INCLUDE + """
</body></html>
"""

GALLERY_PAGE = """
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Gallery - AU Decorating Ltd</title>
<meta name="viewport" content="width=device-width, initial-scale=1">""" + BASE_STYLE + """</head><body>
""" + NAV + """
<section class="band">
  <div class="wrap">
    <div class="head">
      <span class="eyebrow">Recent work</span><div class="rule"></div>
      <h2 class="title serif">The gallery</h2>
      <p class="sub">Real projects completed by AU Decorating around Portsmouth.</p>
    </div>

    <h3 class="serif" style="color:var(--gold);font-weight:600;margin:0 0 16px;font-size:20px;">Before &amp; after</h3>
    <div class="ba-grid">
""" + _ba("terrace-before.jpg","terrace-after.jpg","Victorian terrace, full exterior repaint","3/4") + \
       _ba("arched-before.jpg","arched-after.jpg","Render repair &amp; exterior repaint","3/4") + \
       _ba("kitchen-before.jpg","kitchen-after.jpg","Kitchen renovation &amp; splashback","16/9") + """
    </div>

    <h3 class="serif" style="color:var(--gold);font-weight:600;margin:48px 0 16px;font-size:20px;">Exteriors</h3>
    <div class="work-grid">
      <figure class="work tall"><img src="/static/images/work-terrace-bay.jpg" alt="Painted Victorian bay window"><figcaption>Victorian terrace &middot; grey &amp; white</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-terrace-relief.jpg" alt="Restored period plasterwork"><figcaption>Restored period detailing</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-cottage-1.jpg" alt="Rendered cottage exterior"><figcaption>Rendered cottage exterior</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-cottage-2.jpg" alt="Cottage masonry and trim"><figcaption>Masonry &amp; trim</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-cottage-porch.jpg" alt="Porch and timber satin black"><figcaption>Porch &amp; timber &middot; satin black</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-semi.jpg" alt="Exterior render repaint"><figcaption>Exterior render repaint</figcaption></figure>
    </div>

    <h3 class="serif" style="color:var(--gold);font-weight:600;margin:48px 0 16px;font-size:20px;">Interiors &amp; commercial</h3>
    <div class="work-grid">
      <figure class="work tall"><img src="/static/images/work-burgundy-1.jpg" alt="Heritage red feature wall"><figcaption>Feature wall &middot; heritage red</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-burgundy-2.jpg" alt="Living room and fireplace"><figcaption>Living room &amp; fireplace</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-green.jpg" alt="Sage living room repaint"><figcaption>Living room &middot; full repaint</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-salon-corridor.jpg" alt="Salon corridor, marble floor"><figcaption>House of Glam &middot; salon</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-salon-panel.jpg" alt="Slat panel feature wall"><figcaption>Feature slat panelling</figcaption></figure>
      <figure class="work tall"><img src="/static/images/work-salon-room.jpg" alt="Treatment room"><figcaption>Treatment room</figcaption></figure>
    </div>

    <div class="btns" style="margin-top:34px;justify-content:flex-start">
      <a class="btn" href="https://wa.me/447376204980" target="_blank">Get a free quote</a>
    </div>
  </div>
</section>
""" + FOOTER + SLIDER_JS + WIDGET_INCLUDE + """
</body></html>
"""

CONTACT_PAGE = """
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Contact - AU Decorating Ltd</title>
<meta name="viewport" content="width=device-width, initial-scale=1">""" + BASE_STYLE + """</head><body>
""" + NAV + """
<section class="band">
  <div class="wrap wrap--narrow">
    <div class="head">
      <span class="eyebrow">Get in touch</span><div class="rule"></div>
      <h2 class="title serif">Let's talk about your project</h2>
      <p class="sub">Use the chat bubble in the corner for the fastest reply, or reach us directly.</p>
    </div>
    <div class="contact-box">
      <p><strong>Phone:</strong> <a href="tel:+447376204980">07376 204980</a></p>
      <p><strong>WhatsApp:</strong> <a href="https://wa.me/447376204980" target="_blank">Chat with us on WhatsApp</a></p>
      <p><strong>Email:</strong> <a href="mailto:mehmet@au-decorating.com">mehmet@au-decorating.com</a></p>
      <p><strong>Hours:</strong> Every day, flexible scheduling, plus 24-hour call-out</p>
      <p><strong>Area covered:</strong> Portsmouth, Southsea, Fareham, Gosport, Havant, Waterlooville, Cosham, Portchester &amp; surrounding areas</p>
    </div>
  </div>
</section>
""" + FOOTER + WIDGET_INCLUDE + """
</body></html>
"""

PRIVACY_PAGE = """
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Privacy Policy - AU Decorating Ltd</title>
<meta name="viewport" content="width=device-width, initial-scale=1">""" + BASE_STYLE + """</head><body>
""" + NAV + """
<section class="band">
  <div class="wrap wrap--narrow">
    <div class="head">
      <span class="eyebrow">Your privacy</span><div class="rule"></div>
      <h2 class="title serif">Privacy Policy</h2>
      <p class="sub">How AU Decorating Ltd looks after the information you share with us.</p>
    </div>
    <div class="prose">
      <p>This policy explains what we collect when you contact us through this website, why we collect it, and your rights over it. AU Decorating Limited (company number 14912651, registered in England &amp; Wales) (&ldquo;we&rdquo;, &ldquo;us&rdquo;) is the data controller.</p>
      <h3>What we collect</h3>
      <p>When you use the chat assistant or get in touch, we collect only what you choose to give us &mdash; typically your name, phone number or email, your postcode or area, details about the job you&rsquo;d like quoted, and any photos you send us of the work.</p>
      <h3>Why we collect it &amp; our lawful basis</h3>
      <p>We use your details solely to respond to your enquiry, prepare a quote, and arrange any work you go ahead with. Our lawful basis is taking steps at your request before entering into a contract, and our legitimate interest in responding to enquiries about our services.</p>
      <h3>Who we share it with</h3>
      <p>We don&rsquo;t sell your data or use it for advertising. To run the website assistant, your messages are processed by our AI provider (Groq) to generate replies, and your enquiry is emailed to us through Resend. These providers process the information only to deliver that service. We may also contact you by phone, text, WhatsApp or email to follow up on your enquiry.</p>
      <h3>How long we keep it</h3>
      <p>We keep enquiry details only as long as needed to deal with your enquiry and any work that follows, and for our normal business and tax records, after which they are deleted.</p>
      <h3>Cookies</h3>
      <p>The site uses a single essential cookie to remember your chat session. We don&rsquo;t use advertising or tracking cookies. We use privacy-friendly, cookie-free analytics to count visits.</p>
      <h3>Your rights</h3>
      <p>You can ask us to see, correct, or delete the information we hold about you, or to stop using it. Just get in touch and we&rsquo;ll sort it. You also have the right to complain to the UK&rsquo;s Information Commissioner&rsquo;s Office (ico.org.uk).</p>
      <h3>Contact</h3>
      <p>For anything about your data, email <a href="mailto:mehmet@au-decorating.com">mehmet@au-decorating.com</a> or call <a href="tel:+447376204980">07376 204980</a>.</p>
    </div>
  </div>
</section>
""" + FOOTER + WIDGET_INCLUDE + """
</body></html>
"""


WIDGET_JS = """
(function () {
    var scriptTag = document.currentScript;
    var BASE_URL = new URL(scriptTag.src).origin;

    var bubble = document.createElement('div');
    bubble.id = 'au-chat-bubble';
    bubble.innerHTML = '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<path d="M21 11.5C21.0034 12.8199 20.6951 14.1219 20.1 15.3C19.3944 16.7118 18.3098 17.8992 16.9674 18.7293C15.6251 19.5594 14.0782 19.9994 12.5 20C11.1801 20.0035 9.87812 19.6951 8.7 19.1L3 21L4.9 15.3C4.30493 14.1219 3.99656 12.8199 4 11.5C4.00061 9.92179 4.44061 8.37488 5.27072 7.03258C6.10083 5.69028 7.28825 4.6056 8.7 3.90003C9.87812 3.30496 11.1801 2.99659 12.5 3.00003H13C15.0843 3.11502 17.053 3.99479 18.5291 5.47089C20.0052 6.94699 20.885 8.91568 21 11V11.5Z" stroke="#D4AF37" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    bubble.style.cssText = 'position:fixed;bottom:24px;right:24px;width:76px;height:76px;border-radius:50%;background:#0a0a0a;border:2px solid #D4AF37;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,0.25);z-index:999999;transition:transform 0.15s ease;';
    bubble.onmouseenter = function () { bubble.style.transform = 'scale(1.06)'; };
    bubble.onmouseleave = function () { bubble.style.transform = 'scale(1)'; };

    var iframe = document.createElement('iframe');
    iframe.id = 'au-chat-iframe';
    iframe.src = BASE_URL + '/widget-frame';

    function applyIframeStyle() {
        var isMobile = window.innerWidth <= 600;
        if (isMobile) {
            iframe.style.cssText = 'position:fixed;bottom:0;right:0;left:0;top:0;width:100%;height:100%;border:none;border-radius:0;box-shadow:none;display:none;z-index:999999;';
        } else {
            iframe.style.cssText = 'position:fixed;bottom:112px;right:24px;width:400px;height:580px;border:none;border-radius:18px;box-shadow:0 12px 40px rgba(0,0,0,0.25);display:none;z-index:999999;';
        }
    }
    applyIframeStyle();
    window.addEventListener('resize', function () {
        var wasOpen = iframe.style.display === 'block';
        applyIframeStyle();
        if (wasOpen) iframe.style.display = 'block';
    });

    var isOpen = false;
    bubble.addEventListener('click', function () {
        isOpen = !isOpen;
        iframe.style.display = isOpen ? 'block' : 'none';
    });

    document.body.appendChild(bubble);
    document.body.appendChild(iframe);

    window.addEventListener('message', function (event) {
        if (event.data === 'close-au-chat') {
            isOpen = false;
            iframe.style.display = 'none';
        }
    });
})();
"""

WIDGET_FRAME = """
<!DOCTYPE html>
<html>
<head>
    <style>
        * { box-sizing: border-box; }
        body { margin: 0; font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; }
        #chatWindow { display: flex; flex-direction: column; height: 100vh; background: white; border-radius: 16px; overflow: hidden; }
        #chatHeader { background: #0a0a0a; color: #D4AF37; padding: 20px 22px; }
        #chatHeader .title { font-size: 17px; font-weight: 600; }
        #chatHeader .subtitle { font-size: 13px; opacity: 0.75; color: #e8d9a8; }
        #chatbox { flex: 1; padding: 20px; overflow-y: auto; background: #f7f7f5; }
        .msg { margin: 10px 0; padding: 12px 16px; border-radius: 16px; max-width: 82%; font-size: 16px; line-height: 1.45; }
        .user { background: #0a0a0a; color: #D4AF37; margin-left: auto; }
        .bot { background: #ECECEC; color: #222; }
        .typing { color: #999; letter-spacing: 2px; }
        #inputRow { display: flex; border-top: 1px solid #eee; padding: 12px; background: white; }
        #userInput { flex: 1; padding: 12px 16px; border: 1px solid #ddd; border-radius: 24px; font-size: 16px; outline: none; }
        #userInput:focus { border-color: #D4AF37; }
        #sendBtn { border: none; background: #0a0a0a; color: #D4AF37; width: 46px; height: 46px; border-radius: 50%; margin-left: 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        #sendBtn:hover { background: #1f1f1f; }
        #attachBtn { width: 46px; height: 46px; border-radius: 50%; background: #ECECEC; margin-right: 8px; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        #attachBtn:hover { background: #e2e2da; }
        #attachBtn input { display: none; }
        #attachBtn.busy { opacity: 0.45; pointer-events: none; }
        .msg img.photo { max-width: 190px; width: 100%; border-radius: 12px; display: block; }
        .msg.photo-msg { padding: 5px; background: #0a0a0a; }
    </style>
</head>
<body>
    <div id="chatWindow">
        <div id="chatHeader" style="display:flex; align-items:center; justify-content:space-between;">
            <div>
                <div class="title">AU Decorating Ltd</div>
                <div class="subtitle">Usually replies in a few minutes</div>
            </div>
            <div id="closeBtn" onclick="window.parent.postMessage('close-au-chat', '*')" style="cursor:pointer; font-size:24px; color:#D4AF37; line-height:1; padding:4px 8px;">&times;</div>
        </div>
        <div id="chatbox"></div>
        <div id="inputRow">
            <label id="attachBtn" title="Attach a photo of the job">
                <input type="file" id="fileInput" accept="image/*" multiple onchange="handleFiles(this)">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M21.44 11.05l-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3 3 0 0 1 4.24 4.24l-9.2 9.19a1 1 0 0 1-1.41-1.41l8.49-8.49" stroke="#0a0a0a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </label>
            <input type="text" id="hpField" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0;">
            <input type="text" id="userInput" placeholder="Type a message..." onkeypress="if(event.key==='Enter') sendMessage()">
            <button id="sendBtn" onclick="sendMessage()">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
                    <path d="M3 11L21 3L13 21L11 13L3 11Z" stroke="white" stroke-width="2" stroke-linejoin="round"/>
                </svg>
            </button>
        </div>
    </div>

    <script>
        addMessage("Hey! What kind of job are you after — painting, tiling, flooring, or something else?", 'bot');

        function addMessage(text, sender) {
            const chatbox = document.getElementById('chatbox');
            const div = document.createElement('div');
            div.className = 'msg ' + sender;
            div.textContent = text;
            chatbox.appendChild(div);
            chatbox.scrollTop = chatbox.scrollHeight;
        }

        function showTyping() {
            const chatbox = document.getElementById('chatbox');
            const div = document.createElement('div');
            div.className = 'msg bot typing';
            div.id = 'typingIndicator';
            div.textContent = '...';
            chatbox.appendChild(div);
            chatbox.scrollTop = chatbox.scrollHeight;
        }

        function hideTyping() {
            const t = document.getElementById('typingIndicator');
            if (t) t.remove();
        }

        async function sendMessage() {
            const input = document.getElementById('userInput');
            const message = input.value.trim();
            if (!message) return;

            addMessage(message, 'user');
            input.value = '';
            showTyping();

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message, website: (document.getElementById('hpField')||{}).value || '' }),
                    credentials: 'same-origin'
                });
                const data = await response.json();
                hideTyping();
                addMessage(data.reply, 'bot');
            } catch (e) {
                hideTyping();
                addMessage("Sorry, something went wrong sending that - please try again in a moment.", 'bot');
            }
        }

        function addImageMessage(src) {
            const chatbox = document.getElementById('chatbox');
            const div = document.createElement('div');
            div.className = 'msg user photo-msg';
            const img = document.createElement('img');
            img.className = 'photo';
            img.src = src;
            div.appendChild(img);
            chatbox.appendChild(div);
            chatbox.scrollTop = chatbox.scrollHeight;
        }

        // Phone photos are huge, so we shrink them in the browser before upload:
        // longest side capped at 1600px, re-encoded as JPEG. Modern browsers
        // honour EXIF orientation when drawing to canvas, so photos stay upright.
        function resizeImage(file) {
            return new Promise(function (resolve, reject) {
                const reader = new FileReader();
                reader.onload = function () {
                    const img = new Image();
                    img.onload = function () {
                        const maxDim = 1600;
                        let w = img.naturalWidth, h = img.naturalHeight;
                        if (Math.max(w, h) > maxDim) {
                            if (w >= h) { h = Math.round(h * maxDim / w); w = maxDim; }
                            else { w = Math.round(w * maxDim / h); h = maxDim; }
                        }
                        const canvas = document.createElement('canvas');
                        canvas.width = w; canvas.height = h;
                        const ctx = canvas.getContext('2d');
                        ctx.fillStyle = '#ffffff';      // flatten any transparency
                        ctx.fillRect(0, 0, w, h);
                        ctx.drawImage(img, 0, 0, w, h);
                        resolve(canvas.toDataURL('image/jpeg', 0.82));
                    };
                    img.onerror = function () { reject(new Error('decode')); };
                    img.src = reader.result;
                };
                reader.onerror = function () { reject(new Error('read')); };
                reader.readAsDataURL(file);
            });
        }

        async function handleFiles(input) {
            const files = Array.from(input.files || []);
            input.value = '';
            if (!files.length) return;
            const attachBtn = document.getElementById('attachBtn');
            attachBtn.classList.add('busy');
            for (const file of files) {
                if (!file.type || file.type.indexOf('image/') !== 0) {
                    addMessage("That doesn't look like a photo - please choose an image.", 'bot');
                    continue;
                }
                let dataUrl;
                try {
                    dataUrl = await resizeImage(file);
                } catch (e) {
                    addMessage("Sorry, I couldn't read that image. If it's a HEIC photo from an iPhone, try saving it as a JPG first.", 'bot');
                    continue;
                }
                addImageMessage(dataUrl);
                try {
                    const response = await fetch('/upload', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: dataUrl }),
                        credentials: 'same-origin'
                    });
                    const data = await response.json();
                    addMessage(data.reply, 'bot');
                } catch (e) {
                    addMessage("Sorry, the photo didn't upload - please try again in a moment.", 'bot');
                }
            }
            attachBtn.classList.remove('busy');
        }
    </script>
</body>
</html>
"""

def ensure_session():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

@app.route("/sitemap.xml")
def sitemap():
    pages = ["/", "/services", "/gallery", "/contact"]
    base = "https://au-decorating.com"
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in pages)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nSitemap: https://au-decorating.com/sitemap.xml", mimetype="text/plain")

@app.route("/")
def home():
    ensure_session()
    return render_template_string(HOME_PAGE)

@app.route("/services")
def services():
    ensure_session()
    return render_template_string(SERVICES_PAGE)

@app.route("/gallery")
def gallery():
    ensure_session()
    return render_template_string(GALLERY_PAGE)

@app.route("/contact")
def contact():
    ensure_session()
    return render_template_string(CONTACT_PAGE)

@app.route("/privacy")
def privacy():
    ensure_session()
    return render_template_string(PRIVACY_PAGE)

@app.route("/widget.js")
def widget_js():
    return Response(WIDGET_JS, mimetype="application/javascript")

@app.route("/widget-frame")
def widget_frame():
    ensure_session()
    return render_template_string(WIDGET_FRAME)

@app.route("/chat", methods=["POST"])
def chat_endpoint():
    session_id = session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        session["session_id"] = session_id

    if session_id not in all_conversations:
        all_conversations[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    conversation = all_conversations[session_id]

    data = request.get_json(silent=True) or {}

    # Honeypot: a hidden field real visitors never see or fill. If it's populated,
    # it's almost certainly a bot - quietly stop before spending Groq/Resend.
    if (data.get("website") or "").strip():
        return jsonify({"reply": "Thanks!"})

    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"reply": "Sorry, I didn't catch that - could you type that again?"})

    # Per-session rate limiting to protect against abuse running up Groq/Resend
    # cost: max 20 messages a minute, plus a hard cap per visitor.
    now = time.time()
    recent = [t for t in chat_activity.get(session_id, []) if now - t < 60]
    if len(recent) >= 20:
        return jsonify({"reply": "You're sending messages very quickly - give it a few seconds and try again."})
    if len(conversation) >= 60:
        return jsonify({"reply": "Thanks for all the detail! Drop your name and number and Mehmet will pick this up with you personally."})
    recent.append(now)
    chat_activity[session_id] = recent

    conversation.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=conversation,
            max_tokens=256,
            timeout=20,
        )
        ai_reply = response.choices[0].message.content
    except Exception as e:
        # Never leave the customer staring at a frozen chat. Drop the message we
        # just appended so they can retry cleanly, and reply with a gentle note.
        print(f"Chat completion failed: {e}")
        conversation.pop()
        return jsonify({
            "reply": "Sorry, I had a brief hiccup there - could you send that again?"
        })

    # Strip any internal signal tags so they can never reach the customer.
    lead_ready = bool(re.search(r"\[\[?\s*READY\s*\]?\]", ai_reply, re.I))
    ai_reply = re.sub(r"\[\[?\s*READY\s*\]?\]", "", ai_reply)
    ai_reply = ai_reply.replace("[LEAD_CAPTURED]", "").strip()
    if not ai_reply:
        ai_reply = ("Thanks - that's everything we need for now. AU Decorating "
                    "will be in touch shortly to arrange your free estimate.")

    conversation.append({"role": "assistant", "content": ai_reply})

    # Only email once the assistant has genuinely finished gathering EVERYTHING.
    # It signals this with the internal [[READY]] tag, which it only adds after
    # working through the whole checklist (job, scope, budget, area, contact...).
    # We deliberately do NOT send on wrap-up phrases or a low turn count, because
    # that was firing before budget/postcode were collected. The fallbacks below
    # are conservative - only if the visitor clearly signs off, or a very long
    # chat - so a lead is never lost, but normal chats wait for the full set of
    # questions. Sent at most once per visitor.
    if session_id not in notified_sessions and has_contact_info(conversation):
        if lead_ready or _looks_like_closing(user_message) or len(conversation) >= 24:
            notified_sessions.add(session_id)
            conversation_copy = list(conversation)
            images_copy = list(session_images.get(session_id, []))
            threading.Thread(
                target=send_lead_email,
                args=(conversation_copy, images_copy),
                daemon=True,
            ).start()

    return jsonify({"reply": ai_reply})


@app.route("/upload", methods=["POST"])
def upload_endpoint():
    session_id = session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        session["session_id"] = session_id

    if session_id not in all_conversations:
        all_conversations[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversation = all_conversations[session_id]

    data = request.get_json(silent=True) or {}
    image = _decode_image_data_url(data.get("image", ""))
    if image is None:
        return (
            jsonify({"reply": "Sorry, I couldn't read that image. Please try a JPG or PNG photo."}),
            400,
        )

    images = session_images.setdefault(session_id, [])
    if len(images) >= MAX_IMAGES_PER_SESSION:
        return jsonify({
            "reply": "Thanks - that's plenty of photos for now. Leave your name and number and we'll take a look and get you a quote."
        })

    images.append(image)

    # Keep the transcript (and the AI) aware that a photo came in.
    conversation.append({"role": "user", "content": "(Customer attached a photo of the job)"})
    reply = (
        "Thanks, got your photo - that really helps us picture the job. "
        "Feel free to add more, or leave your name and number and we'll get "
        "you a free quote."
    )
    conversation.append({"role": "assistant", "content": reply})

    # If we've already emailed this lead, forward the new photo as a follow-up
    # so it doesn't get lost.
    if session_id in notified_sessions:
        threading.Thread(
            target=send_photo_followup,
            args=(list(conversation), [image]),
            daemon=True,
        ).start()

    return jsonify({"reply": reply})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
