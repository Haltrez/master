#!/bin/sh
# Applies supabase/compat (plain Postgres only), migrations and seeds once each,
# in order, and records them in schema_migrations. Safe to run on every deploy.
#   DATABASE_URL=postgresql://... SUPABASE=0 scripts/migrate.sh
# If you already ran some files by hand (e.g. in the Supabase SQL editor), mark them done:
#   psql "$DATABASE_URL" -c "insert into schema_migrations values ('migrations/001_init.sql')"
set -eu
cd "$(dirname "$0")/../supabase"
: "${DATABASE_URL:?DATABASE_URL is not set}"
PSQL="psql $DATABASE_URL -v ON_ERROR_STOP=1 -q"
$PSQL -c "create table if not exists schema_migrations (filename text primary key, applied_at timestamptz not null default now())"

files=""
[ "${SUPABASE:-0}" = "1" ] || files="compat/plain_postgres.sql"
for f in migrations/*.sql; do files="$files $f"; done
files="$files seed.sql seed_role_match.sql"

for f in $files; do
  done_already=$($PSQL -tAc "select 1 from schema_migrations where filename = '$f'")
  if [ "$done_already" = "1" ]; then continue; fi
  echo "applying $f"
  $PSQL -1 -f "$f"
  $PSQL -c "insert into schema_migrations (filename) values ('$f')"
done
echo "database up to date"
