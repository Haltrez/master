# Repost Radar

SmartEmploy's lead engine and CRM for UK recruitment agencies.

- **Hot leads.** Companies that keep reposting the same role, or keep an ad
  open for weeks, can't fill it. Those companies are leads for an agency.
- **Prospect agencies.** Agencies advertising in a niche and region are who
  you sell to. Companies House confirms each one is a real company and gives
  you its directors' names.
- **Role Match.** An agency has candidates waiting. You enter the roles and
  towns, with no names or CVs, and Role Match finds direct employers hiring
  those roles nearby.
- **The CRM** (password login). It has a Today page with the 100-a-day
  target, the pipeline, ready-to-send drafts, clients with exclusive
  territories (one agency per niche and region), and Ctrl/Cmd+K search.
- **Make.com** runs the daily schedule and fills each client's Google Sheet.

The full spec is in [`BUILD_PROMPT.md`](BUILD_PROMPT.md).

```
engine/      Python engine: collectors, detection, enrichment, Role Match, API (FastAPI)
crm/         Next.js CRM (the face of the system)
supabase/    SQL migrations + seeds (works on Supabase or plain Postgres)
make/        SCENARIOS.md: step-by-step Make.com build notes
deploy/      Caddyfile (automatic HTTPS)
scripts/     migrate.sh (applies the SQL once each)
docker-compose.yml   everything on one server
```

## How it fits together

```
Make.com (06:00 daily, 08:00 client delivery, 07:30 Riga digest)
        │  HTTPS + X-API-Key
        ▼
Reed API ─┐                      ┌── CRM (password login) ── you
Adzuna ───┼─► engine (FastAPI) ◄─┘      browser never sees the engine secret
Companies House, Brave, Hunter ┘   │
Employer careers pages (polite)    ▼
                                Postgres
```

## Deploy on Hetzner (about 30 minutes)

You need a Hetzner Cloud server (CX22 or bigger, Ubuntu 24.04) and a domain
where you can add DNS records.

1. **DNS.** Add two A records pointing at the server's IP address:
   `crm.yourdomain` and `api.yourdomain`.
2. **Install Docker on the server.** SSH in as root, then:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
3. **Get the code.**
   ```bash
   git clone <this repo> /opt/repost-radar && cd /opt/repost-radar
   ```
4. **Configure it.**
   ```bash
   cp .env.example .env
   nano .env
   ```
   Fill in:
   - `CRM_DOMAIN` and `ENGINE_DOMAIN`
   - `POSTGRES_PASSWORD`, `SESSION_SECRET` and `ENGINE_API_SECRET`. Make each
     one with `openssl rand -hex 32`.
   - `CRM_PASSWORD`: the password you'll log in with
   - `REED_API_KEY` and `COMPANIES_HOUSE_API_KEY`, plus `ADZUNA_*`,
     `BRAVE_API_KEY` and `HUNTER_API_KEY` if you have them

   Keys never go in the code. They live only in this `.env` file.
5. **Start it.**
   ```bash
   docker compose up -d --build
   ```
   The first build takes a few minutes. The database is created and migrated
   automatically. Caddy gets HTTPS certificates on its own.
6. **Open `https://crm.yourdomain`** and log in.
7. **Make.com.** Build the scenarios in [`make/SCENARIOS.md`](make/SCENARIOS.md),
   using `https://api.yourdomain` as `ENGINE_URL`.

**Updating later:**
```bash
cd /opt/repost-radar && git pull && docker compose up -d --build
```
New migrations apply automatically.

**Backups:**
```bash
docker compose --profile backup run --rm backup
```
This writes `backups/repostradar-<date>.dump`. Copy it off the server, or add
the command to cron.

**Without Make.com:**
```bash
docker compose --profile scheduler up -d
```
This runs the daily collection at 06:00 UK from the server itself.

**Using Supabase instead of the built-in Postgres:** set `DATABASE_URL` to
Supabase's connection string and `SUPABASE=1` in `.env`. Everything else is
the same.

**A stronger login:** run `cd crm && npm run hash-password -- 'your password'`
and put the result in `CRM_PASSWORD_HASH` instead of `CRM_PASSWORD`. Failed
logins are limited to 10 per 15 minutes per IP.

## Daily use

1. **Searches.** Add one per niche and region, for example
   "Accountancy - Leeds" with keywords like management accountant and finance
   manager. Click **Run now**, or let Make run them at 06:00.
2. **Hot leads** fill in as roles repost. A lead is hot when any of these
   is true:
   - the same role is posted 3+ times in 90 days
   - one ad stays open for 30+ days (Reed's own posted date counts, so this
     works from day one)
   - two copies run at once

   Agency ads never count. Neither do the same ad on Reed and Adzuna.
3. **Agencies** fill in from agency ads in each niche. Click **Companies
   House** on an agency to confirm it's a Ltd, LLP or PLC and add its
   directors. With Brave and Hunter keys set, it also finds the website and
   verified emails. Only emails Hunter marks "valid" count as verified.
4. **Today.** Open an agency from **Do next**, pick a template, **Copy**, send
   it, then **Log as sent**. Every email, call, LinkedIn DM and LinkedIn
   connect counts toward the 100.
5. **An agency replies with candidates.** Open **Role Match**, or the agency's
   **Candidates** tab, add a brief per role and town, then click **Find
   matches**. You can download the CSV or send it to their Google Sheet.
6. **Pilot.** Drag the agency to Pilot, pick its exclusive territories, and
   Make creates and fills its sheet every weekday.

## Rules built in

- **Official APIs only:** Reed, Adzuna, Companies House, postcodes.io, the
  Wayback Machine, Brave and Hunter. Job boards are never scraped.
- **Careers pages:** Role Match reads employers' **own** careers pages
  politely:
  - robots.txt is checked first
  - user agent `SmartEmployBot/1.0`
  - at most 1 request every 5 seconds per site, and 3 pages per employer
  - each employer is checked at most once a day
- **Email outreach** only goes to agencies confirmed on Companies House as
  Ltd, LLP or PLC with a trusted name match. "Not found" never counts as
  allowed.
- **Do not contact** and **Forget** are permanent. The email goes onto a
  suppression list (only a hash of it is kept) that every draft, export and
  Make flow respects.
- **Every email template** carries the sender footer and an opt-out line.
- **No candidate data.** Role Match briefs are role profiles only. The brief
  importer rejects fields like name, email, CV or salary.
- **Secrets:**
  - Keys stay in `.env` and are masked in logs.
  - The CRM talks to the engine server-side, so the engine secret never
    reaches the browser.
  - Every engine endpoint requires `X-API-Key`.

## Developing locally

**Engine** (Python 3.12):
```bash
cd engine && python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest -q
```
The engine has 202 tests. They run against a throwaway Postgres if Postgres
server binaries are installed; set `PG_BIN` or `TEST_DATABASE_URL` to point
at yours.

Useful CLI commands (they need `DATABASE_URL` in `.env`):
```bash
python cli.py add-search --label "Accountancy - Leeds" --keywords "management accountant,finance manager" --location Leeds
python cli.py run --search-id 1        # collect, then rebuild hot leads and agencies
python cli.py hot-leads --top 20
python cli.py agencies --search-id 1
python cli.py enrich --all-new         # Companies House (+ Brave/Hunter if keys set)
python cli.py brief import ../briefs.example.yaml
python cli.py employers check --all
python cli.py match --agency "MCM Recruitment Solutions" --out matches.csv
python cli.py reed-sample              # save a real Reed response as a test fixture
```
Without `DATABASE_URL`, the Role Match commands use a local JSON file.

**API:**
```bash
uvicorn app.main:app --reload
```
Set `ENABLE_DOCS=1` to see the interactive docs at `/docs`.

**CRM** (Node 22):
```bash
cd crm && npm install
ENGINE_URL=http://localhost:8000 ENGINE_API_SECRET=... SESSION_SECRET=$(openssl rand -hex 32) CRM_PASSWORD=dev npm run dev
```

**Full-stack smoke test.** This starts a throwaway Postgres, a fake Reed, the
engine and the CRM, then drives it with Playwright:
```bash
cd crm && npm run build && python3 -I e2e/stack.py
```

## Source notes

- **Reed:** HTTP Basic with the key as username. Search runs once for direct
  employers and once for agencies, with pages of 100, 1 request per second, up
  to 5 retries with backoff, and a cap of 300 calls per run.
- **Adzuna:** a second UK source that aggregates many job boards. Adzuna's
  terms ask for "Jobs by Adzuna" attribution and use of its links. It doesn't
  say who is an agency, so ads are classified by overrides, by agencies Reed
  has already identified, by big agency brands, and by agency words in the
  name.
- **Companies House:** search, profile and officers, cached for 30 days. A
  match below 0.85 confidence is flagged for review and never trusted.
- **Wayback Machine:** gives an "open since" date for matched job pages.
  Where it has no capture, the report shows "unknown".
- **ATS job boards** read through their public APIs: Greenhouse, Lever and
  Ashby. Breathe HR has no public API, so its vacancy pages are read from the
  employer's careers page.
