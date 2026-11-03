import json

import httpx
import pytest

from app import db
from app.db import DBError, SupabaseRepo

KEY = "service-role-test-key"


def make(handler):
    return SupabaseRepo("https://proj.supabase.co/", KEY, transport=httpx.MockTransport(handler))


def test_upsert_calls_rpc_in_batches(monkeypatch):
    monkeypatch.setattr(db, "UPSERT_BATCH", 2)
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=len(json.loads(req.content)["p_rows"]))

    repo = make(handler)
    n = repo.upsert_listings(7, [{"job_id": i} for i in range(5)])
    assert n == 5 and len(seen) == 3
    assert seen[0].url.path == "/rest/v1/rpc/upsert_listings"
    body = json.loads(seen[0].content)
    assert body["p_search_id"] == 7 and len(body["p_rows"]) == 2
    assert seen[0].headers["apikey"] == KEY
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"


def test_get_search_and_overrides():
    def handler(req):
        if req.url.path.endswith("/searches"):
            assert req.url.params["id"] == "eq.1"
            return httpx.Response(200, json=[{"id": 1, "label": "A", "keywords": ["x", "y"],
                                              "location": "Leeds", "radius_miles": 15}])
        return httpx.Response(200, json=[{"employer_norm": "acme", "is_agency": True}])

    repo = make(handler)
    s = repo.get_search(1)
    assert s.keywords == ["x", "y"] and s.location == "Leeds"
    assert repo.get_overrides() == {"acme": True}


def test_errors_do_not_leak_key():
    repo = make(lambda req: httpx.Response(400, json={"message": "bad"}))
    with pytest.raises(DBError) as e:
        repo.get_overrides()
    assert KEY not in str(e.value) and "400" in str(e.value)


def test_requires_config():
    with pytest.raises(DBError):
        SupabaseRepo("", "")
