-- Lead orders: "make N leads for this agency, this industry, this region, and send them".
-- One order = one agency + one exclusive search + one client row + (optionally) one Google Sheet.
create table if not exists lead_orders (
  id bigserial primary key,
  agency_id bigint references agencies(id),
  client_id bigint references clients(id) on delete set null,
  search_id bigint references searches(id),
  industry text not null,
  location text not null,
  radius_miles int not null default 20,
  leads_wanted int not null check (leads_wanted > 0),
  frequency text not null default 'once' check (frequency in ('once', 'weekly')),
  package text not null default 'sheet' check (package in ('sheet', 'outreach')),
  client_email text,
  sheet_url text,
  sheet_id text,
  status text not null default 'collecting'
    check (status in ('collecting', 'waiting', 'done', 'paused', 'error')),
  note text,
  period_start timestamptz not null default now(),
  last_run_at timestamptz,
  created_at timestamptz not null default now()
);
alter table lead_orders enable row level security;
drop policy if exists owner_all on lead_orders;
create policy owner_all on lead_orders for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

-- The assistant's conversations, stored exactly as sent to the model (append-only).
create table if not exists assistant_messages (
  id bigserial primary key,
  conversation_id text not null,
  seq int not null,
  role text not null check (role in ('user', 'assistant')),
  content jsonb not null,
  created_at timestamptz not null default now(),
  unique (conversation_id, seq)
);
alter table assistant_messages enable row level security;
drop policy if exists owner_all on assistant_messages;
create policy owner_all on assistant_messages for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

-- Which order sent which lead (an agency can have several orders).
alter table deliveries add column if not exists order_id bigint references lead_orders(id) on delete set null;
create index if not exists deliveries_order_idx on deliveries (order_id);
