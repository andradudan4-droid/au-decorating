# Local SEO — Design

## Context

Sub-project 3 in the marketing automation build order (see
`2026-08-07-marketing-automation-design.md`), following the content engine and
lead follow-up system (shipped and deployed 2026-08-08). Covers local SEO for
AU Decorating: Google Business Profile content generation, review-reply
drafting, and local search rank tracking.

## The Google Business Profile API blocker

Google gates the Business Profile API behind a manual access-request form,
even though AU Decorating's GBP listing is already claimed and verified.
Approval time is unknown and could take days. Rather than block this
sub-project on that approval, it's split into two phases:

- **Phase A (this plan):** content generation with manual copy-paste
  publishing, plus rank tracking (neither needs GBP API access). Ships now.
- **Phase B (later, blocked on Google approval):** auto-publishing GBP posts
  and review replies directly via the API, replacing the copy-paste step.
  Starting the GBP API application itself is a manual step on the user's
  Google account — not part of this build, tracked separately.

## Scope (Phase A)

1. **GBP post generator** — an admin page where the user describes a
   completed job or event in free text (e.g. "repainted a Victorian terrace
   exterior in Southsea"); the content engine drafts a Google Business
   Profile post, shown for copy-paste into Google manually.
2. **Review reply generator** — same page or adjacent: paste in a review's
   text, reviewer name, and star rating; get a drafted reply in the
   established brand voice, shown for copy-paste.
3. **Local rank tracking** — weekly automated check of AU Decorating's
   position in Google's local 3-pack (map results) for 8 fixed keywords,
   via SerpApi's free tier (100 searches/month; 8 keywords × weekly = ~32/
   month, comfortably under the limit). Viewable on an admin page.
4. **Sitemap submission to Google Search Console** — a one-time manual step
   (not code): submit the site's existing `/sitemap.xml` in Search Console,
   using the Google account that set up the existing site-verification meta
   tag already present in `app.py`'s `<head>`. Walked through separately
   from this build, like the earlier Supabase/Render/GitHub setup steps.

## Out of scope (this plan)

- GBP API auto-publishing (Phase B, blocked on Google approval).
- Automated job-logging (there's no "completed jobs" database yet — the GBP
  post generator takes free-text input on demand rather than triggering
  automatically from a job record; that's a natural fit for sub-project 4
  (blog) later, not built here).
- Rank tracking beyond the local 3-pack (e.g. full organic ranking) — the
  local pack position is what's actually actionable for a local trades
  business, and keeping SerpApi usage low keeps it free.

## Keywords tracked

Portsmouth and Waterlooville only, per the user's decision (Fareham and
Gosport excluded even though they're in the service area, to keep the
free-tier search budget under 100/month with room to grow):

`painter portsmouth`, `decorator portsmouth`,
`painters and decorators portsmouth`, `house painter portsmouth`,
`painter waterlooville`, `decorator waterlooville`,
`painters and decorators waterlooville`, `interior painter portsmouth`

## Data model (new tables)

- `content_items`: `id`, `content_type` (`'gbp_post'` | `'review_reply'`),
  `input_context` (the free text or review text typed in), `generated_text`,
  `created_at`. Every generated draft is logged here — nothing is only ever
  shown once and lost, and this becomes the audit trail Phase B's
  auto-publish path will read from later.
- `keyword_rankings`: `id`, `keyword`, `position` (nullable integer — NULL
  means not found in the local 3-pack for that check), `checked_at`.

## New content engine prompt templates

- `gbp_post.md`: short (Google Business Profile posts read best under ~300
  words), keyword-natural given the input description, ends with a soft
  call-to-action. Same knowledge-base grounding (brand voice, services,
  service area) as the existing `follow_up.md`.
- `review_reply.md`: short, warm reply in the established brand voice,
  referencing specifics from the review text, tone calibrated to the star
  rating (grateful and brief for 5-star; gracious and addresses the concern
  without being defensive for anything lower).

## Rank tracking implementation

`marketing/rank_tracking.py`: `check_ranking(keyword) -> int | None`, using
SerpApi's Google Search endpoint with a UK/Portsmouth-area location
parameter, parsing the `local_results` (map pack) block for a listing
matching "AU Decorating" by name, returning its 1-based position or `None`
if it doesn't appear. New env var: `SERPAPI_API_KEY`.

## New routes

- `GET /admin/content` — form (content type + free-text input) and a list of
  recently generated items, admin-secret-protected like `/admin/leads`.
- `POST /admin/content/generate` — runs the content engine, saves to
  `content_items`, redisplays with the new draft ready to copy.
- `GET /admin/rankings` — table of the 8 keywords with latest position and
  a short history, admin-secret-protected.
- `POST /internal/rank-check` — secret-protected (reusing `TICK_SECRET`,
  since this is the same class of "internal scheduled job" as
  `/internal/tick`), checks all 8 keywords via SerpApi, records to
  `keyword_rankings`.

## Scheduler

A second GitHub Actions workflow, weekly (not hourly), calling
`/internal/rank-check` the same way `marketing-tick.yml` calls
`/internal/tick`.

## Risks / notes

- SerpApi's free tier is 100 searches/month with no card required; if usage
  needs to grow later (more keywords, more frequent checks), it's a paid
  upgrade at that point, not a blocker now.
- Full autopilot does NOT apply to Phase A output — GBP posts and review
  replies are drafts requiring a manual copy-paste by design (there's no API
  access yet to auto-publish even if we wanted to), so this phase carries
  none of the "wrong message sent automatically" risk the lead follow-up
  system accepted.
