-- Repost Radar: initial schema.
-- Run in the Supabase SQL editor, or with `supabase db push`.

-- ---------------------------------------------------------------------------
-- Owner access. RLS policies below allow access only to user ids listed here.
-- After your first magic-link login, add yourself once in the SQL editor:
--   insert into app_users (user_id, email)
--   select id, email from auth.users where email = 'you@example.com';
-- ---------------------------------------------------------------------------
create table app_users (
  user_id uuid primary key,
  email text,
  created_at timestamptz not null default now()
);

create or replace function public.is_owner()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (select 1 from public.app_users where user_id = auth.uid());
$$;

-- Saved searches = niche + region territories
create table searches (
  id bigserial primary key,
  label text not null,                 -- e.g. 'Accountancy - Leeds'
  keywords text[] not null,            -- each keyword is queried separately
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
  status text not null default 'running',   -- running | ok | error
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
  lead_key text primary key,           -- sha1(employer_norm|title_norm|location_norm)
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
  job_ids bigint[] not null,
  status text not null default 'new',  -- new | used | ignored
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
  ch_company_type text,                -- ltd | llp | plc | unknown ...
  ch_company_status text,
  ch_match_confidence numeric,
  ch_checked_at timestamptz,
  stage text not null default 'new',
  -- new | researching | contacted | replied | call_booked | pilot | paid | lost | do_not_contact
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
  role text,                           -- e.g. Director (from Companies House)
  email text,
  email_verified boolean default false,
  linkedin_url text,
  source text not null,                -- companies_house | manual | website
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
  channel text not null check (channel in ('email','linkedin','client_hm_email')),
  subject text,
  body text not null
);

create table clients (
  id bigserial primary key,
  agency_id bigint references agencies(id),
  plan text not null check (plan in ('pilot','founding','done_for_you')),
  monthly_price_gbp numeric not null default 0,
  start_date date not null,
  end_date date,
  sheet_url text,
  delivery_email text,
  status text not null default 'active'  -- active | paused | ended
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
  outcome text not null default 'none',  -- none | contacted | meeting | placement
  unique (client_id, lead_key)
);

-- Daily scorecard for the CRM home page.
-- security_invoker makes the view respect the RLS of the activities table.
create view scorecard_daily with (security_invoker = true) as
select date_trunc('day', happened_at)::date as day,
       count(*) filter (where counts_toward_daily) as outreach_actions,
       count(*) filter (where type = 'meeting') as meetings
from activities
group by 1;

-- ---------------------------------------------------------------------------
-- Row Level Security on every table. The engine uses the service role key,
-- which bypasses RLS; the CRM uses the anon key + the owner's session.
-- ---------------------------------------------------------------------------
alter table app_users enable row level security;
create policy app_users_self_read on app_users
  for select to authenticated using (user_id = auth.uid());

do $$
declare t text;
begin
  foreach t in array array[
    'searches','runs','listings','listing_searches','employer_overrides',
    'hot_leads','hot_lead_searches','agencies','agency_searches','contacts',
    'activities','templates','clients','client_territories','deliveries'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy owner_all on %I for all to authenticated '
      'using (public.is_owner()) with check (public.is_owner())', t);
  end loop;
end $$;
