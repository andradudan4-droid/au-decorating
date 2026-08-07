# Content Engine + Lead Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AU Decorating's existing lead-capture chat a memory (a real database) and a follow-up sequence, and build a reusable Claude-powered content engine that generates the follow-up messages — the foundation the later SEO/blog/social sub-projects will also consume.

**Architecture:** A new `marketing/` package inside the existing `au-decorating-site` Flask repo. `content_engine.py` wraps the Claude API with a knowledge-base + prompt-template system. `db.py` is a thin data-access layer over Postgres (no ORM). `followups.py` contains the cadence logic, calling `content_engine` to generate messages and `email.py`/`sms.py` to send them. `app.py` gets three small additions: a hook that persists each captured lead, a secret-protected `/internal/tick` endpoint an external scheduler calls periodically, and a minimal `/admin/leads` page for manually marking a lead as replied.

**Tech Stack:** Python 3.13, Flask (existing), `anthropic` SDK, `psycopg2-binary` (Postgres, no ORM), `twilio` SDK, `requests` (existing, reused for Resend), `pytest` + `unittest.mock` (new — no test infra exists yet).

## Global Constraints

- Do not modify the existing Groq-based chat model or its prompt (`SYSTEM_PROMPT` in `app.py`) — it stays as-is per the design spec.
- Full autopilot: generated follow-ups send with no human approval step, but every one is logged to `lead_followups` for audit/manual correction after the fact.
- Follow-up cadence is fixed at 24h / 3 days / 7 days after the lead was captured, then stops automatically (per spec).
- Database: Postgres via `DATABASE_URL` env var (Supabase free tier recommended — see Task 13). No SQLite, no ORM.
- New Anthropic env var: `ANTHROPIC_API_KEY`. Model: `CONTENT_ENGINE_MODEL` env var, default `claude-haiku-4-5-20251001` (cheap/fast — messages are one or two sentences, not long-form content).
- Admin/internal routes are protected by shared-secret env vars (`ADMIN_SECRET`, `TICK_SECRET`), not a full auth system — acceptable for a single-operator v1, called out as a known limitation.

---

### Task 1: Dependencies and environment scaffolding

**Files:**
- Modify: `requirements.txt`
- Create: `.env.example`

**Interfaces:**
- Produces: the four new packages (`anthropic`, `psycopg2-binary`, `twilio`, `pytest`) available to every later task.

- [ ] **Step 1: Add new dependencies**

Modify `requirements.txt` to:

```
flask
groq
gunicorn
requests
anthropic
psycopg2-binary
twilio
pytest
```

- [ ] **Step 2: Install them**

Run: `cd /Users/andradudan/Desktop/au-decorating-site && pip install -r requirements.txt`
Expected: all packages install with no errors.

- [ ] **Step 3: Document required environment variables**

Create `.env.example`:

```
# Existing
SECRET_KEY=
GROQ_API_KEY=
RESEND_API_KEY=
NOTIFY_TO=mehmet@au-decorating.com

# New — content engine + lead follow-up
ANTHROPIC_API_KEY=
CONTENT_ENGINE_MODEL=claude-haiku-4-5-20251001
DATABASE_URL=postgresql://user:password@host:5432/dbname
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
ADMIN_SECRET=
TICK_SECRET=
```

- [ ] **Step 4: Commit**

```bash
cd /Users/andradudan/Desktop/au-decorating-site
git add requirements.txt .env.example
git commit -m "chore: add deps and env docs for content engine + lead follow-up"
```

---

### Task 2: Knowledge base content

**Files:**
- Create: `marketing/__init__.py` (empty)
- Create: `marketing/knowledge/brand.md`
- Create: `marketing/knowledge/services.md`
- Create: `marketing/knowledge/service_area.md`

**Interfaces:**
- Produces: three knowledge files that `content_engine.py` (Task 3) reads verbatim.

- [ ] **Step 1: Create the package**

Create `marketing/__init__.py` (empty file).

- [ ] **Step 2: Write brand.md**

Create `marketing/knowledge/brand.md`:

```markdown
# Brand voice — AU Decorating Ltd

AU Decorating Ltd is a painting and decorating company based in Portsmouth, run
directly by its owner, Mehmet Yildiz. Customers deal with Mehmet personally, not a
call centre — that direct, one-person relationship is core to the brand.

- 10/10 rating from 45+ verified reviews on Checkatrade.
- Free estimates — pricing depends on the job, there are no fixed prices.
- Available every day, flexible scheduling, 24-hour call-out.
- Insurance work undertaken.
- Company No. 14912651 (AU Decorating Limited, incorporated 3 June 2023).

## Tone

Write like a friendly local tradesperson, not a customer service chatbot. Short,
warm, direct — no filler like "Great question!", "I'd be happy to help!", "Of
course!". Get straight to the point. Prefer one or two short sentences over long
paragraphs. Never use bullet points or corporate phrasing in customer-facing
messages.

Bad: "Great! I'd be happy to help you get a free, no-obligation quote today!"
Good: "Nice one — happy to help, let's get you a quote sorted."
```

- [ ] **Step 3: Write services.md**

Create `marketing/knowledge/services.md`:

```markdown
# Services

- Interior painting (walls, ceilings, woodwork)
- Exterior painting and render repair
- Flooring and tiling installation (kitchens, bathrooms, splashbacks)
- Paving and driveway installation
- Commercial fit-out (salons, shops, offices, including panelling)
- Insurance claim repairs and redecoration
- Anti-vandal coatings

Domestic and commercial. From a single feature wall to a full fit-out.
```

- [ ] **Step 4: Write service_area.md**

Create `marketing/knowledge/service_area.md`:

```markdown
# Service area

Based in Southsea (postcode PO5 1JY), covering roughly a 10-mile radius:
Portsmouth, Southsea, Fareham, Gosport, Havant, Waterlooville, Emsworth,
Hayling Island, Cosham, Portchester, Lee-on-the-Solent, Stubbington, Titchfield,
Purbrook, Cowplain, Horndean, Denmead, Wickham, Rowlands Castle.
```

- [ ] **Step 5: Commit**

```bash
git add marketing/__init__.py marketing/knowledge/
git commit -m "feat: add marketing knowledge base"
```

---

### Task 3: Content engine

**Files:**
- Create: `marketing/prompts/follow_up.md`
- Create: `marketing/content_engine.py`
- Test: `tests/test_content_engine.py`

**Interfaces:**
- Consumes: `marketing/knowledge/*.md` (Task 2).
- Produces: `content_engine.generate(content_type: str, context: dict) -> str` — used by `followups.py` (Task 8) and every later sub-project's content generation.

- [ ] **Step 1: Write the follow-up prompt template**

Create `marketing/prompts/follow_up.md`:

```markdown
Write a short follow-up message to a potential customer who enquired about a
job with AU Decorating but hasn't replied yet since the initial enquiry.

Customer name: {name}
Job they enquired about: {job}
This is follow-up number {step} (1 = first nudge, 2 = a few days later, 3 = final
check-in before we stop following up).

Rules:
- One or two short sentences only. This will be sent as an SMS/email body, not a
  full letter — no greeting like "Dear X" and no sign-off/signature block.
- Step 1: friendly, assume they're just busy - a light nudge.
- Step 2: a bit more direct, offer to answer any questions.
- Step 3: final check-in, low-pressure, make clear this is the last follow-up.
- Do not repeat the same phrasing a typical previous step would use - keep it
  feeling like a real person following up, not a template.
- Never invent details about the job that weren't given - if the job is
  "your enquiry", keep it generic rather than guessing specifics.

Write only the message text, nothing else.
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_content_engine.py`:

```python
from unittest.mock import MagicMock

from marketing import content_engine


def _fake_client(reply_text):
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=reply_text)]
    client.messages.create.return_value = response
    return client


def test_generate_returns_model_text(monkeypatch):
    fake_client = _fake_client("Hey James, just checking in on your kitchen quote!")
    monkeypatch.setattr(content_engine, "_get_client", lambda: fake_client)

    result = content_engine.generate(
        "follow_up", {"name": "James", "job": "kitchen painting", "step": 1}
    )

    assert result == "Hey James, just checking in on your kitchen quote!"


def test_generate_includes_knowledge_and_context_in_prompt(monkeypatch):
    fake_client = _fake_client("ok")
    monkeypatch.setattr(content_engine, "_get_client", lambda: fake_client)

    content_engine.generate(
        "follow_up", {"name": "James", "job": "kitchen painting", "step": 2}
    )

    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert "AU Decorating" in call_kwargs["system"]
    assert "Southsea" in call_kwargs["system"]
    user_message = call_kwargs["messages"][0]["content"]
    assert "James" in user_message
    assert "kitchen painting" in user_message
    assert "follow-up number 2" in user_message


def test_generate_unknown_content_type_raises(monkeypatch):
    monkeypatch.setattr(content_engine, "_get_client", lambda: _fake_client("ok"))

    try:
        content_engine.generate("not_a_real_type", {})
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/andradudan/Desktop/au-decorating-site && pytest tests/test_content_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketing.content_engine'`

- [ ] **Step 4: Implement the content engine**

Create `marketing/content_engine.py`:

```python
import os
from pathlib import Path

from anthropic import Anthropic

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_MODEL = os.environ.get("CONTENT_ENGINE_MODEL", "claude-haiku-4-5-20251001")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _load_knowledge():
    parts = [
        (KNOWLEDGE_DIR / name).read_text()
        for name in ("brand.md", "services.md", "service_area.md")
    ]
    return "\n\n".join(parts)


def _load_prompt(content_type):
    path = PROMPTS_DIR / f"{content_type}.md"
    if not path.exists():
        raise ValueError(f"Unknown content type: {content_type}")
    return path.read_text()


def generate(content_type, context):
    """Generate marketing copy of `content_type`, grounded in the AU Decorating
    knowledge base, interpolating `context` into that type's prompt template."""
    knowledge = _load_knowledge()
    template = _load_prompt(content_type)
    instructions = template.format(**context)
    system = f"You are the marketing copywriter for AU Decorating Ltd.\n\n{knowledge}"

    response = _get_client().messages.create(
        model=DEFAULT_MODEL,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": instructions}],
    )
    return response.content[0].text.strip()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_content_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add marketing/prompts/ marketing/content_engine.py tests/test_content_engine.py
git commit -m "feat: add Claude-powered content engine"
```

---

### Task 4: Database schema, lead insert, and lead listing

**Files:**
- Create: `marketing/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.init_schema()`, `db.insert_lead(fields: dict) -> int`, `db.list_leads() -> list[dict]`. `fields` keys: `Name`, `Phone`, `Email`, `Area`, `Job` (values may be `None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db.py`:

```python
from unittest.mock import MagicMock, call

from marketing import db


class FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_init_schema_creates_tables(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.init_schema()

    executed_sql = " ".join(sql for sql, _ in cursor.executed)
    assert "CREATE TABLE IF NOT EXISTS leads" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS lead_followups" in executed_sql
    assert conn.committed
    assert conn.closed


def test_insert_lead_returns_new_id(monkeypatch):
    cursor = FakeCursor(fetchone_result={"id": 42})
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    lead_id = db.insert_lead(
        {"Name": "James", "Phone": "07123 456789", "Email": None,
         "Area": "Southsea", "Job": "Kitchen painting"}
    )

    assert lead_id == 42
    sql, params = cursor.executed[0]
    assert "INSERT INTO leads" in sql
    assert params["Name"] == "James"
    assert conn.committed
    assert conn.closed


def test_list_leads_returns_rows(monkeypatch):
    rows = [{"id": 1, "name": "James"}, {"id": 2, "name": "Priya"}]
    cursor = FakeCursor(fetchall_result=rows)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.list_leads()

    assert result == rows
    assert conn.closed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketing.db'`

- [ ] **Step 3: Implement db.py (schema, insert_lead, list_leads)**

Create `marketing/db.py`:

```python
import os

import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


def init_schema():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id SERIAL PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    email TEXT,
                    area TEXT,
                    job TEXT,
                    status TEXT NOT NULL DEFAULT 'contacted',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_contacted_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS lead_followups (
                    id SERIAL PRIMARY KEY,
                    lead_id INTEGER NOT NULL REFERENCES leads(id),
                    step INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def insert_lead(fields):
    """fields keys: Name, Phone, Email, Area, Job (any may be None).
    Returns the new lead's id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO leads (name, phone, email, area, job)
                VALUES (%(Name)s, %(Phone)s, %(Email)s, %(Area)s, %(Job)s)
                RETURNING id
                """,
                fields,
            )
            lead_id = cur.fetchone()["id"]
        conn.commit()
        return lead_id
    finally:
        conn.close()


def list_leads():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads ORDER BY created_at DESC")
            return cur.fetchall()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add marketing/db.py tests/test_db.py
git commit -m "feat: add database schema, lead insert, and lead listing"
```

---

### Task 5: Follow-up queries — due leads, recording, marking replied

**Files:**
- Modify: `marketing/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: `get_connection()` (Task 4).
- Produces: `db.get_leads_with_followup_counts() -> list[dict]` (each dict includes `followup_count: int`), `db.record_followup(lead_id, step, channel, content)`, `db.mark_replied(lead_id)`. Used by `followups.py` (Task 8) and the admin routes (Task 11).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_get_leads_with_followup_counts_queries_active_leads(monkeypatch):
    rows = [{"id": 1, "name": "James", "followup_count": 0}]
    cursor = FakeCursor(fetchall_result=rows)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    result = db.get_leads_with_followup_counts()

    assert result == rows
    sql, _ = cursor.executed[0]
    assert "lead_followups" in sql
    assert "status = 'contacted'" in sql
    assert conn.closed


def test_record_followup_inserts_and_updates_last_contacted(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.record_followup(7, 1, "email", "Hey James, following up!")

    assert len(cursor.executed) == 2
    insert_sql, insert_params = cursor.executed[0]
    assert "INSERT INTO lead_followups" in insert_sql
    assert insert_params == {
        "lead_id": 7, "step": 1, "channel": "email",
        "content": "Hey James, following up!",
    }
    update_sql, update_params = cursor.executed[1]
    assert "UPDATE leads SET last_contacted_at" in update_sql
    assert update_params == {"id": 7}
    assert conn.committed
    assert conn.closed


def test_mark_replied_updates_status(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)

    db.mark_replied(7)

    sql, params = cursor.executed[0]
    assert "UPDATE leads SET status = 'replied'" in sql
    assert params == {"id": 7}
    assert conn.committed
    assert conn.closed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `AttributeError: module 'marketing.db' has no attribute 'get_leads_with_followup_counts'`

- [ ] **Step 3: Implement the new functions**

Append to `marketing/db.py`:

```python
def get_leads_with_followup_counts():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.id, l.name, l.phone, l.email, l.area, l.job, l.status,
                       l.created_at, COUNT(f.id) AS followup_count
                FROM leads l
                LEFT JOIN lead_followups f ON f.lead_id = l.id
                WHERE l.status = 'contacted'
                GROUP BY l.id
                ORDER BY l.created_at
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def record_followup(lead_id, step, channel, content):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_followups (lead_id, step, channel, content)
                VALUES (%(lead_id)s, %(step)s, %(channel)s, %(content)s)
                """,
                {"lead_id": lead_id, "step": step, "channel": channel, "content": content},
            )
            cur.execute(
                "UPDATE leads SET last_contacted_at = now() WHERE id = %(id)s",
                {"id": lead_id},
            )
        conn.commit()
    finally:
        conn.close()


def mark_replied(lead_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE leads SET status = 'replied' WHERE id = %(id)s",
                {"id": lead_id},
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add marketing/db.py tests/test_db.py
git commit -m "feat: add follow-up due query, recording, and mark-replied"
```

---

### Task 6: Follow-up email sender

**Files:**
- Create: `marketing/email.py`
- Test: `tests/test_email.py`

**Interfaces:**
- Produces: `email.send_followup_email(to_email: str, name: str, message: str)`. Used by `followups.py` (Task 8).

- [ ] **Step 1: Write the failing test**

Create `tests/test_email.py`:

```python
from unittest.mock import MagicMock

from marketing import email


def test_send_followup_email_posts_to_resend(monkeypatch):
    monkeypatch.setattr(email, "RESEND_API_KEY", "fake-key")
    monkeypatch.setattr(email, "REPLY_TO", "mehmet@au-decorating.com")
    fake_response = MagicMock(status_code=200)
    fake_post = MagicMock(return_value=fake_response)
    monkeypatch.setattr(email.requests, "post", fake_post)

    email.send_followup_email("james@example.com", "James", "Just checking in!")

    fake_post.assert_called_once()
    _, kwargs = fake_post.call_args
    payload = kwargs["json"]
    assert payload["to"] == ["james@example.com"]
    assert payload["reply_to"] == "mehmet@au-decorating.com"
    assert payload["text"] == "Just checking in!"


def test_send_followup_email_skips_without_api_key(monkeypatch):
    monkeypatch.setattr(email, "RESEND_API_KEY", None)
    fake_post = MagicMock()
    monkeypatch.setattr(email.requests, "post", fake_post)

    email.send_followup_email("james@example.com", "James", "Just checking in!")

    fake_post.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_email.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketing.email'`

- [ ] **Step 3: Implement email.py**

Create `marketing/email.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_email.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add marketing/email.py tests/test_email.py
git commit -m "feat: add follow-up email sender"
```

---

### Task 7: Follow-up SMS sender

**Files:**
- Create: `marketing/sms.py`
- Test: `tests/test_sms.py`

**Interfaces:**
- Produces: `sms.send_followup_sms(to_phone: str, message: str)`. Used by `followups.py` (Task 8).

- [ ] **Step 1: Write the failing test**

Create `tests/test_sms.py`:

```python
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


def test_send_followup_sms_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(sms.os.environ, "get", {}.get)
    fake_client_cls = MagicMock()
    monkeypatch.setattr(sms, "Client", fake_client_cls)

    sms.send_followup_sms("+447123456789", "Just checking in!")

    fake_client_cls.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sms.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketing.sms'`

- [ ] **Step 3: Implement sms.py**

Create `marketing/sms.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sms.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add marketing/sms.py tests/test_sms.py
git commit -m "feat: add follow-up SMS sender"
```

---

### Task 8: Follow-up cadence logic

**Files:**
- Create: `marketing/followups.py`
- Test: `tests/test_followups.py`

**Interfaces:**
- Consumes: `db.get_leads_with_followup_counts()`, `db.record_followup()` (Task 5); `content_engine.generate()` (Task 3); `email.send_followup_email()` (Task 6); `sms.send_followup_sms()` (Task 7).
- Produces: `followups.CADENCE_HOURS: list[int]`, `followups.leads_due(now=None) -> list[tuple[dict, int]]`, `followups.send_followup(lead: dict, step: int)`, `followups.run_due_followups(now=None) -> int`. `run_due_followups` is called by the `/internal/tick` route (Task 10).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_followups.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from marketing import followups


def _lead(followup_count=0, created_hours_ago=0, email="james@example.com", phone=None):
    now = datetime.now(timezone.utc)
    return {
        "id": 1,
        "name": "James",
        "job": "Kitchen painting",
        "email": email,
        "phone": phone,
        "status": "contacted",
        "created_at": now - timedelta(hours=created_hours_ago),
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


def test_run_due_followups_sends_each_due_lead_and_returns_count(monkeypatch):
    due_lead = _lead(followup_count=0, created_hours_ago=25)
    monkeypatch.setattr(followups, "leads_due", lambda now=None: [(due_lead, 1)])
    fake_send = MagicMock()
    monkeypatch.setattr(followups, "send_followup", fake_send)

    result = followups.run_due_followups()

    assert result == 1
    fake_send.assert_called_once_with(due_lead, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_followups.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketing.followups'`

- [ ] **Step 3: Implement followups.py**

Create `marketing/followups.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_followups.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add marketing/followups.py tests/test_followups.py
git commit -m "feat: add follow-up cadence logic"
```

---

### Task 9: Persist captured leads from the existing chat

**Files:**
- Modify: `app.py:333-365` (the `send_lead_email` function)
- Modify: `app.py` (the `/chat` route's notification block, and the `init_schema()` startup call)
- Test: `tests/test_app_lead_persistence.py`

**Interfaces:**
- Consumes: `marketing.db.insert_lead(fields: dict) -> int`, `marketing.db.init_schema()`.
- Produces: `send_lead_email(conversation, images=None) -> dict` now returns the `fields` dict it already computes internally, so the call site can persist it without a second (costly) Groq summarisation call.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_lead_persistence.py`:

```python
from unittest.mock import MagicMock

import app as app_module


def test_send_lead_email_returns_fields(monkeypatch):
    monkeypatch.setattr(app_module, "_post_resend", MagicMock())
    monkeypatch.setattr(
        app_module,
        "summarise_lead",
        lambda conversation: "Name: James\nJob / work wanted: Kitchen painting",
    )
    conversation = [
        {"role": "user", "content": "It's James, kitchen needs painting, "
                                     "call me on 07123 456789"},
    ]

    fields = app_module.send_lead_email(conversation)

    assert fields["Name"] == "James"
    assert fields["Job"] == "Kitchen painting"
    assert fields["Phone"] == "07123 456789"


def test_chat_notification_persists_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_post_resend", MagicMock())
    fake_insert_lead = MagicMock(return_value=99)
    monkeypatch.setattr(app_module.marketing_db, "insert_lead", fake_insert_lead)
    monkeypatch.setattr(
        app_module, "client_chat",
        lambda **kwargs: MagicMock(
            choices=[MagicMock(message=MagicMock(
                content="Thanks James, that's everything!\n[[READY]]"
            ))]
        ),
    )
    monkeypatch.setattr(
        app_module, "summarise_lead",
        lambda conversation: "Name: James\nJob / work wanted: Kitchen painting",
    )

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["session_id"] = "test-session"
    app_module.all_conversations["test-session"] = [
        {"role": "system", "content": app_module.SYSTEM_PROMPT}
    ]

    client.post(
        "/chat",
        json={"message": "It's James, kitchen needs painting, call 07123 456789"},
    )

    fake_insert_lead.assert_called_once()
    called_fields = fake_insert_lead.call_args.args[0]
    assert called_fields["Name"] == "James"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app_lead_persistence.py -v`
Expected: FAIL — `send_lead_email` currently returns `None`, and `app_module.marketing_db` doesn't exist yet.

- [ ] **Step 3: Modify app.py**

In `app.py`, add the import near the top (after the existing imports, before `app = Flask(__name__)` is fine, but simplest is right after the `requests`/`Groq` imports):

```python
from marketing import db as marketing_db
```

Directly after `app = Flask(__name__)` and its config lines, initialize the schema (guarded so a missing `DATABASE_URL` in local dev doesn't crash the whole app):

```python
try:
    marketing_db.init_schema()
except Exception as e:
    print(f"marketing DB schema init skipped: {e}")
```

Change `send_lead_email` (currently ~`app.py:333-365`) to return the fields it already computes:

```python
def send_lead_email(conversation, images=None):
    """Emails a tidy, professional lead summary (plus transcript and any photos).
    Returns the extracted lead fields so the caller can persist them."""
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

    urgency_raw = fields.get("Urgency", "")
    urgency_m = re.search(r"[1-5]", str(urgency_raw)) if urgency_raw else None
    urgency_score = int(urgency_m.group(0)) if urgency_m else 0
    urgent_prefix = "🔴 URGENT — " if urgency_score >= 5 else ("🟠 " if urgency_score >= 4 else "")

    contact = fields.get("Phone") or fields.get("Email") or "no number yet"
    bits = [b for b in (fields.get("Name"), fields.get("Area") or fields.get("Postcode")) if b]
    subject = urgent_prefix + "New lead - " + (" · ".join(bits + [contact]) if bits else contact)
    _post_resend(
        subject,
        text_body,
        html_body=html_body,
        attachments=images,
    )
    return fields
```

(Only the final `return fields` line and the docstring are new — everything else in the function is unchanged.)

Update the notification block inside `chat_endpoint` (currently ~`app.py:1625-1630`):

```python
    if session_id not in notified_sessions and has_contact_info(conversation):
        if lead_ready or _looks_like_closing(user_message) or len(conversation) >= 24:
            notified_sessions.add(session_id)
            conversation_copy = list(conversation)
            images_copy = list(session_images.get(session_id, []))
            lead_fields = send_lead_email(conversation_copy, images_copy)
            try:
                marketing_db.insert_lead(lead_fields)
            except Exception as e:
                print(f"Failed to persist lead: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app_lead_persistence.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite to check nothing broke**

Run: `pytest -v`
Expected: all tests pass (content_engine, db, email, sms, followups, app_lead_persistence)

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_lead_persistence.py
git commit -m "feat: persist captured leads to the database"
```

---

### Task 10: `/internal/tick` endpoint

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_internal_tick.py`

**Interfaces:**
- Consumes: `marketing.followups.run_due_followups()` (Task 8).
- Produces: `POST /internal/tick` — the endpoint the external scheduler (Task 12) calls.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_internal_tick.py`:

```python
from unittest.mock import MagicMock

import app as app_module


def test_tick_rejects_missing_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/tick")

    assert response.status_code == 401


def test_tick_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.post("/internal/tick", headers={"X-Tick-Secret": "wrong"})

    assert response.status_code == 401


def test_tick_runs_followups_with_correct_secret(monkeypatch):
    monkeypatch.setattr(app_module, "TICK_SECRET", "correct-secret")
    fake_run = MagicMock(return_value=3)
    monkeypatch.setattr(app_module.followups, "run_due_followups", fake_run)
    client = app_module.app.test_client()

    response = client.post(
        "/internal/tick", headers={"X-Tick-Secret": "correct-secret"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"followups_sent": 3}
    fake_run.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app_internal_tick.py -v`
Expected: FAIL — no `/internal/tick` route exists yet.

- [ ] **Step 3: Add the route**

In `app.py`, add near the other imports:

```python
from marketing import followups
```

Add this near the other env var reads at the top of the file:

```python
TICK_SECRET = os.environ.get("TICK_SECRET")
```

Add the route (anywhere among the other `@app.route` definitions):

```python
@app.route("/internal/tick", methods=["POST"])
def internal_tick():
    if not TICK_SECRET or request.headers.get("X-Tick-Secret") != TICK_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    sent = followups.run_due_followups()
    return jsonify({"followups_sent": sent})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app_internal_tick.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_internal_tick.py
git commit -m "feat: add secret-protected /internal/tick endpoint"
```

---

### Task 11: `/admin/leads` page

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_admin_leads.py`

**Interfaces:**
- Consumes: `marketing.db.list_leads()`, `marketing.db.mark_replied(lead_id)` (Task 4/5).
- Produces: `GET /admin/leads?key=...`, `POST /admin/leads/<id>/mark-replied?key=...` — the manual stop mechanism for the follow-up sequence.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_admin_leads.py`:

```python
from unittest.mock import MagicMock

import app as app_module


def test_admin_leads_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.get("/admin/leads")

    assert response.status_code == 401


def test_admin_leads_lists_leads_with_correct_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.marketing_db, "list_leads",
        lambda: [{"id": 1, "name": "James", "phone": "07123456789",
                   "email": None, "status": "contacted"}],
    )
    client = app_module.app.test_client()

    response = client.get("/admin/leads?key=correct-secret")

    assert response.status_code == 200
    assert b"James" in response.data


def test_mark_replied_updates_lead_and_redirects(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    fake_mark_replied = MagicMock()
    monkeypatch.setattr(app_module.marketing_db, "mark_replied", fake_mark_replied)
    client = app_module.app.test_client()

    response = client.post(
        "/admin/leads/7/mark-replied?key=correct-secret"
    )

    fake_mark_replied.assert_called_once_with(7)
    assert response.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app_admin_leads.py -v`
Expected: FAIL — no `/admin/leads` route exists yet.

- [ ] **Step 3: Add the routes**

In `app.py`, add `redirect` to the existing Flask import line:

```python
from flask import Flask, request, jsonify, render_template_string, session, Response, redirect
```

Add near `TICK_SECRET`:

```python
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")


def _admin_authorized():
    return bool(ADMIN_SECRET) and request.args.get("key") == ADMIN_SECRET
```

Add the routes:

```python
@app.route("/admin/leads")
def admin_leads():
    if not _admin_authorized():
        return "Unauthorized", 401
    leads = marketing_db.list_leads()
    rows = "".join(
        "<tr>"
        f"<td>{lead['id']}</td><td>{lead.get('name') or ''}</td>"
        f"<td>{lead.get('phone') or ''}</td><td>{lead.get('email') or ''}</td>"
        f"<td>{lead['status']}</td>"
        "<td>"
        f'<form method="post" action="/admin/leads/{lead["id"]}/mark-replied?key={ADMIN_SECRET}">'
        '<button type="submit">Mark replied</button></form>'
        "</td></tr>"
        for lead in leads
    )
    return (
        "<table border=1 cellpadding=8><tr><th>ID</th><th>Name</th><th>Phone</th>"
        f"<th>Email</th><th>Status</th><th></th></tr>{rows}</table>"
    )


@app.route("/admin/leads/<int:lead_id>/mark-replied", methods=["POST"])
def admin_mark_replied(lead_id):
    if not _admin_authorized():
        return "Unauthorized", 401
    marketing_db.mark_replied(lead_id)
    return redirect(f"/admin/leads?key={ADMIN_SECRET}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app_admin_leads.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite one more time**

Run: `pytest -v`
Expected: all tests across every file pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_admin_leads.py
git commit -m "feat: add /admin/leads page for viewing and marking leads replied"
```

---

### Task 12: External scheduler for `/internal/tick`

**Files:**
- Create: `.github/workflows/marketing-tick.yml`

**Interfaces:**
- Consumes: `POST /internal/tick` (Task 10), deployed on Render.
- No code interface — this is CI configuration, not testable via pytest.

- [ ] **Step 1: Create the scheduled workflow**

Create `.github/workflows/marketing-tick.yml`:

```yaml
name: Marketing follow-up tick

on:
  schedule:
    - cron: "0 * * * *"  # every hour
  workflow_dispatch: {}

jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - name: Call /internal/tick
        run: |
          curl -sf -X POST https://au-decorating.com/internal/tick \
            -H "X-Tick-Secret: ${{ secrets.TICK_SECRET }}"
```

- [ ] **Step 2: Document the required GitHub secret**

In the GitHub repo settings (Settings → Secrets and variables → Actions), add a
repository secret named `TICK_SECRET` with the same value set for `TICK_SECRET`
in Render's environment variables. This is a manual one-time step, not something
scriptable from this plan.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/marketing-tick.yml
git commit -m "chore: schedule hourly /internal/tick via GitHub Actions"
```

---

### Task 13: Deployment configuration (Supabase + Render env vars)

**Files:**
- None (infrastructure setup — no code changes)

This task has no automated test; it's the manual step that makes Tasks 1-12 actually
work in production.

- [ ] **Step 1: Create a Supabase project**

Go to supabase.com, create a free project. Copy its Postgres connection string
(Project Settings → Database → Connection string → URI).

- [ ] **Step 2: Set Render environment variables**

In the Render dashboard for the `au-decorating-site` service, add:
- `DATABASE_URL` — the Supabase connection string from Step 1
- `ANTHROPIC_API_KEY` — from console.anthropic.com
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` — from a Twilio
  account (needs a purchased UK number to send SMS with a UK sender)
- `ADMIN_SECRET` — any long random string (e.g. `openssl rand -hex 16`)
- `TICK_SECRET` — any long random string, different from `ADMIN_SECRET`
- `CONTENT_ENGINE_MODEL` — optional, defaults to `claude-haiku-4-5-20251001` if unset

- [ ] **Step 3: Add the matching GitHub Actions secret**

In the GitHub repo, add `TICK_SECRET` as a repository secret with the exact same
value used in Render (see Task 12, Step 2).

- [ ] **Step 4: Deploy and verify**

Trigger a Render deploy (push to `main` if auto-deploy is on). Once live, check the
Render logs for `marketing DB schema init skipped` — if that line does NOT appear,
the schema initialized successfully. Then manually trigger the GitHub Actions
workflow once (Actions tab → "Marketing follow-up tick" → "Run workflow") and
confirm it returns a 2xx response.

---

## Self-review notes

- **Spec coverage:** content engine (Task 3), lead persistence (Task 9), follow-up
  cadence/sending (Tasks 5-8), manual stop mechanism (Task 11), scheduler (Task 12)
  all map to spec requirements. Local SEO, blog, and social publishing are
  intentionally out of scope for this plan — they're sub-projects 3-5 in the spec
  and get their own plans later.
- **Type/signature consistency checked:** `fields` dict keys (`Name`, `Phone`,
  `Email`, `Area`, `Job`) match between `_lead_fields()` in `app.py`, `db.insert_lead`,
  and the follow-up `context` dict keys consumed by `content_engine.generate`
  (`name`, `job` — lowercase, since those are the prompt template's placeholders,
  intentionally distinct from the dict's capitalized DB-facing keys).
- **No placeholders:** every step has real, runnable code — no TODOs.
