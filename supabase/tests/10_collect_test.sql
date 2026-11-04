-- Checks for 001_init.sql + 002_collect.sql. Fails loudly on any mismatch.
\set ON_ERROR_STOP on

insert into searches (label, keywords, location) values ('Accountancy - Leeds', '{management accountant}', 'Leeds');

do $$
declare
  rows jsonb := '[
    {"job_id": 1, "posted_by": "employer", "employer_name": "Harlow Group Ltd", "employer_norm": "harlow group",
     "title": "Management Accountant", "title_norm": "management accountant", "location_name": "Leeds",
     "location_norm": "leeds", "min_salary": 42000, "max_salary": 45000, "url": "https://www.reed.co.uk/jobs/x/1",
     "posted_date": "2026-11-03", "expiration_date": "2026-12-15", "raw": {"jobId": 1}},
    {"job_id": 2, "posted_by": "agency", "employer_name": "Aire Recruitment", "employer_norm": "aire recruitment",
     "title": "Finance Manager", "title_norm": "finance manager", "raw": {"jobId": 2}},
    {"job_id": 2, "posted_by": "agency", "employer_name": "Aire Recruitment", "employer_norm": "aire recruitment",
     "title": "Finance Manager", "title_norm": "finance manager", "raw": {"jobId": 2}}
  ]';
  n int;
begin
  n := upsert_listings(1, rows);
  if n <> 2 then raise exception 'first upsert: expected 2 new, got %', n; end if;
  if (select count(*) from listing_searches where search_id = 1) <> 2 then raise exception 'listing_searches not filled'; end if;
end $$;

-- Pretend the first run was yesterday, then upsert again.
update listings set first_seen_at = now() - interval '1 day', last_seen_at = now() - interval '1 day';

do $$
declare
  rows jsonb := '[
    {"job_id": 1, "posted_by": "agency", "employer_name": "Harlow Group Ltd", "employer_norm": "harlow group",
     "title": "Management Accountant", "title_norm": "management accountant", "location_name": "Leeds",
     "location_norm": "leeds", "url": null, "raw": {"jobId": 1, "v": 2}},
    {"job_id": 3, "posted_by": "employer", "employer_name": "Northgate Foods", "employer_norm": "northgate foods",
     "title": "Credit Controller", "title_norm": "credit controller", "raw": {"jobId": 3}}
  ]';
  n int;
  l listings;
begin
  n := upsert_listings(1, rows);
  if n <> 1 then raise exception 'second upsert: expected 1 new, got %', n; end if;
  if (select count(*) from listings) <> 3 then raise exception 'duplicate listings created'; end if;
  select * into l from listings where job_id = 1;
  if l.first_seen_at > now() - interval '23 hours' then raise exception 'first_seen_at moved'; end if;
  if l.last_seen_at < now() - interval '1 minute' then raise exception 'last_seen_at did not move'; end if;
  if l.raw->>'v' <> '2' then raise exception 'raw not refreshed'; end if;
  if l.posted_by <> 'agency' then raise exception 'posted_by not updated'; end if;
  if l.url is null or l.posted_date is null then raise exception 'url/posted_date wiped by null update'; end if;
end $$;

-- RLS: an authenticated non-owner sees nothing; the owner sees rows.
insert into app_users (user_id, email) values ('00000000-0000-0000-0000-000000000001', 'owner@example.com');
set role authenticated;
set "request.jwt.claim.sub" = '00000000-0000-0000-0000-000000000002';
do $$ begin
  if (select count(*) from listings) <> 0 then raise exception 'RLS: stranger can read listings'; end if;
end $$;
do $$ begin
  perform upsert_listings(1, '[]');
  raise exception 'RLS: authenticated can execute upsert_listings';
exception when insufficient_privilege then null;
end $$;
set "request.jwt.claim.sub" = '00000000-0000-0000-0000-000000000001';
do $$ begin
  if (select count(*) from listings) <> 3 then raise exception 'RLS: owner cannot read listings'; end if;
  if (select count(*) from templates where channel <> 'linkedin' or name <> 'Role Match DM') < 5 then raise exception 'seed templates missing'; end if;
end $$;
reset role;

-- Every public table has RLS enabled.
do $$
declare t text;
begin
  for t in select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity loop
    raise exception 'RLS not enabled on %', t;
  end loop;
end $$;

-- Territory exclusivity: a search can belong to one client only.
insert into clients (plan, start_date) values ('pilot', current_date), ('pilot', current_date);
insert into client_territories (client_id, search_id) values (1, 1);
do $$ begin
  insert into client_territories (client_id, search_id) values (2, 1);
  raise exception 'territory exclusivity not enforced';
exception when unique_violation then null;
end $$;

select 'ALL SQL CHECKS PASSED' as result;
