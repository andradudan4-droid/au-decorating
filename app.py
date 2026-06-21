from flask import Flask, request, jsonify, render_template_string, session, Response
import os
import re
import uuid
import base64
import threading
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
# Matches UK-style numbers: 07xxx..., +44..., 01xxx/02xxx landlines, with
# optional spaces. Digit-count is verified separately to avoid false hits.
PHONE_RE = re.compile(r"(?:\+44\s?|0)\d(?:[\s-]?\d){8,11}")


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
        digits = re.sub(r"\D", "", candidate)
        if 10 <= len(digits) <= 13:
            return candidate.strip()
    return None


def has_contact_info(conversation):
    """True only if we genuinely have a way to contact this person back."""
    return bool(find_email(conversation) or find_phone(conversation))


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
Approx budget:
Preferred timing:
Location / area:
Other notes:"""


def summarise_lead(conversation):
    """Uses the model to extract a tidy, organised lead from the chat."""
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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


def _post_resend(subject, body, attachments=None):
    """Low-level send via Resend's HTTPS API (Render's free tier blocks SMTP).

    `attachments` is a list of dicts like {"filename": ..., "b64": <base64>}.
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
        "text": body,
    }
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


def send_lead_email(conversation, images=None):
    """Emails an organised lead summary (plus transcript and any photos)."""
    images = images or []

    email = find_email(conversation) or "Not provided"
    phone = find_phone(conversation) or "Not provided"
    transcript = _transcript(conversation)
    structured = summarise_lead(conversation)

    # Contact details come from reliable regex; the rest from the summary.
    body = (
        "NEW LEAD - AU Decorating\n"
        "========================\n"
        f"Phone: {phone}\n"
        f"Email: {email}\n"
    )
    if images:
        body += f"Photos attached: {len(images)} (see attachments)\n"
    if structured:
        body += structured + "\n"
    body += (
        "========================\n\n"
        "Full conversation (for reference):\n\n"
        + transcript
    )

    _post_resend(f"New lead - phone: {phone}", body, attachments=images)


def send_photo_followup(conversation, images):
    """If a photo arrives after the lead email was already sent, forward it on
    so it can't get lost."""
    if not images:
        return

    email = find_email(conversation) or "Not provided"
    phone = find_phone(conversation) or "Not provided"
    body = (
        "ADDITIONAL PHOTO(S) - AU Decorating\n"
        "===================================\n"
        "This relates to a lead you've already been emailed about.\n"
        f"Phone: {phone}\n"
        f"Email: {email}\n"
        f"Photos attached: {len(images)} (see attachments)\n"
    )
    _post_resend(f"Photo added - lead: {phone}", body, attachments=images)

SYSTEM_PROMPT = """
You are a friendly assistant for "AU Decorating Ltd", a painting and
decorating company based in Portsmouth, UK, run by Mehmet Yildiz.

Facts about the business:
- 10/10 rating from 45+ reviews on Checkatrade
- Services: interior and exterior painting, decorating, flooring,
  tiling, paving, driveway installation, and anti-vandal coatings.
  Both domestic and commercial work.
- They offer FREE estimates / quotes - there are no fixed prices,
  since every job depends on the size and scope of the work.
- Available every day with flexible scheduling, plus 24-hour call-out.
- Insurance work undertaken.
- Known for being professional, punctual, fast, and detail-oriented -
  customers often mention them going the extra mile and finishing
  quickly without compromising quality.

YOUR JOB is to be a friendly first point of contact and capture
enquiries properly, since this is a quote-based trade business, not
a fixed-price/fixed-slot booking business. For every enquiry:
1. Find out what kind of job they need (e.g. painting a room,
   flooring, tiling, exterior work, etc.)
2. Ask roughly what the property/job involves (e.g. how many rooms,
   approximate size, any specifics)
3. To help us quote accurately, ask whether they'd like to send a
   couple of photos of the job or would prefer a visit instead. Make
   clear there's a photo/attachment button (the paperclip) right here
   in the chat they can use to send pictures, and that either option is
   completely fine - whatever's easiest for them. For example:
   "Could you please send a couple of photos of the job? You can attach
   them right here in the chat using the paperclip. Or if you'd prefer,
   we can arrange a visit to take a look instead - whatever suits you."
   If they send photos, thank them warmly. If they'd rather have a
   visit, that's great too - just reassure them and carry on.
4. Ask if it's a domestic or commercial job
5. Gently ask if they have a rough budget in mind for the job - frame
   it as helping tailor the quote, not as a hard requirement. If they
   don't know or don't want to say, that's completely fine, just move on.
6. Collect their name and best contact number or email
7. Let them know AU Decorating will be in touch to arrange a free
   estimate / site visit

Keep replies short, warm, and natural - like a helpful person texting
back, not a formal essay. Do not invent prices - always say pricing
depends on the job and they'll get a free, no-obligation quote.
Never write internal notes, asides, or commentary about your own
instructions - just talk to the customer naturally.
"""

all_conversations = {}
notified_sessions = set()
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
<style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; margin: 0; color: #2a2a2a; background: #fff; }
    nav { background: #0a0a0a; padding: 18px 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
    nav .logo { color: #D4AF37; font-size: 20px; font-weight: 700; letter-spacing: 0.5px; display: flex; align-items: center; gap: 10px; }
    nav .logo img { height: 32px; width: auto; }
    nav .links a { color: #e8d9a8; text-decoration: none; margin-left: 24px; font-size: 14px; }
    nav .links a:hover { color: #D4AF37; }
    .hero { background: linear-gradient(135deg, #0a0a0a, #1f1f1f); color: white; padding: 90px 30px; text-align: center; }
    .hero h1 { font-size: 38px; margin: 0 0 16px 0; font-weight: 700; color: #fff; }
    .hero p { font-size: 18px; opacity: 0.9; max-width: 600px; margin: 0 auto 28px auto; }
    .rating { display: inline-block; background: rgba(212,175,55,0.15); border: 1px solid #D4AF37; color: #D4AF37; padding: 8px 18px; border-radius: 20px; font-size: 14px; margin-bottom: 20px; }
    .btn { display: inline-block; background: #D4AF37; color: #0a0a0a; padding: 14px 28px; border-radius: 6px; font-weight: 700; text-decoration: none; font-size: 15px; }
    .btn:hover { background: #c29d2e; }
    .section { max-width: 1000px; margin: 0 auto; padding: 60px 30px; }
    .section h2 { font-size: 28px; margin-bottom: 8px; color: #0a0a0a; }
    .section .sub { color: #666; margin-bottom: 36px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 24px; }
    .card { background: #f7f7f5; border-radius: 10px; padding: 24px; border-top: 3px solid #D4AF37; }
    .card h3 { margin-top: 0; color: #0a0a0a; }
    .gallery-item img, .gallery-item-wide img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .gallery-item { border-radius: 10px; overflow: hidden; aspect-ratio: 4/3; background: #e3e3e0; }
    .gallery-item-wide { border-radius: 10px; overflow: hidden; aspect-ratio: 16/9; background: #e3e3e0; grid-column: span 2; }
    .gallery-caption { font-size: 13px; color: #888; margin-top: 6px; }
    footer { background: #0a0a0a; color: #e8d9a8; text-align: center; padding: 30px; font-size: 14px; margin-top: 40px; }
    .contact-box { background: #f7f7f5; border-radius: 10px; padding: 30px; border-top: 3px solid #D4AF37; }
    .contact-box p { margin: 8px 0; }
    .testimonial-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 22px; }
    .testimonial-card { background: #f7f7f5; border-radius: 10px; padding: 24px; border-left: 3px solid #D4AF37; }
    .testimonial-stars { color: #D4AF37; font-size: 14px; margin-bottom: 10px; }
    .testimonial-text { font-style: italic; color: #333; line-height: 1.5; margin-bottom: 14px; }
    .testimonial-meta { font-size: 13px; color: #888; }
    .feature-img { width: 100%; border-radius: 10px; margin: 30px 0; max-height: 420px; object-fit: cover; }

    /* --- Mobile responsiveness --- */
    @media (max-width: 600px) {
        nav { padding: 14px 18px; flex-direction: column; align-items: flex-start; gap: 10px; }
        nav .links { display: flex; flex-wrap: wrap; gap: 4px 0; }
        nav .links a { margin-left: 0; margin-right: 18px; }
        .hero { padding: 56px 18px; }
        .hero h1 { font-size: 26px; }
        .hero p { font-size: 15px; }
        .section { padding: 40px 18px; }
        .section h2 { font-size: 22px; }
        .grid { grid-template-columns: 1fr 1fr; gap: 14px; }
        .gallery-item-wide { grid-column: span 2; }
        .testimonial-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 420px) {
        .grid { grid-template-columns: 1fr; }
    }
</style>
"""

NAV = """
<nav>
    <div class="logo"><img src="/static/images/logo.webp" alt="AU Decorating logo"> AU DECORATING LTD</div>
    <div class="links">
        <a href="/">Home</a>
        <a href="/services">Services</a>
        <a href="/gallery">Gallery</a>
        <a href="/contact">Contact</a>
    </div>
</nav>
"""

FOOTER = """
<footer>
    AU Decorating Ltd &middot; Portsmouth, UK &middot; Free estimates, every day, flexible scheduling
</footer>
"""

WIDGET_INCLUDE = '<script src="/widget.js"></script>'

HOME_PAGE = """
<!DOCTYPE html><html><head><title>AU Decorating Ltd - Portsmouth Painters & Decorators</title>
<meta name="description" content="AU Decorating Ltd - 10/10 rated painters and decorators in Portsmouth. Interior and exterior painting, flooring, tiling, paving and driveways. Free quotes, every day.">
<meta name="viewport" content="width=device-width, initial-scale=1">
""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="hero">
    <div class="rating">&#9733; 10/10 from 45+ reviews on Checkatrade</div>
    <h1>Professional Painting &amp; Decorating in Portsmouth</h1>
    <p>Interior &amp; exterior painting, flooring, tiling, paving and driveways.
    Free, no-obligation quotes - flexible scheduling, every day.</p>
    <a class="btn" href="https://wa.me/447376204980" target="_blank">Get a Free Quote on WhatsApp</a>
</div>
<div class="section">
    <img class="feature-img" src="/static/images/exterior-terrace-2.webp" alt="Recently painted terrace house exterior">
    <h2>Why choose AU Decorating</h2>
    <p class="sub">Professional, punctual, and detail-oriented on every job.</p>
    <div class="grid">
        <div class="card"><h3>Free Estimates</h3><p>No fixed prices - every quote is tailored to your job, with no obligation.</p></div>
        <div class="card"><h3>Flexible Scheduling</h3><p>Available every day, plus 24-hour call-out, to fit around your time.</p></div>
        <div class="card"><h3>Domestic &amp; Commercial</h3><p>From a single room to full commercial fit-outs, including insurance work.</p></div>
    </div>
</div>

<div class="section" style="background:#f7f7f5; max-width: 100%; padding-top: 60px; padding-bottom: 60px;">
    <div style="max-width:1000px; margin:0 auto;">
        <h2>What customers say</h2>
        <p class="sub">10/10 rating from 45+ reviews on Checkatrade</p>
        <div class="testimonial-grid">
            <div class="testimonial-card">
                <div class="testimonial-stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div>
                <p class="testimonial-text">Mehmet repaired cracked plaster and water damage, then redecorated quickly and tidily - even tackled a few extra small jobs at no extra charge.</p>
                <p class="testimonial-meta">Verified Checkatrade review</p>
            </div>
            <div class="testimonial-card">
                <div class="testimonial-stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div>
                <p class="testimonial-text">A repeat customer praised Mehmet's hallway decorating as efficient, great value, and finished with an excellent clean-up afterwards.</p>
                <p class="testimonial-meta">Verified Checkatrade review</p>
            </div>
            <div class="testimonial-card">
                <div class="testimonial-stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div>
                <p class="testimonial-text">Exterior painting customer noted the careful prep work made a big visible difference, with the team arriving on time and leaving everything tidy.</p>
                <p class="testimonial-meta">Verified Checkatrade review</p>
            </div>
            <div class="testimonial-card">
                <div class="testimonial-stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div>
                <p class="testimonial-text">Praised for staying in touch from the first enquiry through to completion, arriving on time, and being polite and friendly throughout the job.</p>
                <p class="testimonial-meta">Verified Checkatrade review</p>
            </div>
            <div class="testimonial-card">
                <div class="testimonial-stars">&#9733;&#9733;&#9733;&#9733;&#9733;</div>
                <p class="testimonial-text">A bathroom and bedroom ceiling painting customer highlighted a quick, clear quote and a flexible approach to the work required.</p>
                <p class="testimonial-meta">Verified Checkatrade review</p>
            </div>
        </div>
        <p style="margin-top:24px;"><a href="https://www.checkatrade.com/trades/audecoratinglimited" target="_blank" style="color:#1B3A5C;">See all reviews on Checkatrade &rarr;</a></p>
    </div>
</div>
""" + FOOTER + WIDGET_INCLUDE + """
</body></html>
"""

SERVICES_PAGE = """
<!DOCTYPE html><html><head><title>Services - AU Decorating Ltd</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="section">
    <h2>Our Services</h2>
    <p class="sub">Painting, decorating, and more across Portsmouth and the surrounding area.</p>
    <div class="grid">
        <div class="card"><h3>Interior Painting</h3><p>Walls, ceilings, woodwork - finished to a high standard.</p></div>
        <div class="card"><h3>Exterior Painting</h3><p>Weatherproof finishes that protect and refresh your property.</p></div>
        <div class="card"><h3>Wallpapering</h3><p>From feature walls to full-room papering.</p></div>
        <div class="card"><h3>Flooring</h3><p>Installation across a range of flooring types.</p></div>
        <div class="card"><h3>Tiling</h3><p>Bathrooms, kitchens, and more.</p></div>
        <div class="card"><h3>Paving &amp; Driveways</h3><p>Outdoor paving and driveway installation.</p></div>
        <div class="card"><h3>Anti-Vandal Coatings</h3><p>Protective coatings for commercial and public-facing properties.</p></div>
        <div class="card"><h3>Insurance Work</h3><p>Repairs and redecoration undertaken as part of insurance claims.</p></div>
    </div>
</div>
""" + FOOTER + WIDGET_INCLUDE + """
</body></html>
"""

GALLERY_PAGE = """
<!DOCTYPE html><html><head><title>Gallery - AU Decorating Ltd</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="section">
    <h2>Recent Work</h2>
    <p class="sub">A selection of real projects completed by AU Decorating around Portsmouth.</p>

    <h3>Featured project: salon refurbishment (painting &amp; flooring)</h3>
    <div class="grid">
        <div class="gallery-item-wide"><img src="/static/images/salon-flooring-painting.webp" alt="Salon refurbishment - marble effect flooring and feature wall painting"></div>
        <div class="gallery-item"><img src="/static/images/salon-kitchenette.webp" alt="Salon staff kitchenette - repainted and retiled"></div>
    </div>

    <h3 style="margin-top:48px;">Exterior painting</h3>
    <div class="grid">
        <div class="gallery-item"><img src="/static/images/exterior-terrace-1.webp" alt="Exterior terrace house painting"></div>
        <div class="gallery-item"><img src="/static/images/exterior-terrace-2.webp" alt="Exterior terrace house painting, yellow finish"></div>
        <div class="gallery-item"><img src="/static/images/exterior-side-render.webp" alt="Exterior render painting, side of property"></div>
        <div class="gallery-item"><img src="/static/images/exterior-grey-semi.webp" alt="Exterior semi-detached house painting"></div>
        <div class="gallery-item"><img src="/static/images/exterior-extension-1.webp" alt="House extension exterior painting"></div>
        <div class="gallery-item"><img src="/static/images/exterior-extension-2.webp" alt="House extension exterior painting, garden view"></div>
        <div class="gallery-item"><img src="/static/images/exterior-porch.webp" alt="Front porch and door area painting"></div>
        <div class="gallery-item"><img src="/static/images/exterior-bay-painted-1.jpg" alt="Painted bay window detail, grey finish"></div>
        <div class="gallery-item"><img src="/static/images/exterior-bay-painted-2.jpg" alt="Painted bay window, full view"></div>
        <div class="gallery-item"><img src="/static/images/exterior-bay-detail-1.jpg" alt="Decorative plasterwork detail, painted"></div>
        <div class="gallery-item"><img src="/static/images/exterior-bay-detail-2.jpg" alt="Painted terrace house facade detail"></div>
        <div class="gallery-item"><img src="/static/images/exterior-full-house-2.jpg" alt="Exterior terrace house painting, full elevation"></div>
        <div class="gallery-item"><img src="/static/images/exterior-side-wall-grey.jpg" alt="Exterior wall painted dark grey"></div>
    </div>

    <h3 style="margin-top:48px;">Interior painting, flooring &amp; tiling</h3>
    <div class="grid">
        <div class="gallery-item"><img src="/static/images/flooring-grey-wood.webp" alt="New flooring installation, grey wood-effect"></div>
        <div class="gallery-item"><img src="/static/images/kitchen-tiling.webp" alt="Kitchen tiling and splashback"></div>
        <div class="gallery-item"><img src="/static/images/interior-hallway-floor.jpg" alt="Hallway flooring and painted walls"></div>
        <div class="gallery-item"><img src="/static/images/interior-floor-room-3.jpg" alt="Refinished wood flooring with period fireplace surround"></div>
    </div>
</div>
""" + FOOTER + WIDGET_INCLUDE + """
</body></html>
"""

CONTACT_PAGE = """
<!DOCTYPE html><html><head><title>Contact - AU Decorating Ltd</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="section">
    <h2>Get in Touch</h2>
    <p class="sub">Use the chat bubble in the corner for the fastest reply, or reach us directly.</p>
    <div class="contact-box">
        <p><strong>Phone:</strong> <a href="tel:07376204980">07376 204980</a></p>
        <p><strong>WhatsApp:</strong> <a href="https://wa.me/447376204980" target="_blank">Chat with us on WhatsApp</a></p>
        <p><strong>Email:</strong> <a href="mailto:mehmet@au-decorating.com">mehmet@au-decorating.com</a></p>
        <p><strong>Hours:</strong> Available every day, flexible scheduling, plus 24-hour call-out</p>
        <p><strong>Area covered:</strong> Portsmouth and surrounding areas</p>
    </div>
</div>
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
                <input type="file" id="fileInput" accept="image/*" onchange="handleFile(this)">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M21.44 11.05l-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3 3 0 0 1 4.24 4.24l-9.2 9.19a1 1 0 0 1-1.41-1.41l8.49-8.49" stroke="#0a0a0a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </label>
            <input type="text" id="userInput" placeholder="Type a message..." onkeypress="if(event.key==='Enter') sendMessage()">
            <button id="sendBtn" onclick="sendMessage()">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
                    <path d="M3 11L21 3L13 21L11 13L3 11Z" stroke="white" stroke-width="2" stroke-linejoin="round"/>
                </svg>
            </button>
        </div>
    </div>

    <script>
        addMessage("Hi! Thanks for stopping by AU Decorating. What kind of job can we help you with - painting, decorating, flooring, tiling, or something else?", 'bot');

        function addMessage(text, sender) {
            const chatbox = document.getElementById('chatbox');
            const div = document.createElement('div');
            div.className = 'msg ' + sender;
            div.textContent = text;
            chatbox.appendChild(div);
            chatbox.scrollTop = chatbox.scrollHeight;
        }

        async function sendMessage() {
            const input = document.getElementById('userInput');
            const message = input.value.trim();
            if (!message) return;

            addMessage(message, 'user');
            input.value = '';

            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: message }),
                credentials: 'same-origin'
            });
            const data = await response.json();
            addMessage(data.reply, 'bot');
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

        async function handleFile(input) {
            const file = input.files && input.files[0];
            input.value = '';                 // allow re-selecting the same file
            if (!file) return;
            if (!file.type || file.type.indexOf('image/') !== 0) {
                addMessage("That doesn't look like a photo - please choose an image.", 'bot');
                return;
            }

            const attachBtn = document.getElementById('attachBtn');
            attachBtn.classList.add('busy');

            let dataUrl;
            try {
                dataUrl = await resizeImage(file);
            } catch (e) {
                attachBtn.classList.remove('busy');
                addMessage("Sorry, I couldn't read that image. If it's a HEIC photo from an iPhone, try saving it as a JPG first.", 'bot');
                return;
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
            } finally {
                attachBtn.classList.remove('busy');
            }
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

    user_message = request.json.get("message", "")
    conversation.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=conversation,
        max_tokens=300
    )

    ai_reply = response.choices[0].message.content

    # Belt-and-braces: strip the old marker if the model ever emits it, so it
    # can never reach the customer. We no longer depend on it for anything.
    if "[LEAD_CAPTURED]" in ai_reply:
        ai_reply = ai_reply.replace("[LEAD_CAPTURED]", "").strip()

    conversation.append({"role": "assistant", "content": ai_reply})

    # Send the lead email the moment the conversation genuinely contains a phone
    # number or email address (detected server-side), and only once per visitor.
    # No AI marker involved, so there's nothing for the bot to leak to customers.
    if session_id not in notified_sessions and has_contact_info(conversation):
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
