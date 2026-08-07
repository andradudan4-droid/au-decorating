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
