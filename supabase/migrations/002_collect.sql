-- Repost Radar: collector upsert, called by the engine via PostgREST RPC
-- (POST /rest/v1/rpc/upsert_listings) with the service role key.
--
-- Inserts new listings with first_seen_at = now(); for listings already
-- known, moves last_seen_at to now() and refreshes raw and the parsed
-- fields, but never touches first_seen_at. Links every listing to the
-- search. Returns the number of listings that were new.

create or replace function public.upsert_listings(p_search_id bigint, p_rows jsonb)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_new integer;
begin
  with incoming as (
    select distinct on (r.job_id) r.*
    from jsonb_to_recordset(p_rows) as r(
      job_id bigint,
      source text,
      posted_by text,
      employer_id bigint,
      employer_name text,
      employer_norm text,
      title text,
      title_norm text,
      location_name text,
      location_norm text,
      min_salary numeric,
      max_salary numeric,
      url text,
      posted_date date,
      expiration_date date,
      raw jsonb
    )
    order by r.job_id
  ),
  up as (
    insert into listings as l (
      job_id, source, posted_by, employer_id, employer_name, employer_norm,
      title, title_norm, location_name, location_norm, min_salary,
      max_salary, url, posted_date, expiration_date, raw
    )
    select job_id, coalesce(source, 'reed'), posted_by, employer_id,
           employer_name, employer_norm, title, title_norm, location_name,
           location_norm, min_salary, max_salary, url, posted_date,
           expiration_date, raw
    from incoming
    on conflict (job_id) do update set
      last_seen_at    = now(),
      raw             = excluded.raw,
      posted_by       = excluded.posted_by,
      employer_id     = coalesce(excluded.employer_id, l.employer_id),
      employer_name   = excluded.employer_name,
      employer_norm   = excluded.employer_norm,
      title           = excluded.title,
      title_norm      = excluded.title_norm,
      location_name   = excluded.location_name,
      location_norm   = excluded.location_norm,
      min_salary      = excluded.min_salary,
      max_salary      = excluded.max_salary,
      url             = coalesce(excluded.url, l.url),
      posted_date     = coalesce(excluded.posted_date, l.posted_date),
      expiration_date = coalesce(excluded.expiration_date, l.expiration_date)
    returning l.job_id, (xmax = 0) as inserted
  ),
  links as (
    insert into listing_searches (job_id, search_id)
    select job_id, p_search_id from up
    on conflict do nothing
  )
  select count(*) filter (where inserted) into v_new from up;

  return coalesce(v_new, 0);
end;
$$;

revoke execute on function public.upsert_listings(bigint, jsonb) from public;
revoke execute on function public.upsert_listings(bigint, jsonb) from anon, authenticated;
grant execute on function public.upsert_listings(bigint, jsonb) to service_role;
