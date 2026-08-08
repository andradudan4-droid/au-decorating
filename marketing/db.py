import os

import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    # connect_timeout bounds the wait on a slow or unreachable database, so a
    # DB outage can't hang site startup or a live request indefinitely.
    return psycopg2.connect(
        os.environ["DATABASE_URL"], cursor_factory=RealDictCursor, connect_timeout=5
    )


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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS content_items (
                    id SERIAL PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    input_context TEXT NOT NULL,
                    generated_text TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS keyword_rankings (
                    id SERIAL PRIMARY KEY,
                    keyword TEXT NOT NULL,
                    position INTEGER,
                    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
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


def find_active_lead(phone, email):
    """Return an existing active lead matching this phone or email, else None.

    Used to stop a repeat chat session from creating a second lead row for the
    same person, which would enrol them in a duplicate follow-up sequence.
    NULL-safe: a None phone never matches a stored NULL phone.
    """
    if not phone and not email:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM leads
                WHERE status = 'contacted'
                  AND ((%(phone)s IS NOT NULL AND phone = %(phone)s)
                       OR (%(email)s IS NOT NULL AND email = %(email)s))
                ORDER BY created_at DESC
                LIMIT 1
                """,
                {"phone": phone, "email": email},
            )
            return cur.fetchone()
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


def get_leads_with_followup_counts():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.id, l.name, l.phone, l.email, l.area, l.job, l.status,
                       l.created_at, l.last_contacted_at,
                       COUNT(f.id) AS followup_count
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


def insert_content_item(content_type, input_context, generated_text):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO content_items (content_type, input_context, generated_text)
                VALUES (%(content_type)s, %(input_context)s, %(generated_text)s)
                RETURNING id
                """,
                {
                    "content_type": content_type,
                    "input_context": input_context,
                    "generated_text": generated_text,
                },
            )
            item_id = cur.fetchone()["id"]
        conn.commit()
        return item_id
    finally:
        conn.close()


def list_content_items(limit=20):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM content_items ORDER BY created_at DESC LIMIT %(limit)s",
                {"limit": limit},
            )
            return cur.fetchall()
    finally:
        conn.close()


def record_ranking(keyword, position):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO keyword_rankings (keyword, position)
                VALUES (%(keyword)s, %(position)s)
                """,
                {"keyword": keyword, "position": position},
            )
        conn.commit()
    finally:
        conn.close()


def get_latest_rankings():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (keyword) keyword, position, checked_at
                FROM keyword_rankings
                ORDER BY keyword, checked_at DESC
                """
            )
            return cur.fetchall()
    finally:
        conn.close()
