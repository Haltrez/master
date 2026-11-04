-- Earlier postings of the same company + role found in the Wayback Machine
-- (Totaljobs ad URLs carry both: /job/<role>/<company>-job<id>). This gives
-- repost history for vacancies we never saw ourselves.
create table if not exists archive_evidence (
  group_key text primary key,        -- lead key: sha1(employer_norm|title_norm|location_norm)
  employer_norm text not null,
  title_norm text not null,
  prior_count int not null default 0,  -- distinct earlier job ids
  oldest_capture date,
  old_url text,                      -- web.archive.org link to the oldest earlier ad
  checked_at timestamptz not null default now()
);
alter table archive_evidence enable row level security;
drop policy if exists owner_all on archive_evidence;
create policy owner_all on archive_evidence for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

alter table hot_leads add column if not exists old_ad_url text;
alter table hot_leads add column if not exists archive_prior int not null default 0;

-- Hot lead = posted, ended, posted again (the owner's definition). Turn off to
-- also count ads open 30+ days and two copies live at once.
insert into app_settings (key, value) values ('reposts_only', 'true'), ('archive_checks_per_run', '150')
on conflict (key) do nothing;
update app_settings set value = '2' where key = 'repost_threshold' and value = '3'::jsonb;
