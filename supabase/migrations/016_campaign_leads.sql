-- Leads that are already in a Smartlead campaign (from Smartlead Prospect), personalised by the engine:
-- niche from the agency's website, nearest collected city, three of its hot leads written as custom fields.
create table if not exists campaign_leads (
  campaign_id bigint not null,
  lead_id bigint not null,
  campaign_role text,
  map_id bigint,
  email text not null,
  first_name text,
  company_name text,
  website text,
  location text,
  title text,
  sl_status text,
  domain text,
  town text,
  hub text,
  niche text,
  is_agency boolean,
  search_id bigint references searches(id) on delete set null,
  fields jsonb not null default '{}',
  state text not null default 'new' check (state in ('new', 'filled', 'held')),
  reason text,
  paused boolean not null default false,
  filled_at timestamptz,
  synced_at timestamptz not null default now(),
  primary key (campaign_id, lead_id)
);
create index if not exists campaign_leads_state_idx on campaign_leads (state);
alter table campaign_leads enable row level security;
drop policy if exists owner_all on campaign_leads;
create policy owner_all on campaign_leads for all to authenticated using (public.is_owner()) with check (public.is_owner());
