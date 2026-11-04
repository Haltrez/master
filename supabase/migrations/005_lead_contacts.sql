-- Decision makers at hiring companies (hot leads), like the Excel's
-- "Lēmumu pieņēmējs" + "E-pasts" columns. One row per employer.
create table if not exists lead_contacts (
  employer_norm text primary key,
  company_name text,
  ch_company_number text,
  ch_company_type text,
  ch_match_confidence numeric,
  website text,
  contact_name text,
  contact_first_name text,
  contact_role text,
  email text,
  email_status text,            -- Hunter: valid | accept_all | unknown | not_found
  email_verified boolean not null default false,
  checked_at timestamptz not null default now()
);
alter table lead_contacts enable row level security;
drop policy if exists owner_all on lead_contacts;
create policy owner_all on lead_contacts for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

-- The sheet's ready-to-send email from the client agency to the hiring company.
update templates set body = $t$Hi {first_name},

I noticed {company} has advertised the {hm_role} role {posted_since}. That usually means the right person hasn't come through yet.

We specialise in {niche} recruitment around {city} and have candidates who could interview this week. Worth a quick call?$t$
where channel = 'client_hm_email' and body like 'Hi, I noticed your {hm_role} role%';

insert into app_settings (key, value) values ('lead_contacts_per_run', '20') on conflict (key) do nothing;
