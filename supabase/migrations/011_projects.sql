-- Project briefs ("build a hot-lead list for this prospect agency...") run by the research agent.
create table if not exists projects (
  id bigserial primary key,
  title text not null,
  brief text not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'error', 'cancelled')),
  steps int not null default 0,
  log jsonb not null default '[]',
  result jsonb,
  error text,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
alter table projects enable row level security;
drop policy if exists owner_all on projects;
create policy owner_all on projects for all to authenticated
  using (public.is_owner()) with check (public.is_owner());
