-- Repost Radar Phase 2.5: Role Match.
-- No candidate personal data anywhere: a brief is a role profile only.

create table role_families (
  id bigserial primary key,
  name text not null unique,             -- 'Electrical Estimator'
  exact_titles text[] not null,
  related_titles text[] not null default '{}'
);

create table employers (
  id bigserial primary key,
  name text not null,
  name_norm text not null unique,
  website text,
  careers_url text,
  ats_type text,                         -- html | greenhouse | lever | ashby
  town text,
  lat numeric, lng numeric,
  ch_company_number text,
  sic_codes text[],
  source text not null,                  -- manual | reed | companies_house
  status text not null default 'active', -- active | research | needs_manual | blocked_by_robots | ignore
  last_checked_at timestamptz,
  check_every_days int not null default 7,
  created_at timestamptz not null default now()
);

create table employer_jobs (
  id bigserial primary key,
  employer_id bigint references employers(id) on delete cascade,
  source text not null,                  -- careers_page | ats | reed
  title text not null,
  title_norm text not null,
  location_text text,
  lat numeric, lng numeric,
  url text,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  wayback_first_seen date,
  page_hash text,
  -- Additions to the spec (see README): the employer's own posted date when
  -- the page publishes one, and which sector keywords the ad mentions.
  date_posted date,
  keywords_found text[] not null default '{}',
  unique (employer_id, url)
);

create table candidate_briefs (
  id bigserial primary key,
  client_id bigint references clients(id) on delete cascade,    -- a paying/pilot client
  agency_id bigint references agencies(id) on delete cascade,   -- or a prospect agency
  role_family_id bigint references role_families(id),
  label text not null,                   -- 'Electrical Estimator - Warrington'
  location text not null,
  lat numeric, lng numeric,
  radius_miles int not null default 30,
  sector_keywords text[] not null default '{}',
  seniority text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  check (client_id is not null or agency_id is not null)
);

create table brief_matches (
  id bigserial primary key,
  brief_id bigint references candidate_briefs(id) on delete cascade,
  employer_job_id bigint references employer_jobs(id) on delete cascade,
  score int not null,
  reasons text[] not null,
  distance_miles numeric,
  status text not null default 'new',    -- new | sent | contacted | interview | placed | not_relevant
  first_matched_at timestamptz not null default now(),
  unique (brief_id, employer_job_id)
);

-- Response cache for geocoding (forever), Wayback (30 days), Companies House.
create table http_cache (
  key text primary key,
  value jsonb,
  fetched_at timestamptz not null default now()
);

do $$
declare t text;
begin
  foreach t in array array['role_families','employers','employer_jobs','candidate_briefs','brief_matches','http_cache'] loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy owner_all on %I for all to authenticated '
      'using (public.is_owner()) with check (public.is_owner())', t);
  end loop;
end $$;

-- Every direct employer seen in Reed results joins the directory as a
-- research-queue row (no careers URL yet). Never overwrites existing rows.
create or replace function public.add_reed_employer()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.posted_by = 'employer' and new.employer_norm <> '' then
    insert into employers (name, name_norm, source, status)
    values (new.employer_name, new.employer_norm, 'reed', 'research')
    on conflict (name_norm) do nothing;
  end if;
  return new;
end;
$$;

create trigger listings_add_reed_employer
after insert or update of posted_by on listings
for each row execute function public.add_reed_employer();
