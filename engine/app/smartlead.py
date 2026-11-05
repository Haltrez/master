"""Smartlead API client (https://api.smartlead.ai, confirmed against the docs on 2026-11-05).

Base https://server.smartlead.ai/api/v1, auth = api_key query parameter. Rate limit 60 requests/minute
per key (all endpoints together), so every call waits >= 1.05 s after the previous one, and a 429 is
retried after a pause.

The API key is a query parameter, so it would appear in URLs: it is never logged and is scrubbed from
every error message we raise (scrub()).

Endpoints used:
  GET    /campaigns/                                   list campaigns (plain array)
  POST   /campaigns/{id}/status                        {"status": "PAUSED" | "START"}
  GET    /campaigns/{id}/analytics-by-date             start_date, end_date (YYYY-MM-DD, <= 30 days), time_zone
  GET    /campaigns/{id}/email-accounts                inboxes attached to a campaign
  GET    /campaigns/{id}/sequences                     the emails (subject/body with {{variables}})
  GET    /campaigns/{id}/statistics                    per-email rows (sent_time, is_bounced...) for bounce checks
  POST   /campaigns/{id}/leads                         {"lead_list": [...], "settings": {...}} (max 400)
  POST   /campaigns/{id}/leads/{lead_id}/category      {"category_id": n, "pause_lead": bool}
  GET    /leads/?email=                                lead by email (empty object when not found)
  GET    /leads/{lead_id}/campaign-overview            every campaign the lead is in
  POST   /leads/{lead_id}/unsubscribe                  unsubscribe from all current and future campaigns
  POST   /leads/add-domain-block-list                  {"domain_block_list": [emails or domains]}
  GET    /leads/fetch-categories                       lead categories (Interested=1 ...)
  POST   /master-inbox/inbox-replies                   replies across campaigns (limit <= 20 per page)
  GET    /email-accounts/                              inboxes with daily limits, sent today, warmup
  GET    /analytics/mailbox/name-wise-health-metrics   per-inbox sent/bounced in a date range
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("smartlead")

BASE_URL = "https://server.smartlead.ai/api/v1"
MIN_INTERVAL = 1.05
APP_URL = "https://app.smartlead.ai/app"

# Global lead categories (GET /leads/fetch-categories): our CRM category -> Smartlead category id
CATEGORY_IDS = {"interested": 1, "not_interested": 3, "unsubscribe": 4, "out_of_office": 6, "wrong_person": 7}


class SmartleadError(Exception):
    pass


class Smartlead:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, clock=time.monotonic, min_interval: float = MIN_INTERVAL):
        if not api_key:
            raise SmartleadError("SMARTLEAD_API_KEY is not set on the server")
        self._key = api_key
        self._base = base_url.rstrip("/")
        # httpx logs every request URL at INFO, and Smartlead's key is in the URL: never let that through.
        for name in ("httpx", "httpcore"):
            lg = logging.getLogger(name)
            if lg.level == logging.NOTSET or lg.level < logging.WARNING:
                lg.setLevel(logging.WARNING)
        self._http = httpx.Client(transport=transport, timeout=40.0)
        self._sleep, self._clock, self._interval = sleep, clock, min_interval
        self._last: float | None = None

    def scrub(self, text: Any) -> str:
        return str(text).replace(self._key, "***")

    def _req(self, method: str, path: str, params: dict | None = None, json: Any = None, tries: int = 3) -> Any:
        for attempt in range(1, tries + 1):
            if self._last is not None:
                wait = self._last + self._interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            try:
                r = self._http.request(method, self._base + path, params={**(params or {}), "api_key": self._key}, json=json)
            except httpx.HTTPError as exc:
                if attempt == tries:
                    raise SmartleadError(f"couldn't reach Smartlead ({type(exc).__name__})") from None
                self._sleep(2 * attempt)
                continue
            log.info("smartlead %s %s status=%d", method, path, r.status_code)   # path only: never the key
            if r.status_code == 429 and attempt < tries:
                self._sleep(30)
                continue
            if r.status_code == 401:
                raise SmartleadError("Smartlead refused the API key (401): check SMARTLEAD_API_KEY")
            if r.is_error:
                raise SmartleadError(f"Smartlead {method} {path} failed ({r.status_code}): {self.scrub(r.text[:300])}")
            if not r.content:
                return {}
            try:
                return r.json()
            except ValueError:
                return {"raw": self.scrub(r.text[:300])}
        raise SmartleadError("unreachable")  # pragma: no cover

    # -- campaigns ----------------------------------------------------------------------------------
    def campaigns(self) -> list[dict]:
        d = self._req("GET", "/campaigns/")
        return d if isinstance(d, list) else (d.get("campaigns") or d.get("data") or [])

    def set_status(self, campaign_id: int, status: str) -> Any:
        if status not in ("PAUSED", "START"):
            raise ValueError("status must be PAUSED or START")
        return self._req("POST", f"/campaigns/{campaign_id}/status", json={"status": status})

    def analytics_by_date(self, campaign_id: int, start: str, end: str, tz: str = "Europe/London") -> dict:
        return self._req("GET", f"/campaigns/{campaign_id}/analytics-by-date",
                         params={"start_date": start, "end_date": end, "time_zone": tz}) or {}

    def campaign_email_accounts(self, campaign_id: int) -> list[dict]:
        d = self._req("GET", f"/campaigns/{campaign_id}/email-accounts")
        return d if isinstance(d, list) else (d.get("data") or [])

    def sequences(self, campaign_id: int) -> list[dict]:
        d = self._req("GET", f"/campaigns/{campaign_id}/sequences")
        return d if isinstance(d, list) else (d.get("data") or d.get("sequences") or [])

    def statistics(self, campaign_id: int, *, limit: int = 1000, offset: int = 0, since: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"limit": limit, "offset": offset}
        if since:
            p["sent_time_start_date"] = since
        d = self._req("GET", f"/campaigns/{campaign_id}/statistics", params=p) or {}
        rows = d.get("data") if isinstance(d, dict) else d
        return rows if isinstance(rows, list) else []

    # -- leads ----------------------------------------------------------------------------------------
    def add_lead(self, campaign_id: int, lead: dict, *, allow_other_campaigns: bool = False) -> dict:
        body = {"lead_list": [lead], "settings": {
            "ignore_global_block_list": False, "ignore_unsubscribe_list": False,
            "ignore_duplicate_leads_in_other_campaign": allow_other_campaigns, "return_lead_ids": True}}
        return self._req("POST", f"/campaigns/{campaign_id}/leads", json=body) or {}

    def lead_by_email(self, email: str) -> dict:
        d = self._req("GET", "/leads/", params={"email": email})
        return d if isinstance(d, dict) else {}

    def lead_campaigns(self, lead_id: int) -> list[dict]:
        d = self._req("GET", f"/leads/{lead_id}/campaign-overview") or {}
        return ((d.get("data") or {}).get("email_campaign_leads_mappings") or []) if isinstance(d, dict) else []

    def unsubscribe_everywhere(self, lead_id: int) -> Any:
        return self._req("POST", f"/leads/{lead_id}/unsubscribe")

    def block(self, emails_or_domains: list[str]) -> Any:
        return self._req("POST", "/leads/add-domain-block-list", json={"domain_block_list": emails_or_domains})

    def set_category(self, campaign_id: int, lead_id: int, category_id: int | None, pause: bool = False) -> Any:
        return self._req("POST", f"/campaigns/{campaign_id}/leads/{lead_id}/category",
                         json={"category_id": category_id, "pause_lead": pause})

    def categories(self) -> list[dict]:
        d = self._req("GET", "/leads/fetch-categories")
        return d if isinstance(d, list) else (d.get("data") or [])

    # -- inbox, accounts, health -------------------------------------------------------------------------
    def inbox_replies(self, *, offset: int = 0, limit: int = 20, campaign_ids: list[int] | None = None,
                      since_iso: str | None = None, until_iso: str | None = None) -> dict:
        filters: dict[str, Any] = {}
        if campaign_ids:
            filters["campaignId"] = campaign_ids[:5]
        if since_iso and until_iso:
            filters["replyTimeBetween"] = [since_iso, until_iso]
        return self._req("POST", "/master-inbox/inbox-replies", params={"fetch_message_history": "false"},
                         json={"offset": offset, "limit": min(20, limit), "filters": filters,
                               "sortBy": "REPLY_TIME_DESC"}) or {}

    def email_accounts(self) -> list[dict]:
        out, offset = [], 0
        while offset < 1000:
            d = self._req("GET", "/email-accounts/", params={"offset": offset, "limit": 100})
            rows = d if isinstance(d, list) else (d.get("data") or [])
            out += rows
            if len(rows) < 100:
                break
            offset += 100
        return out

    def mailbox_health(self, start: str, end: str, campaign_ids: list[int] | None = None) -> list[dict]:
        p = {"start_date": start, "end_date": end, "timezone": "Europe/London", "full_data": "true"}
        if campaign_ids:
            p["campaign_ids"] = ",".join(str(c) for c in campaign_ids)
        d = self._req("GET", "/analytics/mailbox/name-wise-health-metrics", params=p) or {}
        return ((d.get("data") or {}).get("email_health_metrics") or []) if isinstance(d, dict) else []

    def test(self) -> dict:
        """Harmless read: the first page of inboxes."""
        d = self._req("GET", "/email-accounts/", params={"offset": 0, "limit": 1})
        return {"ok": True, "inboxes_visible": len(d) if isinstance(d, list) else None}
