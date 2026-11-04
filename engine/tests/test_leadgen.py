"""The lead pipeline's checks, each on its own (fakes for Haiku, Companies House and the geocoder)."""

import datetime as dt
import json
from types import SimpleNamespace

from app import orders as O
from app.config import Settings
from app.geo import Place
from app.leadgen import Tools, money, place, pick
from tests.test_orders import FakeHaiku, add_leads, make


def test_money_and_place_formatting():
    assert money(27000, 55000) == "£27,000 - £55,000"
    assert money(12.5, 13) == "£12.50 - £13/hr"
    assert money(None, 450) == "£450/day"
    assert money(12000, 12000) == ""          # pro-rata / estimate: left out
    assert place("OL10 1PN") == "Oldham" and place("Manchester") == "Manchester"


class Geo:
    PLACES = {"Manchester": Place(53.48, -2.24, "Manchester", None), "Leeds": Place(53.80, -1.55, "Leeds", None),
              "Salford": Place(53.49, -2.29, "Salford", None)}

    def geocode(self, text):
        return self.PLACES.get(text)


def test_ads_outside_the_radius_never_reach_haiku(store):
    o = make(store, industry="Sales & Marketing", location="Manchester", leads_wanted=10)
    label = "Sales & Marketing - Manchester"
    add_leads(store, 1, label=label, title="Sales Executive", location="Salford")
    add_leads(store, 1, label=label, title="Sales Executive", location="Leeds", start=5)
    haiku = FakeHaiku()
    rows, stats = pick(store, O.get_order(store, o["id"]), Tools(claude=haiku, geocoder=Geo()), 10, strict=True)
    assert [r["Location"] for r in rows] == ["Salford"] and stats["outside_area"] == 1
    assert haiku.calls[0]["messages"][0]["content"].count("<ad id=") == 1


def test_one_agency_ad_rules_out_the_whole_company(store):
    o = make(store, industry="Sales & Marketing", leads_wanted=10)
    label = "Sales & Marketing - Leeds"
    add_leads(store, 1, label=label, title="Sales Executive")
    # same company, second role, advertised "for our client"
    store.q("update lead_contacts set employer_norm = employer_norm")
    add_leads(store, 1, label=label, title="Field Sales Executive", start=1, description="Our client is hiring...")
    store.q("update hot_leads set employer_name = 'Company 0', employer_norm = 'company 0' where lead_key = 'k1'")
    rows, _ = pick(store, O.get_order(store, o["id"]), Tools(claude=FakeHaiku()), 10)
    assert rows == []


class FakeCH:
    def __init__(self):
        self.items = [{"company_number": "111", "title": "ACME HOLDINGS DORMANT LTD", "company_status": "dissolved"},
                      {"company_number": "222", "title": "ACME TRADING UK LIMITED", "company_status": "active",
                       "company_type": "ltd"}]

    def search(self, q):
        return self.items

    def profile(self, number):
        return {"company_status": "active", "type": "ltd", "sic_codes": ["62020"]} if number == "222" else {}

    def officers(self, number):
        return [{"name": f"SMITH, John {i}", "officer_role": "director"} for i in range(6)]


class PickingHaiku(FakeHaiku):
    def _create(self, **kw):
        if "Companies House search results" in kw["messages"][0]["content"]:
            self.calls.append(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
                type="text", text=json.dumps({"company_number": "222", "why": "main trading company"}))])
        return super()._create(**kw)


def test_haiku_picks_the_registered_company_for_a_brand_name_and_big_boards_get_no_director(store):
    o = make(store, industry="Sales & Marketing", leads_wanted=5)
    add_leads(store, 1, label="Sales & Marketing - Leeds", title="Sales Executive")
    store.q("update hot_leads set employer_name = 'Acme', employer_norm = 'acme'")
    store.q("update lead_contacts set employer_norm = 'acme', ch_company_type = 'needs_review', verified_company = false")
    rows, stats = pick(store, O.get_order(store, o["id"]), Tools(claude=PickingHaiku(), ch=FakeCH()), 5)
    assert len(rows) == 1 and "active company on Companies House" in rows[0]["Why it's a lead"]
    c = store.lead_contacts_for(["acme"])["acme"]
    assert c["ch_company_number"] == "222" and c["verified_company"]
    assert rows[0]["Decision maker"] == ""      # 6 directors: a board member is no use, left for Hunter


def test_stale_and_single_postings_are_not_candidates(store):
    o = make(store, industry="Sales & Marketing", leads_wanted=5)
    add_leads(store, 2, label="Sales & Marketing - Leeds", title="Sales Executive")
    store.q("update hot_leads set last_seen_at = now() - interval '20 days' where lead_key = 'k0'")
    store.q("update hot_leads set posting_count_90d = 1 where lead_key = 'k1'")
    rows, stats = pick(store, O.get_order(store, o["id"]), Tools(claude=FakeHaiku()), 5)
    assert rows == [] and stats["candidates"] == 0
