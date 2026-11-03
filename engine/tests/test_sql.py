"""Runs the SQL checks against a throwaway local Postgres, if one is installed."""

import glob
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "supabase" / "tests" / "run_local.sh"
HAVE_PG = bool(os.environ.get("PG_BIN") or glob.glob("/usr/lib/postgresql/*/bin/initdb")
               or glob.glob("/opt/homebrew/opt/postgresql@*/bin/initdb"))


@pytest.mark.skipif(not HAVE_PG or not shutil.which("bash"), reason="local Postgres server binaries not installed")
def test_migrations_and_upsert_sql():
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL SQL CHECKS PASSED" in proc.stdout
