-- Repost Radar: tables and columns for Phases 2-6 (detection, agencies,
-- API, CRM, Make.com). Additive only.

-- Hot leads: when the latest ad expires, and which job sources it came from.
alter table hot_leads add column if not exists expiration_date date;
alter table hot_leads add column if not exists sources text[] not null default '{reed}';
alter table hot_leads add column if not exists location_norm text;
alter table hot_leads add column if not exists title_norm text;

-- Agencies: Companies House review flag.
alter table agencies add column if not exists ch_review_needed boolean not null default false;
alter table agencies add column if not exists ch_company_name text;

-- Contacts: Hunter verification status ('valid', 'accept_all', 'unknown', ...).
alter table contacts add column if not exists email_status text;

-- Emails of people who asked to be forgotten / not contacted. Only a hash is kept.
create table if not exists suppression_list (
  email_hash text primary key,            -- sha256 of the lowercased email
  reason text,
  created_at timestamptz not null default now()
);

-- Owner settings shown on /settings (repost threshold, daily target, webhooks).
create table if not exists app_settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

-- Background work that isn't a collection run (enrichment, role match).
create table if not exists tasks (
  id bigserial primary key,
  kind text not null,                     -- enrich | match | check_employer | import_ch
  ref_id bigint,
  status text not null default 'running', -- running | ok | error
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  result jsonb,
  error text
);

create index if not exists listings_first_seen on listings (first_seen_at);
create index if not exists listings_source on listings (source);
create index if not exists activities_happened on activities (happened_at);
create index if not exists hot_leads_score on hot_leads (score desc);

do $$
declare t text;
begin
  foreach t in array array['suppression_list','app_settings','tasks'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists owner_all on %I', t);
    execute format(
      'create policy owner_all on %I for all to authenticated '
      'using (public.is_owner()) with check (public.is_owner())', t);
  end loop;
end $$;

insert into app_settings (key, value) values
  ('repost_threshold', '3'),
  ('daily_outreach_target', '100'),
  ('sender_name', '"Roberts"'),
  ('make_webhooks', '{}')
on conflict (key) do nothing;

-- Role Match: what the client-facing CSV / sheet shows, stored with the match.
alter table brief_matches add column if not exists signal text;
alter table brief_matches add column if not exists open_since_label text;
alter table brief_matches add column if not exists location_label text;

-- When a lead first became hot (the morning digest lists new ones).
alter table hot_leads add column if not exists created_at timestamptz not null default now();
