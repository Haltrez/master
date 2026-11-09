-- Client feedback: long-open careers pages are often "evergreen" listings, not real demand.
-- A hot lead needs an ACTIVE signal (re-advertised in the last 30 days); passive-only leads go to a watch list.
alter table hot_leads add column if not exists signal text not null default 'active';
alter table hot_leads drop constraint if exists hot_leads_signal_check;
alter table hot_leads add constraint hot_leads_signal_check check (signal in ('active', 'passive'));
alter table hot_leads add column if not exists evergreen boolean not null default false;
alter table hot_leads add column if not exists evergreen_reason text;
alter table hot_leads add column if not exists last_active_at timestamptz;
create index if not exists hot_leads_signal_idx on hot_leads (signal);
-- careers pages: when the page text last changed (a changed page is an active signal, an unchanged one passive)
alter table employer_jobs add column if not exists page_hash_changed_at timestamptz;
update employer_jobs set page_hash_changed_at = first_seen_at where page_hash_changed_at is null;
alter table employer_jobs alter column page_hash_changed_at set default now();
