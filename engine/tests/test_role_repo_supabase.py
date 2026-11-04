import json

import httpx

from app.role_repo import SupabaseRoleRepo

KEY = "service-role-test-key"


def repo(handler):
    return SupabaseRoleRepo("https://proj.supabase.co", KEY, transport=httpx.MockTransport(handler))


def test_upsert_job_merges_on_employer_and_url_without_touching_first_seen():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(201, json=[{"id": 1, **json.loads(req.content)}])

    repo(h).upsert_job({"employer_id": 3, "source": "careers_page", "title": "Electrical Estimator",
                        "title_norm": "electrical estimator", "location_text": None, "url": "https://x/1",
                        "date_posted": None, "lat": None})
    req = seen[0]
    assert req.url.path == "/rest/v1/employer_jobs"
    assert req.url.params["on_conflict"] == "employer_id,url"
    assert "resolution=merge-duplicates" in req.headers["Prefer"]
    body = json.loads(req.content)
    assert "first_seen_at" not in body and "last_seen_at" in body
    assert "date_posted" not in body and "lat" not in body     # nulls never wipe stored values
    assert body["location_text"] is None


def test_upsert_match_keeps_owner_status():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(201, json=[json.loads(req.content)])

    repo(h).upsert_match({"brief_id": 1, "employer_job_id": 2, "score": 8, "reasons": ["x"], "distance_miles": 3.0})
    body = json.loads(seen[0].content)
    assert seen[0].url.params["on_conflict"] == "brief_id,employer_job_id"
    assert "status" not in body and "first_matched_at" not in body


def test_add_employer_fills_blanks_only():
    calls = []

    def h(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=[{"id": 5, "name": "Acme Ltd", "name_norm": "acme",
                                              "careers_url": "https://acme/careers", "town": None}])
        return httpx.Response(204)

    r = repo(h).add_employer({"name": "ACME Limited", "careers_url": "https://other", "town": "Bolton",
                              "source": "reed"})
    patch = next(c for c in calls if c.method == "PATCH")
    assert json.loads(patch.content) == {"town": "Bolton"}
    assert r["careers_url"] == "https://acme/careers"
