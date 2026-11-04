# Repost Radar Build Prompt for Claude Code

Nov 3, 2026 · @Buda

One prompt that builds SmartEmploy's internal engine: a Python collector
on the Reed API, repost detection, a prospect-agency list, your own CRM
as the front end, and Make.com feeding Google Sheets for clients.

## How to use this prompt, and the context

Export this doc as Markdown, save it as BUILD_PROMPT.md in an empty
repo, and start Claude Code with: *"Read BUILD_PROMPT.md and build
Phase 1. Stop when Phase 1 passes its acceptance checks."* Then do one
phase per session.

> **Instructions to Claude Code**
>
> You are building "Repost Radar", the internal lead engine and CRM for
> SmartEmploy, a one-person business that sells done-for-you business
> development to UK recruitment agencies. Work phase by phase. At the
> end of each phase: run the tests, show me exactly how to run it
> locally, list anything you assumed, and wait for my OK before starting
> the next phase. If something is not specified here, ask me instead of
> guessing. Never hardcode secrets. Use official APIs only, never HTML
> scraping.

**What the business does (so the code makes sense):**

1.  A UK company that posts the same job again and again can't fill it.
    That's a **hot lead**: a company that needs a recruitment agency.

2.  Recruitment agencies that advertise in the same niche and city are
    **prospects**: the people SmartEmploy sells to.

3.  SmartEmploy emails a prospect agency with real hot leads from its
    niche ("Harlow Group has posted Management Accountant 4 times since
    August"), offers 20 free leads, then a free 30-day pilot, then a
    paid done-for-you service at £1,250/month where SmartEmploy books
    meetings for the agency.

4.  Paying and pilot clients get their hot leads delivered daily into
    their own Google Sheet.

5.  One agency per niche per region: territories are exclusive.

**The owner works this system every day.** The CRM is the face. He opens
it in the morning, sees his daily scorecard (target: 100 outreach
actions a day), the agencies to contact, drafted emails ready to copy,
and follow-ups due.

## Architecture, stack and repo structure

Three parts share one database: a Python engine that collects and
analyses, a CRM web app that is the face, and Make.com that runs
schedules and fills Google Sheets.

Make.com (scheduler + Google Sheets)  
\| daily: POST /run-all  
\| daily: GET /clients/{id}/daily-leads -\> client Google Sheet  
v  
Reed API ---\> Python engine (FastAPI) ---\> Supabase Postgres \<--- CRM
(Next.js)  
Companies House --^ collect, detect, (single source the face:  
enrich, export of truth) scorecard, agencies,  
hot leads, drafts

| Layer           | Choice                                                             | Notes                                                         |
|-----------------|--------------------------------------------------------------------|---------------------------------------------------------------|
| Engine          | Python 3.12, FastAPI, httpx, pydantic, pytest                      | One package, also runnable from the CLI                       |
| Database        | Supabase (Postgres + Auth)                                         | Row Level Security on every table                             |
| CRM             | Next.js (App Router, TypeScript), Tailwind, shadcn/ui, Supabase JS | Same stack as smartemploy.io                                  |
| Automation      | Make.com                                                           | Calls the engine over HTTPS with a secret header              |
| Client delivery | Google Sheets                                                      | One sheet per client, written by Make.com                     |
| Hosting         | Engine on Render or Railway, CRM on Vercel                         | Pick an engine plan that doesn't sleep, or accept cold starts |

**Repo structure:**

repost-radar/  
engine/  
app/  
main.py \# FastAPI app and routes  
config.py \# settings from env vars  
db.py \# Supabase/Postgres access  
reed_client.py \# Reed API client  
ch_client.py \# Companies House client  
collect.py \# run a saved search, store listings  
normalise.py \# employer/title/location cleaning  
detect.py \# repost detection and scoring  
agencies.py \# prospect agency building + enrichment  
templates.py \# email draft rendering  
exports.py \# CSV exports  
cli.py \# python cli.py run --search-id 1  
tests/  
fixtures/ \# real API responses saved as JSON  
requirements.txt  
crm/ \# Next.js app  
supabase/  
migrations/ \# SQL migrations  
seed.sql  
make/  
SCENARIOS.md \# step-by-step Make.com build notes  
.env.example  
README.md

## Database schema (Supabase Postgres)

Create this as supabase/migrations/001_init.sql. Adjust types if the
real Reed response demands it, and tell me what you changed.

-- Saved searches = niche + region territories  
create table searches (  
id bigserial primary key,  
label text not null, -- e.g. 'Accountancy - Leeds'  
keywords text\[\] not null, -- each keyword is queried separately  
location text not null,  
radius_miles int not null default 15,  
min_salary int,  
active boolean not null default true,  
created_at timestamptz not null default now(),  
last_run_at timestamptz  
);  
  
create table runs (  
id bigserial primary key,  
search_id bigint references searches(id),  
started_at timestamptz not null default now(),  
finished_at timestamptz,  
status text not null default 'running', -- running \| ok \| error  
api_calls int default 0,  
listings_seen int default 0,  
new_listings int default 0,  
error text  
);  
  
-- Every job ad ever seen. job_id is Reed's id.  
create table listings (  
job_id bigint primary key,  
source text not null default 'reed',  
posted_by text not null check (posted_by in ('employer','agency')),  
employer_id bigint,  
employer_name text not null,  
employer_norm text not null,  
title text not null,  
title_norm text not null,  
location_name text,  
location_norm text,  
min_salary numeric,  
max_salary numeric,  
url text,  
posted_date date,  
expiration_date date,  
first_seen_at timestamptz not null default now(),  
last_seen_at timestamptz not null default now(),  
raw jsonb not null  
);  
create index on listings (employer_norm, title_norm, location_norm);  
create index on listings (posted_by, last_seen_at);  
  
create table listing_searches (  
job_id bigint references listings(job_id) on delete cascade,  
search_id bigint references searches(id) on delete cascade,  
primary key (job_id, search_id)  
);  
  
-- Manual overrides: force an employer to count as agency or employer  
create table employer_overrides (  
employer_norm text primary key,  
is_agency boolean not null  
);  
  
-- Recomputed after every run  
create table hot_leads (  
lead_key text primary key, --
sha1(employer_norm\|title_norm\|location_norm)  
employer_name text not null,  
employer_norm text not null,  
title text not null,  
location_name text,  
posting_count_90d int not null,  
first_seen_at timestamptz not null,  
last_seen_at timestamptz not null,  
live_now boolean not null,  
long_open boolean not null,  
score int not null,  
latest_url text,  
job_ids bigint\[\] not null,  
status text not null default 'new', -- new \| used \| ignored  
updated_at timestamptz not null default now()  
);  
create table hot_lead_searches (  
lead_key text references hot_leads(lead_key) on delete cascade,  
search_id bigint references searches(id) on delete cascade,  
primary key (lead_key, search_id)  
);  
  
-- Prospects: recruitment agencies  
create table agencies (  
id bigserial primary key,  
name text not null,  
name_norm text not null unique,  
website text,  
ch_company_number text,  
ch_company_type text, -- ltd \| llp \| plc \| unknown ...  
ch_company_status text,  
ch_match_confidence numeric,  
ch_checked_at timestamptz,  
stage text not null default 'new',  
-- new \| researching \| contacted \| replied \| call_booked \| pilot \|
paid \| lost \| do_not_contact  
ads_count_90d int not null default 0,  
next_follow_up date,  
notes text,  
created_at timestamptz not null default now()  
);  
create table agency_searches (  
agency_id bigint references agencies(id) on delete cascade,  
search_id bigint references searches(id) on delete cascade,  
ads_count int not null default 0,  
primary key (agency_id, search_id)  
);  
  
create table contacts (  
id bigserial primary key,  
agency_id bigint references agencies(id) on delete cascade,  
full_name text not null,  
first_name text,  
role text, -- e.g. Director (from Companies House)  
email text,  
email_verified boolean default false,  
linkedin_url text,  
source text not null, -- companies_house \| manual \| website  
do_not_contact boolean not null default false,  
created_at timestamptz not null default now()  
);  
  
create table activities (  
id bigserial primary key,  
agency_id bigint references agencies(id) on delete cascade,  
contact_id bigint references contacts(id),  
type text not null check (type in
('email','call','linkedin_dm','linkedin_connect','note','meeting')),  
counts_toward_daily boolean not null default true,  
happened_at timestamptz not null default now(),  
notes text  
);  
  
create table templates (  
id bigserial primary key,  
name text not null,  
channel text not null check (channel in
('email','linkedin','client_hm_email')),  
subject text,  
body text not null  
);  
  
create table clients (  
id bigserial primary key,  
agency_id bigint references agencies(id),  
plan text not null check (plan in
('pilot','founding','done_for_you')),  
monthly_price_gbp numeric not null default 0,  
start_date date not null,  
end_date date,  
sheet_url text,  
delivery_email text,  
status text not null default 'active' -- active \| paused \| ended  
);  
  
-- Exclusivity: a search (niche+region) can belong to one client only  
create table client_territories (  
client_id bigint references clients(id) on delete cascade,  
search_id bigint references searches(id) unique,  
primary key (client_id, search_id)  
);  
  
create table deliveries (  
id bigserial primary key,  
client_id bigint references clients(id) on delete cascade,  
lead_key text references hot_leads(lead_key),  
delivered_at timestamptz not null default now(),  
outcome text not null default 'none', -- none \| contacted \| meeting \|
placement  
unique (client_id, lead_key)  
);  
  
-- Daily scorecard for the CRM home page  
create view scorecard_daily as  
select date_trunc('day', happened_at)::date as day,  
count(\*) filter (where counts_toward_daily) as outreach_actions,  
count(\*) filter (where type = 'meeting') as meetings  
from activities group by 1;

Enable Row Level Security on every table. For now there is one user (the
owner): allow all operations only for that authenticated user id, set
via an env var or a small app_users table. The engine uses the service
role key server-side only.

## Phase 1: Reed API collector

Goal: run a saved search, page through every result for each keyword,
twice (direct employers, then agencies), and store every listing with
first-seen and last-seen dates. **This phase ships first because repost
detection needs history, and history only exists from the day collection
starts.**

**Reed Jobseeker API facts (from reed.co.uk/developers/jobseeker):**

- Search endpoint: GET https://www.reed.co.uk/api/1.0/search

- Details endpoint: GET https://www.reed.co.uk/api/1.0/jobs/{jobId}

- Auth: HTTP Basic, API key as the username, empty password

- Search parameters used here: keywords, locationName,
  distanceFromLocation (miles, default 10), minimumSalary,
  postedByDirectEmployer (true/false), postedByRecruitmentAgency
  (true/false), resultsToTake (max 100), resultsToSkip (for paging)

- Search results include job id, employer id, employer name, employer
  profile id, job title, location, min/max salary. Details add
  expiration date, contract type, job type and URLs.

**Before writing the parser:** make one real call with my key, save the
raw JSON to engine/tests/fixtures/reed_search_sample.json, print the
exact field names, and build the pydantic model from what actually comes
back. Do not assume field names beyond the docs.

**reed_client.py requirements:**

- search(keywords, location, radius, min_salary, posted_by) where
  posted_by is employer or agency, mapping to the two boolean parameters

- Pages with resultsToTake=100 and resultsToSkip until a page returns
  fewer than 100 results or the total is reached

- Throttle to 1 request per second by default (configurable). Retry 429
  and 5xx with exponential backoff, max 5 tries

- Hard cap of API calls per run (configurable, default 300) so a bad
  search can't burn the quota

- get_details(job_id) used only for hot leads in Phase 2, never for
  every listing

- Log every call: url params (no key), status, result count, duration

**collect.py: run_search(search_id)**

1.  Create a runs row with status running

2.  For each keyword in the search, call search with posted_by=employer,
    then with posted_by=agency

3.  Upsert each result into listings: insert with first_seen_at=now(),
    or on conflict update last_seen_at=now() and raw. Set posted_by from
    which query returned it

4.  Upsert listing_searches

5.  Apply employer_overrides: if an employer name is overridden as
    agency, store posted_by='agency' regardless of which query returned
    it

6.  Also apply an agency-name heuristic to posted_by='employer' results:
    names containing recruit, recruitment, staffing, resourcing,
    personnel, talent, search partners, appointments, selection, careers
    → mark as agency. Keep the word list in config and log every
    reclassification

7.  Close the run with counts, or status='error' and the message

**cli.py:** python cli.py run --search-id 1, python cli.py run-all,
python cli.py add-search --label "Accountancy - Leeds" --keywords
"management accountant,financial accountant,finance manager,credit
controller" --location Leeds --radius 15

**Phase 1 acceptance checks:**

- [ ] One real search runs end to end and the run row shows correct
  counts

- [ ] Running the same search twice creates no duplicate listings and
  moves last_seen_at

- [ ] Employer and agency results are stored with the right posted_by

- [ ] API key never appears in logs

- [ ] Tests pass for paging, retry and upsert logic using the saved
  fixture

## Phase 2: repost detection, normalisation and scoring

Goal: after every run, rebuild hot_leads from employer listings so the
CRM always shows which companies can't fill which roles.

**normalise.py** (pure functions, heavily unit-tested):

- employer_norm(name): lowercase, strip punctuation, remove trailing
  legal suffixes (ltd, limited, plc, llp, uk ltd), collapse spaces. Keep
  words like "group" and "holdings"

- title_norm(title): lowercase, remove anything in brackets, remove
  salary and location fragments, remove noise words (urgent, immediate
  start, hybrid, remote, x2, ftc, maternity cover), collapse spaces.
  **Keep seniority words** (senior, junior, lead, head, assistant): a
  Senior Accountant and an Accounts Assistant are different roles

- location_norm(location_name): lowercase main town or city

- lead_key(employer_norm, title_norm, location_norm): sha1 of the three
  joined with \|

Write at least 25 test cases from real titles in the fixtures, including
tricky ones like "Management Accountant - Hybrid (Leeds) £45k" and
"Senior Management Accountant".

**detect.py: rebuild_hot_leads()**

For all listings where posted_by='employer', grouped by lead_key, over
the last 90 days (configurable):

| Signal            | Rule                                                                                                              |
|-------------------|-------------------------------------------------------------------------------------------------------------------|
| posting_count_90d | Number of distinct job_ids for the key first seen in the window                                                   |
| live_now          | Any job_id for the key seen in the latest run                                                                     |
| long_open         | Any single job_id with last_seen_at - first_seen_at \>= 30 days                                                   |
| Day-one signal    | 2+ distinct job_ids for the same key live at the same time (lets the system work before 90 days of history exist) |

**Hot if:** posting_count_90d \>= REPOST_THRESHOLD (default 3) **or**
long_open **or** the day-one signal.

**Score** (higher = hotter): 2 × (posting_count_90d − 1) + 2 if
long_open + 1 if the same employer has 3+ hot keys + 1 if live_now.

Upsert into hot_leads, keeping any existing status the owner set by
hand. Fill hot_lead_searches from listing_searches. For newly hot leads
only, call Reed's details endpoint once to fill latest_url and
expiration info.

Run rebuild_hot_leads() automatically at the end of every collection
run.

**Phase 2 acceptance checks:**

- [ ] Unit tests cover normalisation, grouping, each signal and the
  score

- [ ] A synthetic fixture with one role posted 4 times over 60 days
  becomes a hot lead with the right count and score

- [ ] Agency listings never become hot leads

- [ ] Owner-set status survives a rebuild

- [ ] python cli.py hot-leads --search-id 1 --top 20 prints a readable
  table

## Phase 2.5: Role Match (paste this into the existing Claude Code chat)

Copy everything from here to the end of the next section into Claude
Code as one message.

> **Update to BUILD_PROMPT.md. Read it fully before changing anything.**
>
> Add this as Phase 2.5, between Phase 2 and Phase 3. Also append it to
> BUILD_PROMPT.md so the file stays the single source of truth. Priority
> order changes: **finish Phase 1, then build the Role Match MVP below
> before anything else.** A prospect agency needs a matched list by
> Friday 6 November 2026. The CRM parts of Role Match get built during
> Phase 5. Work as before: run tests, show me how to run it, list
> assumptions, wait for my OK.

**What Role Match does:** an agency has candidates it is "sitting on"
(e.g. an Electrical Estimator in Warrington). Role Match finds **direct
employers currently hiring that role near that location**, and ranks
them by how stuck they look. The agency then sends the candidate's CV to
the employer. Recruiters call this marketing an MPC (most placeable
candidate). This is the most valuable thing SmartEmploy can hand an
agency, because it turns idle candidates into fees.

**Hard rule: no candidate personal data.** A "candidate brief" is a role
profile only: role title, location, radius, sector keywords, seniority.
Never names, CVs, contact details or salaries of individuals.

**Lesson from manual research (design around it):** in North West M&E,
most job-board ads are posted by recruitment agencies with the employer
hidden. The best leads came from **employers' own careers pages**. So
Role Match uses four sources, in this order of trust:

| \#  | Source                          | How                                                                                                                              | Notes                                                                                                                                        |
|-----|---------------------------------|----------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| 1   | Employer careers pages          | Fetch each employer's own careers URL from the employer directory, extract job titles and locations                              | Main source. Polite fetching rules below                                                                                                     |
| 2   | Known ATS job boards            | If a careers URL is on a known applicant-tracking system, use that system's documented public job-board endpoint instead of HTML | Only documented public endpoints. Read each provider's docs first and tell me which ones you used. If unsure, fall back to fetching the page |
| 3   | Reed API, direct employers only | Existing reed_client.search(..., posted_by='employer') for each role synonym and location                                        | Also adds newly discovered employers to the directory                                                                                        |
| 4   | Wayback Machine CDX API         | For each matched job URL and careers page URL: earliest capture date                                                             | Gives "open since". Coverage is patchy: show "unknown" rather than guessing                                                                  |

Agency ads from Reed are **not** matches, but count them per role and
region as a "market demand" figure shown in the report ("9 agency ads
for this role in the North West right now").

**Polite fetching rules for careers pages (non-negotiable):**

- Only employers' own careers pages and ATS job boards. Never job boards
  like Indeed, Totaljobs, CV-Library or LinkedIn

- Check robots.txt with urllib.robotparser before every domain. If
  disallowed, skip and mark the employer blocked_by_robots

- User agent: SmartEmployBot/1.0 (+https://smartemploy.io/bot). Max 1
  request per 5 seconds per domain. Max 3 pages per employer per check
  (careers page plus up to 2 job detail pages)

- Check each employer at most once a day, default weekly

- Store only: job title, location text, job URL, first seen, last seen,
  and a hash of the page text. No personal names or emails from the page

- Handle JavaScript-only careers pages by marking them needs_manual
  rather than adding a headless browser. Ask me before adding Playwright

**Wayback CDX:** GET
https://web.archive.org/cdx/search/cdx?url={url}&output=json&fl=timestamp,statuscode&filter=statuscode:200&limit=1
returns the earliest successful capture. Throttle to 1 request per
second, cache results for 30 days, and store wayback_first_seen (date or
null). If the job URL has no captures, try the careers page URL and
label the result "careers page captured since {date}" rather than
claiming the job was open that long.

**Location matching:** geocode places and postcodes with the free
postcodes.io API (/postcodes/{postcode} and /places?q={name}), cache
forever, and compute distance in miles. Job locations that can't be
geocoded get distance = null and are kept but ranked lower.

**Role families and synonyms** (new table, editable in the CRM, seeded
with these M&E families):

| Brief role                          | Exact titles (match)                                                                                                                    | Related titles (lower score)                                                                                    |
|-------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| Electrical Estimator                | electrical estimator, senior electrical estimator, m&e estimator, estimator (electrical)                                                | mechanical estimator, quantity surveyor (m&e), pre-construction engineer (electrical)                           |
| Mechanical Pre-Construction Manager | mechanical pre-construction manager, pre-construction manager (mechanical), mechanical precon manager, mechanical pre-contracts manager | pre-construction manager, mep preconstruction manager, mechanical design manager, mechanical estimating manager |
| Electrical Pre-Construction Manager | electrical pre-construction manager, pre-construction manager (electrical), electrical precon manager, electrical pre-contracts manager | pre-construction manager, mep preconstruction manager, electrical design manager, electrical estimating manager |
| Mechanical Design Engineer          | mechanical design engineer, mechanical building services engineer, mechanical engineer (building services), hvac design engineer        | senior mechanical engineer (building services), mechanical design manager, building services design engineer    |
| Electrical Site Manager             | electrical site manager, m&e site manager, electrical site supervisor                                                                   | electrical supervisor, m&e supervisor, electrical contracts manager                                             |
| Electrical Project Manager          | electrical project manager, m&e project manager                                                                                         | electrical contracts manager, trainee electrical project manager, electrical project engineer                   |

Match on normalised titles from Phase 2's title_norm. Seniority words
still matter: a "Senior" ad is still a match for a non-senior brief, but
flag it.

**Scoring** (higher = better, shown with a plain-English reason):

| Signal                                                                                              | Points                                  |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------|
| Exact title match                                                                                   | +4                                      |
| Related title                                                                                       | +2                                      |
| Within the brief's radius                                                                           | +2 (outside radius but within 1.5×: +1) |
| Open 30+ days (our first_seen or Wayback)                                                           | +2                                      |
| Reposted (Phase 2 hot lead for the same key)                                                        | +2                                      |
| Employer has 2+ open roles in the same family                                                       | +1                                      |
| Sector keywords found in the ad (M&E, building services, HVAC, electrical installation, commercial) | +1                                      |
| Ad older than 60 days with no last_seen in 14 days (probably filled)                                | −3                                      |

**Employer directory:** a new employers table holds companies we
monitor. It fills from three places:

1.  Manual add in the CRM (name + website + careers URL)

2.  Every direct employer seen in Reed results, with careers URL left
    blank until found

3.  Companies House advanced company search filtered by SIC codes and
    location, as a "to research" queue. Seed SIC codes: 43210 electrical
    installation, 43220 plumbing, heat and air-conditioning
    installation, 43290 other construction installation, 71129 other
    engineering activities. Confirm the endpoint and parameters against
    the Companies House API docs before building it. Companies House has
    no websites, so these employers sit in a research queue until I add
    a URL

Seed the directory with three verified North West employers so the MVP
works on day one:

| Employer                               | Town                 | Careers URL                        |
|----------------------------------------|----------------------|------------------------------------|
| DL M&E Building Services Ltd           | Westhoughton, Bolton | https://www.dl-bsl.co.uk/job-board |
| Central Electrical & Mechanical NW Ltd | Manchester           | https://celectrical.com/careers/   |
| B-engineering (NW) Ltd                 | Warrington           | https://b-enggroup.co.uk/jobs/     |

**Schema additions** (supabase/migrations/00X_role_match.sql):

create table role_families (  
id bigserial primary key,  
name text not null unique, -- 'Electrical Estimator'  
exact_titles text\[\] not null,  
related_titles text\[\] not null default '{}'  
);  
  
create table employers (  
id bigserial primary key,  
name text not null,  
name_norm text not null unique,  
website text,  
careers_url text,  
ats_type text, -- e.g. 'html', 'breathehr', or a known ATS  
town text,  
lat numeric, lng numeric,  
ch_company_number text,  
sic_codes text\[\],  
source text not null, -- manual \| reed \| companies_house  
status text not null default 'active', -- active \| research \|
needs_manual \| blocked_by_robots \| ignore  
last_checked_at timestamptz,  
check_every_days int not null default 7,  
created_at timestamptz not null default now()  
);  
  
create table employer_jobs (  
id bigserial primary key,  
employer_id bigint references employers(id) on delete cascade,  
source text not null, -- careers_page \| ats \| reed  
title text not null,  
title_norm text not null,  
location_text text,  
lat numeric, lng numeric,  
url text,  
first_seen_at timestamptz not null default now(),  
last_seen_at timestamptz not null default now(),  
wayback_first_seen date,  
page_hash text,  
unique (employer_id, url)  
);  
  
create table candidate_briefs (  
id bigserial primary key,  
client_id bigint references clients(id) on delete cascade, -- or a
prospect agency  
agency_id bigint references agencies(id) on delete cascade,  
role_family_id bigint references role_families(id),  
label text not null, -- 'Electrical Estimator - Warrington'  
location text not null,  
lat numeric, lng numeric,  
radius_miles int not null default 30,  
sector_keywords text\[\] not null default '{}',  
seniority text,  
active boolean not null default true,  
created_at timestamptz not null default now()  
);  
  
create table brief_matches (  
id bigserial primary key,  
brief_id bigint references candidate_briefs(id) on delete cascade,  
employer_job_id bigint references employer_jobs(id) on delete cascade,  
score int not null,  
reasons text\[\] not null,  
distance_miles numeric,  
status text not null default 'new', -- new \| sent \| contacted \|
interview \| placed \| not_relevant  
first_matched_at timestamptz not null default now(),  
unique (brief_id, employer_job_id)  
);

Row Level Security on all of them, same as the rest. candidate_briefs
works for a prospect agency (pre-pilot, agency_id) as well as a client.

**MVP for Friday (CLI only, no UI):**

python cli.py brief add --agency "MCM Recruitment Solutions" --role
"Electrical Estimator" --location Warrington --radius 30  
python cli.py employers check --all \# fetch careers pages + ATS
boards  
python cli.py match --agency "MCM Recruitment Solutions" --out
matches.csv

Also support python cli.py brief import briefs.yaml so I can load all
six at once:

agency: MCM Recruitment Solutions  
sector_keywords: \[m&e, building services, commercial\]  
briefs:  
- {role: Electrical Estimator, location: Warrington, radius: 30}  
- {role: Mechanical Pre-Construction Manager, location: Manchester,
radius: 35}  
- {role: Electrical Pre-Construction Manager, location: Warrington,
radius: 40}  
- {role: Mechanical Design Engineer, location: Preston, radius: 30}  
- {role: Electrical Site Manager, location: Manchester, radius: 25}  
- {role: Electrical Project Manager, location: Manchester, radius: 25}

**matches.csv columns** (this is what the client sees, so keep wording
plain): candidate_role, company, their_open_role, location,
distance_miles, open_since, signal, link, source, score. Sort by
candidate role, then score. signal is a short human sentence, e.g. "Open
since Sept, 2 PM roles open at once". Below the matches, a summary row
per role: "Market demand: 9 agency ads for this role in the North West".

**Phase 2.5 acceptance checks:**

- [ ] The three seed employers are checked politely and their current
  roles stored

- [ ] Loading the six-brief YAML and running match produces a CSV with
  correct matches from the seed employers (e.g. DL M&E → Electrical
  Project Manager; Central Electrical & Mechanical → Electrical
  Estimator)

- [ ] No agency ads appear as matches; agency counts appear as market
  demand

- [ ] robots.txt, rate limits and the 3-pages-per-employer cap are
  enforced and tested

- [ ] Wayback dates show where captures exist and "unknown" where they
  don't

- [ ] No candidate personal data anywhere in the schema, logs or exports

## Role Match in the CRM (build during Phase 5)

Add these to the CRM when Phase 5 is built. They make Role Match
something the owner runs in two clicks for any agency, prospect or
client.

**New engine endpoints** (same X-API-Key rules as Phase 4):

| Method | Path                                                | Does                                                                  |
|--------|-----------------------------------------------------|-----------------------------------------------------------------------|
| POST   | /briefs                                             | Create a brief for an agency or client                                |
| POST   | /agencies/{id}/match                                | Check due employers, run matching for all active briefs, 202 + run_id |
| GET    | /agencies/{id}/matches?brief_id=&min_score=&status= | Matches with reasons                                                  |
| GET    | /exports/matches.csv?agency_id=                     | The client-facing CSV from Phase 2.5                                  |
| POST   | /employers                                          | Add an employer manually                                              |
| POST   | /employers/{id}/check                               | Check one employer's careers page now                                 |
| POST   | /employers/import-companies-house?sic=&location=    | Fill the research queue                                               |

**CRM pages:**

| Where                                                        | What's on it                                                                                                                                                                                                                                                                                                                                                                                |
|--------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| /agencies/\[id\] and /clients/\[id\], new **Candidates** tab | List of candidate briefs (role, location, radius, sector, active). "Add brief" form with role picked from role families. **"Find matches"** button with live run status. Results grouped by brief: company, their role, distance, open since, signal, score, link, status dropdown (new, sent, contacted, interview, placed, not relevant). Buttons: "Download CSV", "Send to Google Sheet" |
| /employers                                                   | Employer directory table: name, town, careers URL, status badge (active, research, needs manual, blocked by robots), open roles count, last checked. Filters by status and town. Inline "add careers URL" for research-queue rows, so building the directory is fast. "Check now" per row                                                                                                   |
| /settings/role-families                                      | Edit exact and related titles per family, with a live preview of which stored jobs each family would match                                                                                                                                                                                                                                                                                  |
| / Today                                                      | New card: "New matches for your agencies" with count per agency, linking to their Candidates tab                                                                                                                                                                                                                                                                                            |

**Make.com addition, Scenario 5: Candidate matches to Google Sheet** (on
demand, triggered by the CRM button through a webhook):

1.  Custom webhook receives agency_id and sheet_url

2.  HTTP: GET /agencies/{id}/matches?status=new&min_score=4

3.  Google Sheets: add rows to a **"Candidate matches"** tab with
    columns Candidate role · Company · Their open role · Location ·
    Distance (miles) · Open since · Signal · Link · Status (client
    dropdown)

4.  HTTP: mark those matches as sent

**Sales use (why this matters):** Role Match is also an outreach hook.
The DM becomes: "Send me the roles of 3 candidates you're sitting on and
I'll find direct employers hiring them, free." Add a template for it in
templates (channel linkedin):

Hey {first_name}, quick one. Got any candidates you're sitting on right
now? Send me the roles and locations (no names needed) and I'll find
direct employers hiring those exact roles nearby. Free, no strings.

**Acceptance checks for the CRM part:**

- [ ] From an agency page, add six briefs, click "Find matches", and see
  grouped results within a few minutes

- [ ] Research-queue employers can get a careers URL added inline and
  checked immediately

- [ ] "Send to Google Sheet" fills the client's Candidate matches tab
  with no duplicates on a second send

- [ ] Match status changes are saved and visible on the Today card
  counts

## Phase 3: prospect agencies and Companies House enrichment

Goal: turn agency listings into a clean prospect list per niche, then
confirm each agency is a limited company and find its directors' names.

**agencies.py: rebuild_agencies()** (runs after each collection)

1.  Group listings where posted_by='agency' by employer_norm

2.  Upsert into agencies (name_norm unique). Never overwrite stage,
    notes, website or next_follow_up that the owner set

3.  Update ads_count_90d and agency_searches.ads_count per search

4.  New agencies start at stage new

**Companies House enrichment** (ch_client.py), free API key from the
Companies House developer hub:

- Base URL https://api.company-information.service.gov.uk, HTTP Basic
  auth with the API key as username and empty password

- GET /search/companies?q={name} → pick the best active match by name
  similarity. Store ch_match_confidence (0–1). Below 0.85, mark for
  manual review instead of trusting it

- GET /company/{number} → store company type and status

- GET /company/{number}/officers → for current directors (no resigned
  date), create contacts rows with source='companies_house', role, full
  name and parsed first name. No email at this stage

- Check the Companies House docs for the current rate limit and throttle
  below it. Cache responses for 30 days

**Compliance flags** (shown in the CRM, used by exports):

| ch_company_type                             | Email outreach allowed?                    |
|---------------------------------------------|--------------------------------------------|
| ltd, llp, plc                               | Yes (corporate subscriber)                 |
| Not found, low confidence, or anything else | No: show a "check before emailing" warning |

Sole traders and ordinary partnerships aren't on Companies House as
companies, so "not found" must never default to allowed.

**CLI:** python cli.py agencies --search-id 1 and python cli.py enrich
--agency-id 12 / --all-new.

**Phase 3 acceptance checks:**

- [ ] Agencies from a real run appear once each, with correct ad counts
  per search

- [ ] Enrichment fills company type, status and directors for a known
  agency

- [ ] Low-confidence matches are flagged, not trusted

- [ ] Owner-edited fields survive a rebuild

## Phase 4: FastAPI endpoints for Make.com and the CRM

Goal: expose the engine over HTTPS so Make.com and the CRM can trigger
runs and pull data. The CRM does normal reads and edits straight against
Supabase; it only calls this API for runs, enrichment, drafts and
exports.

**Rules for every endpoint:**

- Require header X-API-Key matching ENGINE_API_SECRET. Return 401
  otherwise

- Long work returns 202 with a run_id immediately and continues as a
  background task. Make.com must never wait on a long job

- JSON in, JSON out, except CSV exports. Dates in ISO 8601, UK time zone
  noted in docs

- Auto-generated OpenAPI docs at /docs, disabled in production unless a
  flag is set

| Method | Path                                            | Does                                                                                              | Used by                  |
|--------|-------------------------------------------------|---------------------------------------------------------------------------------------------------|--------------------------|
| GET    | /health                                         | Liveness check, DB reachable                                                                      | Monitoring               |
| POST   | /searches/{id}/run                              | Run one search, then rebuild hot leads and agencies. 202 + run_id                                 | CRM "Run now"            |
| POST   | /run-all                                        | Run every active search one after another                                                         | Make.com daily           |
| GET    | /runs/{id}                                      | Run status and counts                                                                             | CRM, Make.com            |
| GET    | /hot-leads?search_id=&min_score=&since=&status= | Hot leads as JSON, sorted by score                                                                | CRM, Make.com            |
| GET    | /clients/active                                 | Active pilot and paid clients with sheet URL and territories                                      | Make.com                 |
| GET    | /clients/{id}/daily-leads?mark=true             | Hot leads in the client's territories not yet delivered. With mark=true, writes deliveries rows   | Make.com client delivery |
| POST   | /agencies/{id}/enrich                           | Companies House enrichment                                                                        | CRM                      |
| POST   | /agencies/{id}/draft?template_id=               | Rendered email with the agency's top 3 hot leads filled in                                        | CRM                      |
| GET    | /exports/outreach.csv?search_id=&stage=new      | CSV for a cold email tool, see columns below                                                      | CRM download             |
| GET    | /digest/today                                   | Owner's morning digest: new hot leads, agencies to contact, follow-ups due, yesterday's scorecard | Make.com                 |
| POST   | /contacts/{id}/forget                           | Delete a contact's personal data and add their email to a suppression list                        | CRM                      |

**/exports/outreach.csv columns:** email, first_name, agency, niche,
city, lead1_employer, lead1_role, lead1_count, lead1_since,
lead2_employer, lead2_role, lead2_count, lead3_employer, lead3_role,
lead3_count.

Export rules: skip contacts without a verified email, skip
do_not_contact, skip anyone on the suppression list, skip agencies whose
company type isn't ltd/llp/plc, skip agencies not at the requested
stage. Return the skipped count and reasons in a response header.

**Phase 4 acceptance checks:**

- [ ] Every endpoint rejects requests without the secret

- [ ] /run-all returns in under 2 seconds and the run completes in the
  background

- [ ] /clients/{id}/daily-leads?mark=true never returns the same lead
  twice for the same client

- [ ] Export skips non-compliant rows and reports why

- [ ] Tests for each endpoint with a test database

## Phase 5: the CRM, the face of the system

Goal: one web app the owner lives in every day. Fast, keyboard-friendly,
works on a phone. Clean and minimal, matching the SmartEmploy site
style. No feature that doesn't help him send more outreach or close more
deals.

**Stack:** Next.js App Router + TypeScript, Tailwind, shadcn/ui,
Supabase JS with Auth (email magic link, single user). Server actions
for writes. Engine calls go through a Next.js server route so
ENGINE_API_SECRET never reaches the browser.

**Pages:**

| Route            | What's on it                                                                                                                                                                                                                                                                                                                                                                                                                            |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| / Today          | Big scorecard: outreach actions today vs **100** (progress bar), meetings this week, active pilots, paying clients, MRR in £. Below: "Do next" list of agencies at stage new with a ready draft, follow-ups due today, and replies waiting                                                                                                                                                                                              |
| /searches        | Saved searches (niche + region). Create/edit form: label, keywords (tag input), location, radius, minimum salary, active toggle. "Run now" button with live run status. Shows last run time, listings seen, hot leads, agencies found, and which client owns the territory                                                                                                                                                              |
| /hot-leads       | Table: employer, role, times posted, first seen, last seen, location, score, live, status. Filters: search, min score, live only, status. Row actions: open ad, mark used, ignore                                                                                                                                                                                                                                                       |
| /agencies        | Kanban by stage: new → researching → contacted → replied → call_booked → pilot → paid, plus lost and do_not_contact columns. Drag to change stage. Card shows name, niche, ads in 90 days, director name, company-type badge (green ltd/llp/plc, amber "check"). Toggle to table view                                                                                                                                                   |
| /agencies/\[id\] | Header with stage, company info, website, next follow-up date picker. Contacts list (add, edit, verify email, LinkedIn URL, forget button). Their live ads. **Top 3 hot leads** in their niche and region. **Draft panel**: pick a template, see it filled in, one-click copy, and a "Log as sent" button that creates an activity. Activity timeline with quick-log buttons: Email, Call, LinkedIn DM, LinkedIn connect, Meeting, Note |
| /clients         | Pilot and paid clients: plan, price, start date, territories, Google Sheet link, leads delivered, outcomes (contacted, meetings, placements). "Convert pilot to paid" action. Territory picker blocks searches already owned by another client                                                                                                                                                                                          |
| /templates       | Create and edit templates with a variables cheat sheet and live preview against a real agency                                                                                                                                                                                                                                                                                                                                           |
| /settings        | Engine connection status, Make.com webhook URLs, repost threshold, daily outreach target                                                                                                                                                                                                                                                                                                                                                |

**Behaviour that matters:**

- Every logged action (email, call, DM, connect) counts toward the 100
  on the Today page instantly

- Changing an agency to contacted without logging an activity prompts
  "How did you contact them?"

- Setting do_not_contact on an agency or contact removes it from every
  export and draft forever

- Moving an agency to pilot opens a form to create the client record and
  pick territories

- Global search (Ctrl/Cmd + K) across agencies, contacts and hot leads

- Empty states that tell the owner what to do next ("No searches yet.
  Add your first niche.")

**Phase 5 acceptance checks:**

- [ ] Login works and only the owner can see data

- [ ] Create a search, run it from the UI, and watch hot leads and
  agencies appear

- [ ] Drag an agency across the pipeline, log activities, see the Today
  scorecard move

- [ ] Draft panel fills a template with real hot leads and copies it

- [ ] Territory exclusivity is enforced in the UI and the database

- [ ] Usable on a phone screen

## Phase 6: Make.com scenarios and Google Sheets

Goal: Make.com runs the schedule and delivers client leads into Google
Sheets. Claude Code can't click around Make.com, so this phase produces
make/SCENARIOS.md: exact build instructions the owner follows by hand,
plus the engine changes they need. Set the Make.com organisation time
zone to Europe/London.

**Client Google Sheet template** (one copy per client, owner creates the
template once):

| Column           | Filled by                                                             |
|------------------|-----------------------------------------------------------------------|
| Date added       | Make.com                                                              |
| Company          | Make.com                                                              |
| Role             | Make.com                                                              |
| Times posted     | Make.com                                                              |
| First seen       | Make.com                                                              |
| Location         | Make.com                                                              |
| Ad link          | Make.com                                                              |
| Suggested opener | Make.com (from the client_hm_email template)                          |
| Status           | Client: dropdown New / Contacted / Meeting / Placement / Not relevant |
| Notes            | Client                                                                |

Add suggested_opener to each lead returned by /clients/{id}/daily-leads,
rendered from the client_hm_email template.

**Scenario 1: Daily collect** (every day 06:00 UK)

1.  Schedule trigger

2.  HTTP "Make a request": POST {ENGINE_URL}/run-all, header X-API-Key

3.  On error: send the owner an email or Telegram message

**Scenario 2: Client delivery** (every weekday 08:00 UK, after scenario
1 finishes)

1.  Schedule trigger

2.  HTTP: GET /clients/active → parse JSON

3.  Iterator over clients

4.  HTTP: GET /clients/{id}/daily-leads?mark=true

5.  Iterator over leads

6.  Google Sheets "Add a Row" into the client's sheet (sheet ID from
    sheet_url)

7.  Aggregator, then Gmail "Send an email" to delivery_email: "{n} new
    hot leads in your sheet today" with the sheet link. Skip if n = 0

**Scenario 3: Owner's morning digest** (every weekday 07:30 Riga time,
so set this one's schedule accordingly)

1.  Schedule trigger

2.  HTTP: GET /digest/today

3.  Gmail or Telegram message to the owner: yesterday's outreach count
    vs 100, new hot leads, top 10 agencies to contact today, follow-ups
    due

**Scenario 4: New client onboarding** (triggered from the CRM)

1.  Custom webhook, called by the CRM when a client is created

2.  Google Drive "Copy a File": copy the client sheet template, name it
    "{Agency} – Hot Leads"

3.  Share the copy with the client's email (manually at first; automate
    later with the Google Drive module)

4.  HTTP: PATCH the client's sheet_url back via a new engine endpoint
    /clients/{id}/sheet

**Phase 6 acceptance checks:**

- [ ] SCENARIOS.md lets someone who has never used Make.com build all
  four scenarios

- [ ] A test client receives leads in their sheet, with no duplicates
  over three days

- [ ] The owner gets the morning digest

- [ ] Failures in any scenario notify the owner

## Email and DM templates

Seed these into templates via supabase/seed.sql. Rendering uses simple
{variable} placeholders. **If any variable is empty, the draft must show
a red warning and the export must skip that row.** No links and no
images in first-touch emails. Plain text only.

**Variables:** {first_name} {agency} {niche} {city} {lead1_employer}
{lead1_role} {lead1_count} {lead1_since} (month name) {lead2_employer}
{lead2_role} {lead2_count} {lead3_employer} {lead3_role} {lead3_count}
{hm_role} {open_since} {sender_name}

**Footer on every email** (identifies the sender and gives an opt-out):

{sender_name}, SmartEmploy  
SmartEmploy is a trading name of SIA Impresso, Latvia  
Not interested? Reply "stop" and I won't email again.

**1. Cold email, first touch** (channel: email)

Subject: {lead1_count}x reposted {niche} role in {city}  
  
Hi {first_name},  
  
Saw {agency} places {niche} roles around {city}.  
  
{lead1_employer} has posted the same {lead1_role} role {lead1_count}
times since {lead1_since}. Nobody's filled it yet. {lead2_employer} is
on {lead2_count} posts for {lead2_role}.  
  
I track companies like this across the UK every day. Want 20 more in
your niche? Free, no call needed.

**2. Follow-up, day 3** (channel: email)

Subject: re: {lead1_count}x reposted {niche} role in {city}  
  
Hi {first_name}, one more for you: {lead3_employer} has posted
{lead3_role} {lead3_count} times. Want the full list of 20?

**3. Follow-up, day 7** (channel: email)

Subject: close the loop?  
  
Hi {first_name}, I'll stop here. If new clients in {niche} aren't a
priority right now, no worries. If they are, reply "leads" and I'll send
20 this week.

**4. LinkedIn DM** (channel: linkedin, no footer)

Hey {first_name}, quick one. {lead1_employer} has posted {lead1_role}
{lead1_count} times since {lead1_since} and still hasn't filled it. I
track these for {niche} agencies in {city}. Want the list? Free, no
strings.

**5. Opener for the client's own outreach to the employer** (channel:
client_hm_email, used in client Google Sheets)

Hi, I noticed your {hm_role} role has been advertised since
{open_since}. We specialise in {niche} in {city} and have candidates who
could interview this week. Worth a quick call?

## Rules, compliance, security, testing and deployment

**Hard rules for Claude Code:**

1.  Official APIs only (Reed, Companies House, later Adzuna). No HTML
    scraping of any job board, no LinkedIn scraping, no automation of
    LinkedIn

2.  Store only company, job and business-contact data. Never store
    candidate or job-seeker data

3.  Secrets live in env vars. Provide .env.example, never commit .env,
    never log keys. The Supabase service role key is used by the engine
    only, never in the browser

4.  Row Level Security on every table

5.  This is a fresh build. Do not ask for, import or reuse any code or
    data from the owner's employer

6.  Ask before adding any dependency or service not listed in this
    prompt

**UK data protection basics built into the product** (not legal advice,
the owner will confirm with a professional):

- Contacts are business contacts held under legitimate interest. Keep
  only the fields in the schema

- do_not_contact and the suppression list are respected by every draft,
  export and Make.com flow

- /contacts/{id}/forget deletes the personal data and keeps only the
  suppressed email hash

- Email exports only include agencies confirmed as ltd, llp or plc on
  Companies House

- Every email template carries the sender footer and an opt-out

**Environment variables** (.env.example):

REED_API_KEY=  
COMPANIES_HOUSE_API_KEY=  
SUPABASE_URL=  
SUPABASE_SERVICE_ROLE_KEY= \# engine only  
NEXT_PUBLIC_SUPABASE_URL=  
NEXT_PUBLIC_SUPABASE_ANON_KEY=  
ENGINE_API_SECRET= \# shared with Make.com and the CRM server  
ENGINE_URL=  
OWNER_EMAIL=  
REPOST_THRESHOLD=3  
LOOKBACK_DAYS=90  
REED_MAX_CALLS_PER_RUN=300  
REED_REQUESTS_PER_SECOND=1

**Testing:** pytest for the engine (normalisation, detection, agencies,
exports, endpoints) with saved real fixtures and a test database.
Playwright smoke test for the CRM: log in, create a search, see results,
move an agency, log an activity.

**Deployment:**

1.  Supabase project: run migrations and seed

2.  Engine: deploy to Render or Railway from engine/, set env vars,
    confirm /health

3.  CRM: deploy crm/ to Vercel, set env vars, add the owner's email to
    Supabase Auth

4.  Make.com: build scenarios from make/SCENARIOS.md

5.  README with every step above, written for a non-expert

**Final acceptance checklist (whole system):**

- [ ] A new niche goes from "Add search" to hot leads and a prospect
  agency list in one click

- [ ] Collection runs daily without anyone touching it

- [ ] Every morning the owner gets a digest and the Today page shows his
  100-action target

- [ ] An agency card shows directors, company type, top 3 hot leads and
  a ready draft

- [ ] A pilot client gets new hot leads in their Google Sheet every
  weekday, with no duplicates

- [ ] Do-not-contact and forget requests are honoured everywhere

- [ ] No secrets in the repo, no scraping, no candidate data
