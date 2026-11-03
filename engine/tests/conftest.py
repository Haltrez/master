from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = "test-reed-key-5f3a9c"


@pytest.fixture
def sample() -> dict[str, Any]:
    return json.loads((FIXTURES / "reed_search_sample.json").read_text())


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_items(base: dict[str, Any], n: int, start_id: int = 70000000, **overrides: Any) -> list[dict[str, Any]]:
    items = []
    for i in range(n):
        item = copy.deepcopy(base)
        item["jobId"] = start_id + i
        item.update(overrides)
        items.append(item)
    return items


class FakeReed:
    """Serves the Reed /search endpoint from in-memory datasets, honouring paging.

    datasets: {(keywords, 'employer'|'agency'): [items]}
    script: optional list of status codes to return before normal responses.
    """

    def __init__(self, datasets: dict[tuple[str, str], list[dict[str, Any]]], script: list[int] | None = None):
        self.datasets = datasets
        self.script = list(script or [])
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            code = self.script.pop(0)
            if code != 200:
                headers = {"Retry-After": "7"} if code == 429 else {}
                return httpx.Response(code, json={"message": "error"}, headers=headers)
        if request.url.path.endswith("/search"):
            q = request.url.params
            posted_by = "employer" if q["postedByDirectEmployer"] == "true" else "agency"
            assert q["postedByRecruitmentAgency"] == ("true" if posted_by == "agency" else "false")
            items = self.datasets.get((q["keywords"], posted_by), [])
            skip, take = int(q["resultsToSkip"]), int(q["resultsToTake"])
            return httpx.Response(200, json={"results": items[skip:skip + take],
                                             "ambiguousLocations": [], "totalResults": len(items)})
        job_id = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"jobId": job_id, "jobUrl": f"https://www.reed.co.uk/jobs/x/{job_id}"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
