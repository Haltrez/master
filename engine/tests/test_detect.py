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
    assert lead["signal"] == "active" and not lead["evergreen"]
    assert lead["score"] == 4 + 2 * 2 + 1            # active re-post +4, two more re-posts +2 each, +1 live
    assert lead["job_ids"] == [1, 2, 3, 4]


def test_threshold():
    assert one(compute_hot_leads([L(1, 40, 30), L(2, 20, 10)], RUN, NOW)) is None   # 2 < 3, not live
    assert one(compute_hot_leads([L(1, 40, 30), L(2, 20, 10)], RUN, NOW, threshold=2)) is not None


def test_long_open_single_ad():
    lead = one(compute_hot_leads([L(1, 35, 0)], RUN, NOW))
    assert lead["long_open"] and lead["posting_count_90d"] == 1
    assert lead["signal"] == "passive" and lead["score"] == 0         # long open, never re-advertised: watch list


def test_long_open_from_reed_posted_date_on_day_one():
    # We only started collecting yesterday, but Reed says the ad was posted 40 days ago.
    lead = one(compute_hot_leads([L(1, 1, 0, posted_days_ago=40)], RUN, NOW))
    assert lead["long_open"]


def test_day_one_signal_two_live_ads():
    lead = one(compute_hot_leads([L(1, 2, 0), L(2, 1, 0)], RUN, NOW))
    assert lead is not None and lead["posting_count_90d"] == 1 and lead["live_now"]   # one posting, two copies


def test_same_ad_on_reed_and_adzuna_counts_once():
    ls = [L(1, 40, 30), L(2, 20, 10), L(10**15 + 9, 40, 30, source="adzuna"), L(10**15 + 8, 20, 10, source="adzuna")]
    assert one(compute_hot_leads(ls, RUN, NOW)) is None                       # 2 per source, not 4
    assert one(compute_hot_leads(ls + [L(3, 5, 4)], RUN, NOW))["posting_count_90d"] == 3


def test_employer_with_three_hot_keys_gets_a_point():
    ls = []
    for i, t in enumerate(["management accountant", "credit controller", "payroll officer"]):
        ls += [L(100 + i * 10 + n, 60 - n * 20, 55 - n * 20, title=t) for n in range(3)]
    leads = compute_hot_leads(ls, RUN, NOW)
    assert len(leads) == 3 and all(l["score"] == 4 + 2 + 1 for l in leads)   # active +4, one more re-post +2, 3 roles +1


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
    assert lead["employer_norm"] == "harlow group" and lead["posting_count_90d"] == 4 and lead["score"] == 9
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


def test_reworded_titles_at_the_same_company_and_town_are_one_role():
    ls = [L(1, 40, 30, title="sales executive"), L(2, 5, 0, title="door to door sales executive"),
          L(3, 5, 0, title="store manager"), L(4, 40, 30, title="manager")]
    leads = compute_hot_leads(ls, RUN, NOW, threshold=2, reposts_only=True)
    assert [(l["title_norm"], l["posting_count_90d"]) for l in leads] == [("door to door sales executive", 2)]


def test_titles_that_differ_by_a_place_are_different_vacancies():
    ls = [L(1, 40, 30, title="area sales manager"), L(2, 5, 0, title="area sales manager north east")]
    assert compute_hot_leads(ls, RUN, NOW, threshold=2, reposts_only=True) == []


# -- client feedback: ACTIVE re-posts vs PASSIVE evergreen listings -------------------------------------
def test_evergreen_page_goes_to_the_watch_list_not_hot():
    """One careers-style ad, up unchanged for 90 days with no closing date and 'always looking' wording."""
    ad = L(1, 90, 0)
    ad.description = "We are always looking for talented accountants to join our growing team."
    lead = one(compute_hot_leads([ad], RUN, NOW))
    assert lead["signal"] == "passive" and lead["score"] == 0 and lead["last_active_at"] is None
    assert lead["evergreen"] and "always looking" in lead["evergreen_reason"]
    # the same unchanged ad without the wording: still evergreen (60+ days, no closing date)
    plain = one(compute_hot_leads([L(1, 70, 0)], RUN, NOW))
    assert plain["signal"] == "passive" and plain["evergreen_reason"] == "same ad up unchanged for 70 days with no closing date"
    # reposting a talent-pool ad isn't demand either
    pool = [L(i, d, d - 5) for i, d in ((1, 60), (2, 35), (3, 6))]
    for ad in pool:
        ad.description = "Join our talent pool for future opportunities"
    assert one(compute_hot_leads(pool, RUN, NOW))["signal"] == "passive"


def test_actively_reposted_role_is_hot_with_its_last_active_date():
    """Posted 50, 25 and 5 days ago (new job URL each time): an active re-post in the last 30 days."""
    lead = one(compute_hot_leads([L(1, 50, 45), L(2, 25, 20), L(3, 5, 0)], RUN, NOW))
    assert lead["signal"] == "active" and not lead["evergreen"]
    assert lead["last_active_at"] == NOW - dt.timedelta(days=5)                       # "Re-advertised 30 Oct"
    assert lead["score"] == 4 + 2 + 1                                                 # +4 active, +2 one more re-post, live
    # re-posted, but the newest posting is 40 days old: no active signal in the last 30 days -> watch
    old = one(compute_hot_leads([L(1, 80, 75), L(2, 60, 55), L(3, 40, 0)], RUN, NOW))
    assert old["signal"] == "passive" and old["score"] == 0


def test_watch_list_leads_never_reach_client_orders_or_emails(store):
    """End to end on Postgres: the evergreen page is stored as 'passive' (watch list) and every hot-lead reader
    (leads page, order candidates, cold-email pool) skips it; the re-posted role is 'active'."""
    from app import orders as O
    from app import personalise as PZ
    from app.leadgen import candidates
    _seed(store)                                                        # Harlow: re-posted management accountant
    store.q("""insert into listings (job_id, posted_by, employer_name, employer_norm, title, title_norm, location_name,
               location_norm, url, first_seen_at, last_seen_at, description, raw) values
               (50, 'employer', 'Evergreen Ltd', 'evergreen', 'Management Accountant', 'management accountant', 'Leeds',
                'leeds', 'https://r/50', now() - interval '80 days', now(), 'We are always looking for accountants', '{}')""")
    store.q("insert into listing_searches values (50, 1)")
    rebuild_hot_leads(store)
    sig = {r["employer_norm"]: (r["signal"], r["evergreen"]) for r in store.q("select * from hot_leads")}
    assert sig == {"harlow group": ("active", False), "evergreen": ("passive", True)}
    assert [r["employer_norm"] for r in store.hot_leads()] == ["harlow group"]
    assert [r["employer_norm"] for r in store.hot_leads(signal="passive")] == ["evergreen"]
    for norm in ("harlow group", "evergreen"):
        store.q("insert into lead_contacts (employer_norm, verified_company) values (%s, true)", (norm,))
    assert {h["employer_norm"] for h in PZ.hot_leads(store, 1)} == {"harlow group"}
    order = {"search_id": 1, "client_id": None}
    assert {h["employer_norm"] for h in candidates(store, order, dt.datetime.now(dt.timezone.utc))} == {"harlow group"}
