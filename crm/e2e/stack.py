"""End-to-end smoke test: throwaway Postgres + fake Reed + engine + CRM + Playwright.

    python -I e2e/stack.py        (from crm/, after `npm run build`; needs the engine's Python deps)
"""

import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

CRM = Path(__file__).resolve().parents[1]
ROOT = CRM.parent
ENGINE = ROOT / "engine"
PY = os.environ.get("ENGINE_PYTHON", sys.executable)
PG_BIN = os.environ.get("PG_BIN") or sorted(Path("/usr/lib/postgresql").glob("*/bin"))[-1].as_posix()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def job(i, employer, title, location="Leeds"):
    return {"jobId": 90000000 + i, "employerId": 1000 + i, "employerName": employer, "jobTitle": title,
            "locationName": location, "minimumSalary": 40000, "maximumSalary": 45000, "currency": "GBP",
            "expirationDate": "20/12/2026", "date": "01/09/2026", "jobDescription": "A role in our finance team.",
            "applications": 3, "jobUrl": f"https://www.reed.co.uk/jobs/x/{90000000 + i}"}


EMPLOYER_ADS = [job(1, "Harlow Group Ltd", "Management Accountant"), job(2, "Harlow Group Ltd", "Management Accountant - Hybrid"),
                job(3, "Harlow Group Ltd", "Management Accountant (Leeds)"), job(4, "Northgate Foods", "Credit Controller")]
AGENCY_ADS = [job(10 + i, "Aire Valley Recruitment", "Management Accountant") for i in range(3)] + \
             [job(20, "Pennine Finance Search", "Finance Manager")]


class FakeReed(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.endswith("/search"):
            items = EMPLOYER_ADS if q.get("postedByDirectEmployer") == "true" else AGENCY_ADS
            skip = int(q.get("resultsToSkip", 0))
            body = {"results": items[skip:skip + 100], "totalResults": len(items), "ambiguousLocations": []}
        else:
            jid = int(u.path.rsplit("/", 1)[-1])
            body = {"jobId": jid, "jobUrl": f"https://www.reed.co.uk/jobs/x/{jid}", "expirationDate": "20/12/2026"}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def wait_http(url, timeout=60, headers=None):
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=2)
            return
        except Exception as e:
            if hasattr(e, "code") and e.code < 500:
                return
            time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {url}")


def main() -> int:
    procs: list[subprocess.Popen] = []
    work = tempfile.mkdtemp(prefix="rr-e2e-")
    run_as = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if run_as:
        shutil.chown(work, "postgres")
    data = f"{work}/data"
    try:
        subprocess.run(run_as + [f"{PG_BIN}/initdb", "-D", data, "-A", "trust", "-U", "postgres"], check=True, capture_output=True)
        subprocess.run(run_as + [f"{PG_BIN}/pg_ctl", "-D", data, "-o", f"-k {work} -c listen_addresses=''", "-l", f"{work}/log", "-w", "start"],
                       check=True, capture_output=True)
        dsn = f"postgresql://postgres@/postgres?host={work}"
        sqls = [ROOT / "supabase/compat/plain_postgres.sql", *sorted((ROOT / "supabase/migrations").glob("*.sql")),
                ROOT / "supabase/seed.sql", ROOT / "supabase/seed_role_match.sql"]
        for f in sqls:
            subprocess.run(run_as + [f"{PG_BIN}/psql", "-h", work, "-U", "postgres", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(f)],
                           check=True, capture_output=True)

        # No internet archive in the test sandbox.
        subprocess.run(run_as + [f"{PG_BIN}/psql", "-h", work, "-U", "postgres", "-q", "-c",
                                 "update app_settings set value = '0' where key = 'archive_checks_per_run'"],
                       check=True, capture_output=True)
        reed_port, engine_port, crm_port = free_port(), free_port(), free_port()
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", reed_port), FakeReed)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        secret = "e2e-engine-secret-" + os.urandom(6).hex()
        env = {**os.environ, "DATABASE_URL": dsn, "ENGINE_API_SECRET": secret, "REED_API_KEY": "fake",
               "REED_BASE_URL": f"http://127.0.0.1:{reed_port}/api/1.0", "REED_REQUESTS_PER_SECOND": "0"}
        procs.append(subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", str(engine_port)], cwd=ENGINE, env=env,
                                      stdout=open(f"{work}/engine.log", "w"), stderr=subprocess.STDOUT))
        wait_http(f"http://127.0.0.1:{engine_port}/health", headers={"X-API-Key": secret})

        crm_env = {**os.environ, "ENGINE_URL": f"http://127.0.0.1:{engine_port}", "ENGINE_API_SECRET": secret,
                   "SESSION_SECRET": os.urandom(24).hex(), "CRM_PASSWORD": "e2e-password", "PORT": str(crm_port)}
        procs.append(subprocess.Popen(["npx", "next", "start", "-p", str(crm_port)], cwd=CRM, env=crm_env,
                                      stdout=open(f"{work}/crm.log", "w"), stderr=subprocess.STDOUT))
        wait_http(f"http://127.0.0.1:{crm_port}/login")

        r = subprocess.run(["node", str(CRM / "e2e/smoke.mjs")], env={**os.environ, "CRM_URL": f"http://127.0.0.1:{crm_port}",
                                                                     "CRM_PASSWORD": "e2e-password"})
        if r.returncode != 0:
            print("---- engine log ----\n" + Path(f"{work}/engine.log").read_text()[-4000:])
            print("---- crm log ----\n" + Path(f"{work}/crm.log").read_text()[-3000:])
        return r.returncode
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        subprocess.run(run_as + [f"{PG_BIN}/pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
