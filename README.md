# Repost Radar

SmartEmploy's internal lead engine and CRM. The full spec is in
[`BUILD_PROMPT.md`](BUILD_PROMPT.md). It's being built one phase at a time.

| Phase | What | Status |
|---|---|---|
| 1 | Reed API collector | **built, waiting for a real-key check** |
| 2 | Repost detection, normalisation, scoring | not started |
| 3 | Prospect agencies + Companies House | not started |
| 4 | FastAPI endpoints | not started |
| 5 | CRM (Next.js) | not started |
| 6 | Make.com + Google Sheets | not started |

## Repo layout (so far)

```
engine/
  app/
    config.py        settings from env vars (.env supported)
    db.py            Supabase REST access (service role, server only) + in-memory repo for tests
    reed_client.py   Reed API client: paging, throttle, retry, call cap, redacted logs
    normalise.py     employer/title/location cleaning (Phase 2 hardens it)
    collect.py       run_search / run_all
  cli.py             command line
  tests/             pytest suite + fixtures/
  requirements.txt
supabase/
  migrations/001_init.sql     schema + Row Level Security
  migrations/002_collect.sql  upsert_listings() used by the collector
  seed.sql                    the 5 outreach templates
  tests/                      SQL checks run against a throwaway local Postgres
.env.example
```

## Phase 1: set up and run the collector locally

You need Python 3.12, a Reed API key and a Supabase project.

### 1. Get a Reed API key

Sign up at <https://www.reed.co.uk/developers/jobseeker>. The key arrives by
email.

### 2. Create the Supabase project and database

1. Create a project at <https://supabase.com> (pick the London region).
2. Open **SQL Editor**, paste the contents of each file below, and click
   **Run**, in this order:
   1. `supabase/migrations/001_init.sql`
   2. `supabase/migrations/002_collect.sql`
   3. `supabase/seed.sql`
3. Open **Project Settings → API** and copy the **Project URL** and the
   **service_role** key. The service_role key bypasses all security, so it
   only ever goes in the engine's `.env` and never in the browser or the CRM.

### 3. Configure

From the repo root:

```bash
cp .env.example .env
```

Fill in `REED_API_KEY`, `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
`.env` is git-ignored, so don't commit it.

### 4. Install

```bash
cd engine
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Save a real Reed response as the test fixture

```bash
python cli.py reed-sample --keywords "management accountant" --location Leeds
```

This makes one API call, overwrites `tests/fixtures/reed_search_sample.json`
(the committed one is a synthetic placeholder), and prints every field name
Reed actually returned. If the names differ from the ones in
`app/reed_client.py` (`ReedJob`), the model needs adjusting.

### 6. Add a search and run it

```bash
python cli.py add-search --label "Accountancy - Leeds" \
  --keywords "management accountant,financial accountant,finance manager,credit controller" \
  --location Leeds --radius 15
python cli.py run --search-id 1
python cli.py run-all            # every active search, one after another
```

A run prints its counts, for example:

```
run 1 (search 1): ok
  api_calls=9 listings_seen=412 new_listings=412
  employer=158 agency=254 reclassified=6
```

Each keyword is queried twice, once for direct employers and once for
agencies, and each query pages 100 results at a time. The run is capped at
`REED_MAX_CALLS_PER_RUN` calls (default 300). If it hits the cap, the run is
marked `error`, but every listing fetched up to that point is kept.

### 7. Check the Phase 1 acceptance criteria in Supabase

Run these in the SQL Editor after two runs of the same search:

```sql
-- run rows and counts
select id, status, api_calls, listings_seen, new_listings, error from runs order by id;

-- no duplicates (should return 0 rows), and last_seen_at moved
select job_id, count(*) from listings group by 1 having count(*) > 1;
select count(*) filter (where last_seen_at > first_seen_at) as seen_again, count(*) from listings;

-- employer vs agency split
select posted_by, count(*) from listings group by 1;
```

To force a company to count as an agency (or as an employer), add an override.
It applies from the next run:

```sql
insert into employer_overrides (employer_norm, is_agency) values ('harlow group', true);
```

`employer_norm` is the lowercased name with punctuation and Ltd/Limited/PLC/LLP
removed.

### Tests

```bash
cd engine
pytest -q
```

The suite covers paging, throttling, retry, the call cap, key redaction,
upsert and dedupe, posted_by classification and overrides. `test_sql.py` also
spins up a throwaway local Postgres, applies the migrations and seed, and checks
the upsert SQL, RLS and territory exclusivity. It is skipped automatically if
Postgres server binaries aren't installed. You can run it on its own with
`supabase/tests/run_local.sh`.

## Rules this codebase follows

- Official APIs only. No scraping.
- No candidate or job-seeker data.
- Secrets only in env vars. Keys are never logged: the Reed key travels in the
  Basic-auth header, httpx's own request logging is silenced, and a log filter
  masks any configured secret.
- Row Level Security is on for every table. After your first CRM login
  (Phase 5), add yourself as the owner:
  `insert into app_users (user_id, email) select id, email from auth.users where email = 'you@example.com';`
