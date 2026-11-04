"""Google Sheets for lead orders, through the owner's own Google account.

One-time "Connect Google" in the CRM (OAuth, scope drive.file: the engine can
only see sheets it created itself). The refresh token lives in app_settings
under "google_oauth" and is never returned by the API.

REST only (httpx): create a spreadsheet with a "Hot leads" tab, append rows,
share it with the client agency.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

log = logging.getLogger("google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
SCOPES = "openid email https://www.googleapis.com/auth/drive.file"
SETTING = "google_oauth"
TAB = "Hot leads"
STATUSES = ["new", "contacted", "meeting", "placement", "not relevant"]


class GoogleError(Exception):
    pass


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state})


def _email_from_id_token(id_token: str | None) -> str | None:
    # Straight from Google's token endpoint over TLS, so reading the payload is enough here.
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


def exchange_code(store, client_id: str, client_secret: str, code: str, redirect_uri: str,
                  transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    with httpx.Client(transport=transport, timeout=30) as http:
        r = http.post(TOKEN_URL, data={"code": code, "client_id": client_id, "client_secret": client_secret,
                                       "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if r.is_error:
        raise GoogleError(f"Google refused the sign-in ({r.status_code}): {r.text[:200]}")
    body = r.json()
    if not body.get("refresh_token"):
        raise GoogleError("Google sent no refresh token: remove the app at myaccount.google.com/permissions and connect again")
    email = _email_from_id_token(body.get("id_token"))
    store.set_setting(SETTING, {"refresh_token": body["refresh_token"], "email": email, "connected_at": time.time()})
    return {"connected": True, "email": email}


def status(store) -> dict[str, Any]:
    v = store.setting(SETTING) or {}
    return {"connected": bool(v.get("refresh_token")), "email": v.get("email")}


def disconnect(store) -> None:
    store.q("delete from app_settings where key = %s", (SETTING,))


class Sheets:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 transport: httpx.BaseTransport | None = None):
        self._cid, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._http = httpx.Client(transport=transport, timeout=60)
        self._token: str | None = None
        self._expires = 0.0

    @classmethod
    def from_store(cls, store, settings, transport=None) -> "Sheets | None":
        v = store.setting(SETTING) or {}
        if not (settings.google_client_id and settings.google_client_secret and v.get("refresh_token")):
            return None
        return cls(settings.google_client_id, settings.google_client_secret, v["refresh_token"], transport)

    def _headers(self) -> dict[str, str]:
        if not self._token or time.time() > self._expires - 60:
            r = self._http.post(TOKEN_URL, data={"client_id": self._cid, "client_secret": self._secret,
                                                 "refresh_token": self._refresh, "grant_type": "refresh_token"})
            if r.is_error:
                raise GoogleError("Google access expired: press 'Connect Google' in Settings again")
            body = r.json()
            self._token, self._expires = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method: str, url: str, **kw) -> dict:
        r = self._http.request(method, url, headers=self._headers(), **kw)
        if r.is_error:
            raise GoogleError(f"Google {method} {url.split('?')[0].rsplit('/', 2)[-1]} failed {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def create(self, title: str, headers: list[str]) -> dict[str, str]:
        body = self._call("POST", SHEETS, json={
            "properties": {"title": title},
            "sheets": [{"properties": {"title": TAB, "gridProperties": {"frozenRowCount": 1}}}]})
        sid, gid = body["spreadsheetId"], body["sheets"][0]["properties"]["sheetId"]
        status_col = len(headers) - 1
        self._call("POST", f"{SHEETS}/{sid}:batchUpdate", json={"requests": [
            {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold"}},
            {"setDataValidation": {"range": {"sheetId": gid, "startRowIndex": 1, "startColumnIndex": status_col,
                                             "endColumnIndex": status_col + 1},
                                   "rule": {"condition": {"type": "ONE_OF_LIST",
                                                          "values": [{"userEnteredValue": s} for s in STATUSES]},
                                            "showCustomUi": True, "strict": False}}},
        ]})
        self.append(sid, [headers])
        return {"sheet_id": sid, "sheet_url": body.get("spreadsheetUrl") or f"https://docs.google.com/spreadsheets/d/{sid}/edit"}

    def append(self, sheet_id: str, rows: list[list[Any]]) -> None:
        if not rows:
            return
        # RAW: a job title starting with "=" stays text, never a formula.
        self._call("POST", f"{SHEETS}/{sheet_id}/values/{TAB}!A1:append",
                   params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                   json={"values": [["" if v is None else v for v in r] for r in rows]})

    def share(self, sheet_id: str, email: str) -> None:
        self._call("POST", f"{DRIVE}/{sheet_id}/permissions", params={"sendNotificationEmail": "true"},
                   json={"type": "user", "role": "writer", "emailAddress": email})
