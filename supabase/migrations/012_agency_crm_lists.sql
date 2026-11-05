-- A client agency's own CRM export (companies they already work with), so leads can be left out or
-- marked "already in your CRM, owner: ...". Replaced on every upload.
create table if not exists agency_crm_companies (
  id bigserial primary key,
  agency_id bigint not null references agencies(id) on delete cascade,
  name text,
  name_norm text,
  domain text,
  ch_number text,
  owner text,
  uploaded_at timestamptz not null default now()
);
create index if not exists agency_crm_norm_idx on agency_crm_companies (agency_id, name_norm);
alter table agency_crm_companies enable row level security;
drop policy if exists owner_all on agency_crm_companies;
create policy owner_all on agency_crm_companies for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

-- exclude = matching companies never go out; flag = they go out marked with the owner
alter table agencies add column if not exists crm_match_mode text not null default 'exclude'
  check (crm_match_mode in ('exclude', 'flag'));

-- Daily orders: up to N new leads every morning, ongoing.
alter table lead_orders drop constraint if exists lead_orders_frequency_check;
alter table lead_orders add constraint lead_orders_frequency_check check (frequency in ('once', 'daily', 'weekly'));
