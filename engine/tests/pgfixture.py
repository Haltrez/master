"""A throwaway Postgres for tests: migrations + seeds applied once per session,
tables truncated and re-seeded before each test that asks for `store`."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUPA = ROOT / "supabase"


def _pg_bin() -> str | None:
    if os.environ.get("PG_BIN"):
        return os.environ["PG_BIN"]
    cands = sorted(glob.glob("/usr/lib/postgresql/*/bin")) + sorted(glob.glob("/opt/homebrew/opt/postgresql@*/bin"))
    cands = [c for c in cands if os.path.exists(os.path.join(c, "initdb"))]
    return cands[-1] if cands else None


def sql_files() -> list[Path]:
    return ([SUPA / "compat" / "plain_postgres.sql"] + sorted((SUPA / "migrations").glob("*.sql"))
            + [SUPA / "seed.sql", SUPA / "seed_role_match.sql"])


@pytest.fixture(scope="session")
def pg_dsn():
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        yield test_url
        return
    pg_bin = _pg_bin()
    if not pg_bin:
        pytest.skip("Postgres server binaries not installed (set PG_BIN or TEST_DATABASE_URL)")
    work = tempfile.mkdtemp(prefix="rr-pg-")
    run = []
    if os.geteuid() == 0:
        shutil.chown(work, "postgres")
        run = ["runuser", "-u", "postgres", "--"]
    data = os.path.join(work, "data")
    subprocess.run(run + [f"{pg_bin}/initdb", "-D", data, "-A", "trust", "-U", "postgres"], check=True,
                   capture_output=True)
    subprocess.run(run + [f"{pg_bin}/pg_ctl", "-D", data, "-o", f"-k {work} -c listen_addresses='' -c fsync=off",
                          "-l", os.path.join(work, "log"), "-w", "start"], check=True, capture_output=True)
    dsn = f"host={work} user=postgres dbname=postgres"
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        for f in sql_files():
            c.execute(f.read_text(encoding="utf-8"))
    try:
        yield dsn
    finally:
        subprocess.run(run + [f"{pg_bin}/pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


TABLES = ("deliveries", "client_territories", "clients", "activities", "contacts", "agency_searches", "agencies",
          "hot_lead_searches", "hot_leads", "employer_overrides", "listing_searches", "listings", "runs", "searches",
          "templates", "brief_matches", "candidate_briefs", "employer_jobs", "employers", "role_families",
          "http_cache", "suppression_list", "app_settings", "tasks", "app_users", "lead_contacts", "archive_evidence",
          "lead_orders", "assistant_messages", "projects", "agency_crm_companies",
          "outreach_queue", "outreach_replies", "lead_assignments")


@pytest.fixture
def store(pg_dsn):
    import psycopg

    from app.pg import PgStore
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("truncate " + ", ".join(TABLES) + " restart identity cascade")
        c.execute((SUPA / "seed.sql").read_text(encoding="utf-8"))
        c.execute((SUPA / "seed_role_match.sql").read_text(encoding="utf-8"))
        mig = (SUPA / "migrations" / "004_engine.sql").read_text(encoding="utf-8")
        c.execute(mig[mig.index("insert into app_settings"):])
    s = PgStore(pg_dsn)
    yield s
    s.close()
