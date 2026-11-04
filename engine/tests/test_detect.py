import datetime as dt

from app.detect import Listing, compute_hot_leads, rebuild_hot_leads
from app.normalise import lead_key

NOW = dt.datetime(2026, 11, 4, 7, 0, tzinfo=dt.timezone.utc)
RUN = {1: NOW - dt.timedelta(minutes=30)}


def L(job_id, first_days_ago, last_days_ago=None, *, employer="harlow group", title="management accountant",
      town="leeds", source="reed", posted_days_ago=None, searches=(1,)):
    last = first_days_ago if last_days_ago is None else last_days_ago
    return Listing(job_id=job_id, source=source, employer_name=employer.title(), employer_norm=employer,
                   title=title.title(), title_norm=title, location_name=town.title(), location_norm=town,
                   first_seen_at=NOW - dt.timedelta(days=first_days_ago),
                   last_seen_at=NOW - dt.timedelta(days=last_days_ago if last_days_ago is not None else first_days_ago,
                                                   minutes=0 if last else -5),
                   posted_date=(NOW - dt.timedelta(days=posted_days_ago)).date() if posted_days_ago is not None else None,
                   url=f"https://www.reed.co.uk/jobs/x/{job_id}", search_ids=list(searches))


def one(leads, **kw):
    key = lead_key(kw.get("employer", "harlow group"), kw.get("title", "management accountant"), kw.get("town", "leeds"))
    return next((l for l in leads if l["lead_key"] == key), None)


def test_role_posted_four_times_in_sixty_days_is_hot_with_right_count_and_score():
    # The same role reposted every ~2-3 weeks; each ad lives ~10 days. The latest is live now.
    ls = [L(1, 60, 50), L(2, 42, 32), L(3, 21, 11), L(4, 3, 0)]
    lead = one(compute_hot_leads(ls, RUN, NOW))
    assert lead is not None
    assert lead["posting_count_90d"] == 4
    assert lead["live_now"] and not lead["long_open"]
    assert lead["score"] == 2 * (4 - 1) + 1          # +1 live
    assert lead["job_ids"] == [1, 2, 3, 4]


def test_threshold():
    assert one(compute_hot_leads([L(1, 40, 30), L(2, 20, 10)], RUN, NOW)) is None   # 2 < 3, not live
    assert one(compute_hot_leads([L(1, 40, 30), L(2, 20, 10)], RUN, NOW, threshold=2)) is not None


def test_long_open_single_ad():
    lead = one(compute_hot_leads([L(1, 35, 0)], RUN, NOW))
    assert lead["long_open"] and lead["posting_count_90d"] == 1
    assert lead["score"] == 0 + 2 + 1


def test_long_open_from_reed_posted_date_on_day_one():
    # We only started collecting yesterday, but Reed says the ad was posted 40 days ago.
    lead = one(compute_hot_leads([L(1, 1, 0, posted_days_ago=40)], RUN, NOW))
    assert lead["long_open"]


def test_day_one_signal_two_live_ads():
    lead = one(compute_hot_leads([L(1, 2, 0), L(2, 1, 0)], RUN, NOW))
    assert lead is not None and lead["posting_count_90d"] == 2 and lead["live_now"]


def test_same_ad_on_reed_and_adzuna_counts_once():
    ls = [L(1, 40, 30), L(2, 20, 10), L(10**15 + 9, 40, 30, source="adzuna"), L(10**15 + 8, 20, 10, source="adzuna")]
    assert one(compute_hot_leads(ls, RUN, NOW)) is None                       # 2 per source, not 4
    assert one(compute_hot_leads(ls + [L(3, 5, 4)], RUN, NOW))["posting_count_90d"] == 3


def test_employer_with_three_hot_keys_gets_a_point():
    ls = []
    for i, t in enumerate(["management accountant", "credit controller", "payroll officer"]):
        ls += [L(100 + i * 10 + n, 60 - n * 20, 55 - n * 20, title=t) for n in range(3)]
    leads = compute_hot_leads(ls, RUN, NOW)
    assert len(leads) == 3 and all(l["score"] == 2 * 2 + 1 for l in leads)


def test_outside_lookback_ignored():
    assert compute_hot_leads([L(1, 200, 150), L(2, 180, 120), L(3, 160, 100)], RUN, NOW) == []


def test_live_uses_latest_run_of_its_search():
    run = {1: NOW - dt.timedelta(minutes=30), 2: NOW - dt.timedelta(days=3)}
    stale = L(1, 10, 3, searches=(2,))
    stale.last_seen_at = run[2] + dt.timedelta(minutes=5)
    lead = one(compute_hot_leads([stale, L(2, 40, 35, searches=(2,)), L(3, 70, 60, searches=(2,))], run, NOW))
    assert lead["live_now"]   # seen in the latest run of search 2, even though that run was 3 days ago


# --- against the real database ---------------------------------------------------

def _seed(store):
    store.q("insert into searches (id, label, keywords, location) values (1, 'Accountancy - Leeds', "
            "'{management accountant}', 'Leeds')")
    store.q("insert into runs (search_id, status, started_at) values (1, 'ok', now() - interval '30 minutes')")
    rows = [(1, 60, 50), (2, 42, 32), (3, 21, 11), (4, 3, 0)]
    for job, first, last in rows:
        store.q("""insert into listings (job_id, posted_by, employer_name, employer_norm, title, title_norm,
                   location_name, location_norm, url, first_seen_at, last_seen_at, raw)
                   values (%s, 'employer', 'Harlow Group Ltd', 'harlow group', 'Management Accountant',
                   'management accountant', 'Leeds', 'leeds', %s, now() - make_interval(days => %s),
                   now() - make_interval(days => %s), '{}')""", (job, f"https://r/{job}", first, last))
        store.q("insert into listing_searches values (%s, 1)", (job,))
    # an agency reposting constantly must never become a hot lead
    for job in range(10, 16):
        store.q("""insert into listings (job_id, posted_by, employer_name, employer_norm, title, title_norm,
                   location_name, location_norm, raw, first_seen_at, last_seen_at) values
                   (%s, 'agency', 'Aire Recruitment', 'aire recruitment', 'Management Accountant',
                   'management accountant', 'Leeds', 'leeds', '{}', now() - make_interval(days => %s), now())""",
                (job, job))


class FakeReedDetails:
    def __init__(self):
        self.calls = []

    def get_details(self, job_id, budget=None):
        self.calls.append(job_id)
        return {"jobId": job_id, "jobUrl": f"https://www.reed.co.uk/jobs/management-accountant/{job_id}",
                "expirationDate": "15/12/2026"}


def test_rebuild_on_postgres_keeps_owner_status_and_skips_agencies(store):
    _seed(store)
    reed = FakeReedDetails()
    out = rebuild_hot_leads(store, reed=reed)
    assert (out["hot"], out["new"]) == (1, 1)
    lead = store.one("select * from hot_leads")
    assert lead["employer_norm"] == "harlow group" and lead["posting_count_90d"] == 4 and lead["score"] == 7
    assert lead["expiration_date"] == dt.date(2026, 12, 15)
    assert lead["latest_url"].endswith("/4") and reed.calls == [4]
    assert store.val("select count(*) from hot_lead_searches") == 1
    assert not store.q("select 1 from hot_leads where employer_norm = 'aire recruitment'")

    store.q("update hot_leads set status = 'used'")
    out2 = rebuild_hot_leads(store, reed=reed)
    assert out2["new"] == 0 and reed.calls == [4]            # details only for newly hot leads
    assert store.one("select status from hot_leads")["status"] == "used"

    # cooled off: listings gone -> untouched 'new' leads are removed, 'used' ones kept
    store.q("delete from listings where job_id between 1 and 4")
    rebuild_hot_leads(store)
    assert store.val("select count(*) from hot_leads") == 1
    store.q("update hot_leads set status = 'new'")
    rebuild_hot_leads(store)
    assert store.val("select count(*) from hot_leads") == 0


def test_same_ad_in_several_places_in_one_run_is_not_a_repost():
    import dataclasses
    base = L(0, 0)
    ls = [dataclasses.replace(base, job_id=i, first_seen_at=NOW + dt.timedelta(seconds=i),
                              last_seen_at=NOW + dt.timedelta(seconds=i)) for i in range(10)]
    later = NOW + dt.timedelta(minutes=1)
    assert compute_hot_leads(ls, {}, later, threshold=2, reposts_only=True) == []
    gone = L(20, 10, 6)   # an earlier ad we saw end 6 days ago
    assert len(compute_hot_leads(ls + [gone], {}, later, threshold=2, reposts_only=True)) == 1
