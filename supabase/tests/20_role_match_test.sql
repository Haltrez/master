-- Checks for 003_role_match.sql + seed_role_match.sql.
\set ON_ERROR_STOP on

do $$ begin
  if (select count(*) from role_families) <> 6 then raise exception 'role families not seeded'; end if;
  if (select count(*) from employers where source = 'manual' and careers_url is not null) <> 3 then
    raise exception 'seed employers missing'; end if;
  if not exists (select 1 from templates where name = 'Role Match DM' and channel = 'linkedin') then
    raise exception 'Role Match DM template missing'; end if;
end $$;

-- Re-running the seed is harmless.
\ir ../seed_role_match.sql
do $$ begin
  if (select count(*) from role_families) <> 6 or (select count(*) from templates where name = 'Role Match DM') <> 1 then
    raise exception 'seed is not idempotent'; end if;
end $$;

-- Direct employers seen in Reed listings join the directory as research rows.
do $$
declare n int;
begin
  perform upsert_listings(1, '[{"job_id": 900, "posted_by": "employer", "employer_name": "Mersey Building Services Ltd",
    "employer_norm": "mersey building services", "title": "Electrical Estimator", "title_norm": "electrical estimator",
    "raw": {}}, {"job_id": 901, "posted_by": "agency", "employer_name": "Some Recruitment", "employer_norm": "some recruitment",
    "title": "Electrical Estimator", "title_norm": "electrical estimator", "raw": {}}]');
  select count(*) into n from employers where name_norm = 'mersey building services' and source = 'reed' and status = 'research';
  if n <> 1 then raise exception 'reed employer not added to directory'; end if;
  if exists (select 1 from employers where name_norm = 'some recruitment') then raise exception 'agency added as employer'; end if;
  -- an existing employer row is never overwritten
  update employers set careers_url = 'https://mersey.example/careers', status = 'active' where name_norm = 'mersey building services';
  perform upsert_listings(1, '[{"job_id": 902, "posted_by": "employer", "employer_name": "Mersey Building Services", "employer_norm": "mersey building services", "title": "Estimator", "title_norm": "estimator", "raw": {}}]');
  if (select status from employers where name_norm = 'mersey building services') <> 'active' then raise exception 'trigger overwrote employer'; end if;
end $$;

-- Jobs unique per employer + URL; matches unique per brief + job; briefs need an owner.
insert into employer_jobs (employer_id, source, title, title_norm, url) values (1, 'careers_page', 'Electrical Project Manager', 'electrical project manager', 'https://x/1');
do $$ begin
  insert into employer_jobs (employer_id, source, title, title_norm, url) values (1, 'careers_page', 'Dup', 'dup', 'https://x/1');
  raise exception 'duplicate employer job allowed';
exception when unique_violation then null; end $$;

insert into agencies (name, name_norm) values ('MCM Recruitment Solutions', 'mcm recruitment solutions');
insert into candidate_briefs (agency_id, role_family_id, label, location) values (1, 6, 'Electrical Project Manager - Manchester', 'Manchester');
do $$ begin
  insert into candidate_briefs (role_family_id, label, location) values (6, 'orphan', 'Manchester');
  raise exception 'brief without agency or client allowed';
exception when check_violation then null; end $$;
insert into brief_matches (brief_id, employer_job_id, score, reasons) values (1, 1, 8, '{"Exact title match (+4)"}');
do $$ begin
  insert into brief_matches (brief_id, employer_job_id, score, reasons) values (1, 1, 1, '{}');
  raise exception 'duplicate match allowed';
exception when unique_violation then null; end $$;

-- RLS: a stranger sees nothing in the new tables.
set role authenticated;
set "request.jwt.claim.sub" = '00000000-0000-0000-0000-000000000002';
do $$ begin
  if (select count(*) from employers) + (select count(*) from candidate_briefs) + (select count(*) from brief_matches) <> 0 then
    raise exception 'RLS: stranger can read role match tables'; end if;
end $$;
reset role;

select 'ALL ROLE MATCH SQL CHECKS PASSED' as result;
