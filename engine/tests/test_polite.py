import httpx
import pytest

from app.polite import (USER_AGENT, ForbiddenHost, PageBudget, PageCapReached, PoliteFetcher, RobotsDisallowed,
                        is_forbidden_host)
from tests.role_fakes import Clock


def fetcher(handler, clock):
    return PoliteFetcher(transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock.monotonic)


def site(robots: str | None = "User-agent: *\nDisallow: /private/\n", robots_status: int = 200):
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots or "")
        return httpx.Response(200, text="<html>ok</html>")
    return handler, seen


def test_robots_disallow_blocks_and_sends_user_agent():
    clock = Clock()
    h, seen = site()
    f = fetcher(h, clock)
    with pytest.raises(RobotsDisallowed):
        f.get("https://acme.co.uk/private/jobs", PageBudget())
    assert [r.url.path for r in seen] == ["/robots.txt"]
    assert seen[0].headers["User-Agent"] == USER_AGENT == "SmartEmployBot/1.0 (+https://smartemploy.io/bot)"
    assert f.get("https://acme.co.uk/careers", PageBudget()).status_code == 200


def test_robots_checked_once_per_domain():
    clock = Clock()
    h, seen = site()
    f = fetcher(h, clock)
    b = PageBudget(10)
    for p in ("/a", "/b", "/c"):
        f.get("https://acme.co.uk" + p, b)
    assert [r.url.path for r in seen].count("/robots.txt") == 1


def test_missing_robots_allows_but_server_error_or_forbidden_blocks():
    clock = Clock()
    for status, allowed in ((404, True), (403, False), (500, False)):
        h, _ = site(robots="", robots_status=status)
        f = fetcher(h, clock)
        assert f.allowed("https://acme.co.uk/careers") is allowed, status


def test_rfc9309_longest_match_wins():
    clock = Clock()
    h, _ = site(robots="User-Agent: *\nDisallow: /\nAllow: /v/\n")
    f = fetcher(h, clock)
    assert f.allowed("https://hr.breathehr.com/v/estimator-1")
    assert not f.allowed("https://hr.breathehr.com/admin")


def test_robots_agent_specific_group():
    clock = Clock()
    h, _ = site(robots="User-agent: SmartEmployBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert not fetcher(h, clock).allowed("https://acme.co.uk/careers")


def test_five_seconds_per_domain_including_robots():
    clock = Clock()
    h, _ = site()
    f = fetcher(h, clock)
    b = PageBudget(10)
    f.get("https://acme.co.uk/careers", b)       # robots.txt, then page: one 5s wait
    f.get("https://acme.co.uk/jobs/1", b)        # another 5s wait
    f.get("https://other.co.uk/careers", b)      # different domain: robots, then a 5s wait for its page
    assert clock.sleeps == [5.0, 5.0, 5.0]


def test_page_cap_three_per_employer():
    clock = Clock()
    h, seen = site()
    f = fetcher(h, clock)
    b = PageBudget()
    for i in range(3):
        f.get(f"https://acme.co.uk/{i}", b)
    with pytest.raises(PageCapReached):
        f.get("https://acme.co.uk/3", b)
    assert len([r for r in seen if r.url.path != "/robots.txt"]) == 3


@pytest.mark.parametrize("url", ["https://www.indeed.co.uk/jobs", "https://uk.linkedin.com/jobs/x",
                                 "https://www.totaljobs.com/job/1", "https://www.cv-library.co.uk/job/1",
                                 "https://www.reed.co.uk/jobs/1"])
def test_never_fetches_job_boards(url):
    clock = Clock()
    h, seen = site()
    assert is_forbidden_host(url)
    with pytest.raises(ForbiddenHost):
        fetcher(h, clock).get(url, PageBudget())
    assert seen == []
