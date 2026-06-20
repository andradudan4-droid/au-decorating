from flask import Flask, request, jsonify, render_template_string, session, Response
import os
import uuid
import smtplib
import threading
from email.mime.text import MIMEText
from groq import Groq

app = Flask(__name__)
app.secret_key = "dev-secret-key-change-this-later"
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True

client = Groq(
    api_key=os.environ.get("GROQ_API_KEY")
)

# --- Email notification settings ---
# These come from environment variables you'll set in Render.
NOTIFY_EMAIL_ADDRESS = os.environ.get("NOTIFY_EMAIL_ADDRESS")   # the Gmail account SENDING the alert
NOTIFY_EMAIL_PASSWORD = os.environ.get("NOTIFY_EMAIL_PASSWORD") # that Gmail account's App Password
NOTIFY_TO = os.environ.get("NOTIFY_TO", "mehemt@au-decorating.com")  # where leads get sent

def send_lead_email(conversation):
    """Emails the full chat transcript whenever a conversation looks like a real lead."""
    if not NOTIFY_EMAIL_ADDRESS or not NOTIFY_EMAIL_PASSWORD:
        return  # not configured yet, skip silently

    transcript_lines = []
    for msg in conversation:
        if msg["role"] == "user":
            transcript_lines.append(f"Customer: {msg['content']}")
        elif msg["role"] == "assistant":
            transcript_lines.append(f"Assistant: {msg['content']}")
    transcript = "\n\n".join(transcript_lines)

    body = f"New enquiry from the AU Decorating website chat:\n\n{transcript}"
    email = MIMEText(body)
    email["Subject"] = "New website enquiry - AU Decorating"
    email["From"] = NOTIFY_EMAIL_ADDRESS
    email["To"] = NOTIFY_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(NOTIFY_EMAIL_ADDRESS, NOTIFY_EMAIL_PASSWORD)
            server.sendmail(NOTIFY_EMAIL_ADDRESS, [NOTIFY_TO], email.as_string())
    except Exception as e:
        print(f"Failed to send lead email: {e}")

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
3. Ask if it's a domestic or commercial job
4. Gently ask if they have a rough budget in mind for the job - frame
   it as helping tailor the quote, not as a hard requirement. If they
   don't know or don't want to say, that's completely fine, just move on.
5. Collect their name and best contact number or email
6. Let them know AU Decorating will be in touch to arrange a free
   estimate / site visit

Keep replies short, warm, and natural - like a helpful person texting
back, not a formal essay. Do not invent prices - always say pricing
depends on the job and they'll get a free, no-obligation quote.
"""

all_conversations = {}
notified_sessions = set()

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
        <p><strong>Email:</strong> <a href="mailto:mehemt@au-decorating.com">mehemt@au-decorating.com</a></p>
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
    conversation.append({"role": "assistant", "content": ai_reply})

    # Once someone has exchanged a few real messages, treat it as a likely lead
    # and email the transcript - but only once per visitor session.
    # This runs in a background thread so a slow/failed email never
    # holds up the actual chat reply to the visitor.
    user_message_count = sum(1 for m in conversation if m["role"] == "user")
    if session_id not in notified_sessions and user_message_count >= 3:
        notified_sessions.add(session_id)
        conversation_copy = list(conversation)
        threading.Thread(target=send_lead_email, args=(conversation_copy,), daemon=True).start()

    return jsonify({"reply": ai_reply})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
