"""Repost history from the Wayback Machine (archived Totaljobs ad URLs) and the reposts-only rule."""

import datetime as dt

import httpx

from app.detect import Listing, compute_hot_leads, rebuild_hot_leads
from app.normalise import lead_key
from app.role_repo import LocalRoleRepo
from app.wayback import ArchiveReposts, Wayback, company_slugs
from tests.test_detect import NOW, RUN, L, one

# Real CDX rows the owner got from the server (4 Nov 2026), trimmed.
REAL_ROWS = [["timestamp", "original"],
             ["20250722230725", "https://www.totaljobs.com/job/management-accountant/euro-car-parks-job105266767"],
             ["20260209002233", "https://www.totaljobs.com/job/management-accountant/euro-car-parks-job106657849?src=search&page=1"],
             ["20260216233528", "https://www.totaljobs.com/job/management-accountant/euro-car-parks-job106657849?src=search&page=1"]]


def fake_archive(rows_by_prefix, calls=None):
    def h(req):
        prefix = req.url.params["url"]
        if calls is not None:
            calls.append(prefix)
        assert req.url.params["matchType"] == "prefix"
        return httpx.Response(200, json=rows_by_prefix.get(prefix, []))
    repo = LocalRoleRepo(None, seed=False)
    return ArchiveReposts(Wayback(repo, transport=httpx.MockTransport(h), min_interval=0, sleep=lambda s: None))


def test_euro_car_parks_two_earlier_postings():
    calls = []
    a = fake_archive({"totaljobs.com/job/management-accountant/euro-car-parks-job": REAL_ROWS}, calls)
    ev = a.prior_postings("Euro Car Parks Ltd", "Management Accountant - Hybrid", "management accountant",
                          before=dt.date(2026, 6, 1))
    assert ev["prior_count"] == 2                                   # job 105266767 (Jul 2025) and 106657849 (Feb 2026)
    assert ev["oldest_capture"] == dt.date(2025, 7, 22)
    assert ev["old_url"] == ("https://web.archive.org/web/20260209002233/"
                             "https://www.totaljobs.com/job/management-accountant/euro-car-parks-job106657849")
    assert calls[0] == "totaljobs.com/job/management-accountant/euro-car-parks-ltd-job"   # tried 'ltd' slug first
    n = len(calls)
    later = a.prior_postings("Euro Car Parks Ltd", "Management Accountant", "management accountant",
                             before=dt.date(2026, 11, 1))
    assert len(calls) == n                                          # cached
    assert later["prior_count"] == 1                                # July 2025 is over a year old: ignored


def test_recent_capture_is_a_cross_post_not_a_repost():
    a = fake_archive({"totaljobs.com/job/management-accountant/euro-car-parks-job": REAL_ROWS})
    ev = a.prior_postings("Euro Car Parks", "Management Accountant", "management accountant", before=dt.date(2026, 2, 20))
    assert ev["prior_count"] == 1                                   # the Feb 2026 capture is the same live vacancy


def test_company_slugs():
    assert company_slugs("B&M Retail Ltd") == ["b-m-retail-ltd", "b-m-retail"]
    assert company_slugs("Clark Contracts") == ["clark-contracts"]


def test_reposts_only_rule():
    key = lead_key("harlow group", "management accountant", "leeds")
    # two copies live at the same time: not a repost
    simultaneous = [L(1, 2, 0), L(2, 1, 0)]
    assert one(compute_hot_leads(simultaneous, RUN, NOW, threshold=2, reposts_only=True)) is None
    # one ad open 40 days: not a repost
    assert one(compute_hot_leads([L(1, 40, 0)], RUN, NOW, threshold=2, reposts_only=True)) is None
    # posted, ended, posted again (our own logs)
    lead = one(compute_hot_leads([L(1, 50, 30), L(2, 10, 0)], RUN, NOW, threshold=2, reposts_only=True))
    assert lead is not None and lead["reposted"] and lead["posting_count_90d"] == 2
    # only one ad seen by us, but the archive has an earlier one
    arch = {key: {"prior_count": 1, "oldest_capture": dt.date(2026, 3, 1), "old_url": "https://web.archive.org/web/x"}}
    lead = one(compute_hot_leads([L(1, 3, 0)], RUN, NOW, threshold=2, reposts_only=True, archive=arch))
    assert lead["posting_count_90d"] == 2 and lead["old_ad_url"] == "https://web.archive.org/web/x"
    assert lead["first_seen_at"].date() == dt.date(2026, 3, 1)


def test_rebuild_checks_archive_and_fills_old_ad(store):
    store.q("insert into searches (id, label, keywords, location) values (1, 'Accountancy - Leeds', '{a}', 'Leeds')")
    store.q("insert into runs (search_id, status, started_at) values (1, 'ok', now() - interval '30 minutes')")
    store.q("""insert into listings (job_id, posted_by, employer_name, employer_norm, title, title_norm, location_name,
               location_norm, url, first_seen_at, last_seen_at, raw) values (1, 'employer', 'Euro Car Parks Ltd',
               'euro car parks', 'Management Accountant', 'management accountant', 'Leeds', 'leeds', 'https://r/1',
               now() - interval '1 day', now(), '{}')""")
    store.q("insert into listing_searches values (1, 1)")
    a = fake_archive({"totaljobs.com/job/management-accountant/euro-car-parks-job": REAL_ROWS})
    out = rebuild_hot_leads(store, threshold=2, archive=a, reposts_only=True)
    assert out["hot"] == 1
    h = store.one("select * from hot_leads")
    # today is Nov 2026: the Feb 2026 ad counts, the Jul 2025 one is over a year old
    assert h["archive_prior"] == 1 and h["posting_count_90d"] == 2 and h["old_ad_url"].startswith("https://web.archive.org/")
    assert h["first_seen_at"].date() == dt.date(2026, 2, 9)
    # checked once; the next rebuild doesn't query the archive again for 30 days
    a2 = fake_archive({})
    rebuild_hot_leads(store, threshold=2, archive=a2, reposts_only=True)
    assert store.one("select count(*) as n from hot_leads")["n"] == 1
