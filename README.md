# Repost Radar

SmartEmploy's internal lead engine and CRM. The full spec is in
[`BUILD_PROMPT.md`](BUILD_PROMPT.md). It's being built one phase at a time.

| Phase | What | Status |
|---|---|---|
| 1 | Reed API collector | **built, waiting for a real-key check** |
| 2.5 | Role Match MVP (CLI) | **built, ran live against the 3 seed employers** |
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

## Phase 2.5: Role Match (CLI)

Role Match finds **direct employers currently hiring the roles an agency's
candidates do**, and ranks them by how stuck the employers look. A *candidate
brief* is a role profile only: role, location, radius, sector keywords and
seniority. It never holds names, CVs, contact details or salaries. The brief
file rejects keys like `name`, `email` or `salary`.

### Run it

```bash
cd engine
source .venv/bin/activate && pip install -r requirements.txt    # adds PyYAML
python cli.py brief import ../briefs.example.yaml                # the six MCM briefs
python cli.py employers check --all                              # careers pages + ATS boards, politely
python cli.py match --agency "MCM Recruitment Solutions" --out matches.csv
```

Other commands:

```bash
python cli.py brief add --agency "MCM Recruitment Solutions" --role "Electrical Estimator" --location Warrington --radius 30
python cli.py brief list --agency "MCM Recruitment Solutions"
python cli.py employers add --name "Acme M&E Ltd" --careers-url https://acme.example/careers --town Bolton
python cli.py employers list [--status research]
python cli.py employers check --id 3 [--force]        # --force skips the weekly schedule, never the once-a-day limit
python cli.py employers import-ch --location Manchester  # Companies House SIC search -> research queue
python cli.py match --agency "..." --check --no-reed      # check due employers first; skip Reed
```

**Where data goes:** if `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set,
Role Match uses Supabase. In that case run `supabase/migrations/003_role_match.sql`
and then `supabase/seed_role_match.sql` in the SQL editor first. Otherwise it
uses a local file, `engine/.role_match_local.json` (git-ignored), which comes
seeded with the role families and the three seed employers. You can force one
or the other with `python cli.py --store local|supabase ...`.

### Sources, in order of trust

1. **Employer careers pages.** The extractor reads JSON-LD `JobPosting` data
   when a page has it. Otherwise it finds job-title-shaped headings and labels
   and pairs each with its own link (Apply button, job page, or .docx advert).
   Bullet lists of "roles we're always looking for" with no links are ignored.
   So are named people, such as staff profiles.
2. **ATS job boards** with documented, public, keyless APIs:
   - Greenhouse (`boards-api.greenhouse.io/v1/boards/{token}/jobs`)
   - Lever (`api.lever.co/v0/postings/{site}?mode=json`)
   - Ashby (`api.ashbyhq.com/posting-api/job-board/{name}`)

   Breathe HR's API needs the company's own key, so Breathe vacancies are read
   from the careers page and Breathe's public `/v/` pages.
3. **Reed API, direct employers only.** This needs `REED_API_KEY`. Reed is
   searched for each exact title in the role family, at 1.5× the brief's
   radius. Direct employers become matches and join the directory as
   `research` rows. Agency ads, including employer-query results whose names
   look like agencies, are never matches. They're counted as **market demand**.
4. **Wayback Machine CDX.** This gives the earliest capture of each matched
   job URL, cached for 30 days. With no capture, the report says `unknown`.
   It never guesses.

### Polite fetching (enforced in `app/polite.py`, tested in `tests/test_polite.py`)

- robots.txt is checked with `urllib.robotparser` before every domain. Rules
  are ordered so the longest match wins, per RFC 9309. A disallowed careers
  page marks the employer `blocked_by_robots`.
- User agent `SmartEmployBot/1.0 (+https://smartemploy.io/bot)`.
- At most 1 request per 5 seconds per domain, robots.txt requests included.
- At most 3 pages per employer per check: the careers page plus up to 2 job
  pages.
- Each employer is checked at most once a day, and weekly by default.
- Job boards (Indeed, Totaljobs, CV-Library, LinkedIn, Reed web pages, …) are
  refused outright.
- Pages that only work with JavaScript are marked `needs_manual`. There is no
  headless browser.
- Per job, only the title, location text, URL, first/last seen, a hash of the
  page text, the employer's own posted date and which sector keywords appear
  are stored. Page text, names and emails are never stored.

### matches.csv

The columns are `candidate_role, company, their_open_role, location,
distance_miles, open_since, signal, link, source, score`. Rows are sorted by
candidate role, then score. A market-demand row per role follows the matches.
When an ad doesn't state a location, the company's base town is used and
labelled `(company base)`.

Scoring follows the spec table: exact +4, related +2, within radius +2
(within 1.5× +1, further out is excluded), open 30+ days +2, reposted on Reed
+2, 2+ roles in the family at one employer +1, sector keyword +1, probably
filled −3.

## Rules this codebase follows

- Official APIs only, with one exception added in Phase 2.5: employers' own
  careers pages and ATS boards, fetched under the polite rules above. Job
  boards are never scraped.
- No candidate or job-seeker data.
- Secrets only in env vars. Keys are never logged: the Reed key travels in the
  Basic-auth header, httpx's own request logging is silenced, and a log filter
  masks any configured secret.
- Row Level Security is on for every table. After your first CRM login
  (Phase 5), add yourself as the owner:
  `insert into app_users (user_id, email) select id, email from auth.users where email = 'you@example.com';`
