-- Cold email to agencies through Smartlead: the daily queue, replies, and which hot leads went to which city.
create table if not exists outreach_queue (
  id bigserial primary key,
  date date not null default (now() at time zone 'Europe/London')::date,
  agency_id bigint references agencies(id) on delete cascade,
  contact_id bigint references contacts(id) on delete set null,
  campaign_role text not null default 'main' check (campaign_role in ('main', 'fallback')),
  fields jsonb not null default '{}',
  warnings jsonb not null default '[]',
  status text not null default 'built' check (status in ('built', 'approved', 'pushed', 'failed', 'removed', 'blocked')),
  smartlead_lead_id bigint,
  smartlead_campaign_id bigint,
  error text,
  is_test boolean not null default false,
  created_at timestamptz not null default now(),
  pushed_at timestamptz
);
create index if not exists outreach_queue_date_idx on outreach_queue (date, status);

create table if not exists outreach_replies (
  id bigserial primary key,
  smartlead_message_id text not null unique,
  agency_id bigint references agencies(id) on delete set null,
  contact_id bigint references contacts(id) on delete set null,
  lead_email text,
  smartlead_campaign_id bigint,
  campaign_lead_map_id text,
  received_at timestamptz,
  subject text,
  snippet text,
  smartlead_category text,
  category text check (category in ('interested', 'not_now', 'not_interested', 'unsubscribe', 'out_of_office', 'wrong_person')),
  categorised_at timestamptz,
  created_at timestamptz not null default now()
);

-- A hot lead is pitched to one agency per city per day.
create table if not exists lead_assignments (
  hot_lead_key text not null,
  agency_id bigint references agencies(id) on delete cascade,
  city text not null,
  date date not null,
  unique (hot_lead_key, city, date)
);

alter table outreach_queue enable row level security;
alter table outreach_replies enable row level security;
alter table lead_assignments enable row level security;
drop policy if exists owner_all on outreach_queue;
drop policy if exists owner_all on outreach_replies;
drop policy if exists owner_all on lead_assignments;
create policy owner_all on outreach_queue for all to authenticated using (public.is_owner()) with check (public.is_owner());
create policy owner_all on outreach_replies for all to authenticated using (public.is_owner()) with check (public.is_owner());
create policy owner_all on lead_assignments for all to authenticated using (public.is_owner()) with check (public.is_owner());

insert into app_settings (key, value) values
  ('outreach_daily_push_limit', '50'), ('outreach_auto_push', 'false')
on conflict (key) do nothing;
