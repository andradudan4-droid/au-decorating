# Marketing Automation System — Design

## Context

The user is starting an agency-style venture: using Claude to run marketing automation
for local businesses, operating solo. The first (and currently only) client is AU
Decorating Ltd (au-decorating.com), a Portsmouth painting & decorating company run by
Mehmet Yildiz, whose website the user built and hosts (Flask app, deployed on Render,
repo at `au-decorating-site`).

The inspiration is a viral video showing a "five department" Claude-powered marketing
system (social, local SEO, content, ads, ops) sitting under an orchestrating "AI CMO."
This spec scopes a realistic, sequenced version of that idea against the real business
and the real, already-partially-built codebase.

## What already exists (do not rebuild)

`app.py` already has a working lead-capture system:
- A chat widget (iframe + bubble) that interviews visitors using **Groq/Llama 3.3**
  (this stays on Groq — not migrating to Claude).
- Regex-based safety-net extraction of phone/email/postcode from the conversation.
- A single automatic email to Mehmet (via Resend) once the chat signals `[[READY]]`,
  containing a structured summary, full transcript, and any attached job photos.
- A privacy policy that already discloses Groq and Resend as data processors, and
  already states the business "may contact you by phone, text, WhatsApp or email to
  follow up on your enquiry" — this covers the automated follow-up in sub-project 2
  without needing a policy rewrite.

What's genuinely missing: **persistence** (everything lives in in-memory Python dicts,
wiped on every restart/redeploy — there is no database at all), **follow-up** (exactly
one email is sent, then nothing; this is the "leads go cold" problem), and all content
generation / SEO / social capability.

## Goals

Build a sequenced set of sub-projects that give AU Decorating (and, as a reusable
pattern, the user's other `*-site` client projects later) automatic marketing
execution: generating leads (local SEO, content, social) and making sure leads that
do arrive get chased until they respond.

## Out of scope

- **Invoicing / bookkeeping.** Explicitly deferred. Real financial record-keeping for
  a UK business carries tax/compliance weight (VAT, HMRC Making Tax Digital); the
  sane path is integrating with established accounting software (Xero/QuickBooks/
  FreeAgent) later, not building bookkeeping logic from scratch.
- Migrating the existing lead-chat model off Groq.
- Full two-way reply detection for the follow-up sequence (see sub-project 2 — a
  manual "mark as replied" step is the v1 approach; auto-detecting replies is a
  reasonable fast-follow, not day one).

## Build order

1. **Content engine** — foundation; everything else consumes it.
2. **Lead persistence + follow-up** — closes the "leads go cold" gap; fastest to ship
   since the capture side already works.
3. **Local SEO** (GBP posts, review-reply drafts, rank tracking) — start the Google
   Business Profile API access application as early as possible in parallel, since
   Google's approval has the longest lead time of anything in this plan.
4. **Blog publishing** — feeds local SEO with fresh, location-specific content.
5. **Social publishing** (Instagram `audecoratinguk` + Facebook via Meta Graph API) —
   lowest urgency; Meta app/tester setup should also start early given its own
   lead time, even though the build work lands last.

## Decisions made

- **Operating model:** agency — the user is the sole operator running this for
  clients, starting with AU Decorating.
- **Publishing mode: full autopilot.** Generated content (blog posts, social
  captions, follow-up messages) publishes/sends with no human review step. This was
  an explicit user choice against the recommendation of a review-before-publish
  queue. **Risk:** an off-brand post or a wrong automated message to a real customer
  can't be caught before it goes out. Mitigation: every generated item is still
  logged to `content_items`/`lead_followups` with full content and a timestamp, so
  there's an audit trail and a way to manually retract/correct after the fact, even
  though nothing blocks it beforehand.
- **Repo/location:** new code lives inside the existing `au-decorating-site` repo, in
  a new `marketing/` package, sharing one Flask deployment. Chosen for tight coupling
  (shared DB, shared site routes for the blog) over a separate service.
- **Database:** none currently exists — `all_conversations` etc. are in-memory and
  wiped on restart. Render's free-tier disk isn't persistent either. **Recommendation:
  Supabase's free Postgres tier** (persists indefinitely; Render's free Postgres
  expires after 90 days). New tables: `leads`, `lead_followups`, `content_items`.
- **Scheduler:** Render's free tier can't run a persistent background cron. A
  secret-protected `/internal/tick` endpoint does the periodic work (check
  follow-ups due, generate today's content), triggered externally via a free
  GitHub Actions scheduled workflow or cron-job.org.
- **Social platforms (v1):** Instagram (`audecoratinguk`) + Facebook via Meta Graph
  API. Posting to the business's own accounts as an "Instagram Tester" in the Meta
  Developer dashboard avoids full App Review for the initial build.

## Sub-project 1: Content engine

`marketing/content_engine.py` — one function, `generate(content_type, context)`,
backed by the Claude API.

- **Knowledge base** (`marketing/knowledge/`): `brand.md`, `services.md`,
  `service_area.md` — derived from the existing `SYSTEM_PROMPT` in `app.py` so the
  generated voice matches the existing chat rather than drifting from it.
- **Prompt templates** (`marketing/prompts/`): one per content type — blog post,
  Instagram caption, Facebook caption, GBP post, review reply, follow-up message.
- Approach is flat-file context now (no vector DB / RAG) — there's no content
  history yet for retrieval to help with. Structured so a retrieval layer can be
  added later without changing the calling interface.

## Sub-project 2: Lead persistence + follow-up

- `leads` table: populated by hooking directly into the existing `send_lead_email()`
  call site — the moment a lead email fires, also insert the structured lead fields
  into the DB.
- `lead_followups` table: log of every automated follow-up sent (channel, content,
  timestamp).
- Scheduled check (via `/internal/tick`): finds leads with no reply and due for their
  next follow-up, generates a personalised message via the content engine, sends via
  Resend (email) and Twilio (SMS, if a phone number was captured). Cadence (matches
  the step-based pattern from the reference video): follow-up 1 at 24h after the
  initial lead email, follow-up 2 at 3 days, follow-up 3 at 7 days, then stop
  automatically — after that it's down to Mehmet to chase manually if he still wants
  to. Each step's tone should soften/shorten rather than repeat the same message.
- **Stopping condition (v1):** manual — a small internal `/admin` page lets Mehmet
  or the user mark a lead as replied/booked, which halts further follow-ups.
  Automatic reply detection (e.g. parsing inbound email/SMS) is a real feature to
  revisit once this is proven out, not part of this build.

## Sub-project 3: Local SEO

- Google Business Profile API integration for: drafting review replies, publishing
  GBP posts (generated by the content engine), and tracking local ranking.
- **Prerequisite:** apply for GBP API access immediately — Google gates this behind
  an application even though the listing itself is already claimed/verified, and
  approval can take days.

## Sub-project 4: Blog publishing

- New Flask routes + `content_items`-backed pages on au-decorating.com.
- Content engine generates posts (e.g. "Interior Painting in Southsea: Before &
  After") from completed-job context; published automatically per the full-autopilot
  decision above.

## Sub-project 5: Social publishing

- Meta Graph API integration posting to Instagram (`audecoratinguk`) and the
  business's Facebook Page.
- Same generated content (or platform-adapted variants) as the blog/GBP posts,
  via the content engine's per-content-type templates.

## Open questions / risks flagged, not yet resolved

- Full autopilot means a bad generated message could reach a real customer with
  nobody catching it first — accepted risk per user's explicit choice, mitigated
  only by logging, not prevention.
- GBP API access approval timeline is unknown until the application is actually
  submitted — sub-project 3 timing depends on it.
- Meta Tester-mode posting avoids App Review for now, but scaling this pattern to
  the user's other client sites later would likely require going through full App
  Review.
