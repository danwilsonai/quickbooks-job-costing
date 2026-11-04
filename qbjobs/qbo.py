"""The QuickBooks Online connection: sign-in (OAuth 2.0), token refresh, reads, and two kinds of writes.

This tool may CREATE invoices and customers in QuickBooks, and nothing else. It never edits, voids or deletes
anything, and never touches journal entries, bills, payments, payroll or accounts. That limit is enforced
here (ALLOWED_WRITES), not just promised in the docs.
"""
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import requests

from . import config

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_BASE = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "75"            # Intuit's floor since 2025-08-01
# The only QuickBooks records this tool may write, and only by creating new ones.
# QuickBooks is the owner's books: an edit, void, delete, journal entry, bill or
# payment written by mistake changes their real financial statements and taxes.
# Don't widen this without the owner's bookkeeper agreeing (see CLAUDE.md rule 1).
ALLOWED_WRITES = {"invoice", "customer"}


class QBOError(Exception):
    pass


class WriteNotAllowed(QBOError):
    pass


class NotConnected(QBOError):
    pass


# ---- sign-in ----------------------------------------------------------------------------------------

def _need_keys():
    if not config.CLIENT_ID or not config.CLIENT_SECRET:
        raise NotConnected("QBO_CLIENT_ID and QBO_CLIENT_SECRET aren't set. Copy .env.example to .env and fill "
                           "them in from your app's Keys page at developer.intuit.com.")


def auth_url(state: str) -> str:
    _need_keys()
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": config.CLIENT_ID, "response_type": "code", "scope": SCOPE,
        "redirect_uri": config.REDIRECT_URI, "state": state,
    })


def new_state() -> str:
    return secrets.token_urlsafe(24)


def _token_call(data: dict) -> dict:
    _need_keys()
    r = requests.post(TOKEN_URL, data=data, auth=(config.CLIENT_ID, config.CLIENT_SECRET),
                      headers={"Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise QBOError(f"Intuit refused the token request ({r.status_code}): {r.text[:300]}")
    return r.json()


def _save_tokens(tok: dict, realm_id: str, path: Path) -> dict:
    data = {
        "env": config.QBO_ENV,
        "realm_id": realm_id,
        "access_token": tok["access_token"],
        "refresh_token": tok["refresh_token"],
        "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 120,
    }
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)      # this file is a key to your books: only you can read it
    return data


def exchange_code(code: str, realm_id: str, path: Path | None = None) -> dict:
    tok = _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": config.REDIRECT_URI})
    return _save_tokens(tok, realm_id, path or config.TOKENS_PATH)


def disconnect(path: Path | None = None) -> None:
    path = path or config.TOKENS_PATH
    if path.exists():
        path.unlink()


# ---- the client -------------------------------------------------------------------------------------

class QBO:
    def __init__(self, tokens_path: Path | None = None, session: requests.Session | None = None):
        self.path = tokens_path or config.TOKENS_PATH
        if not self.path.exists():
            raise NotConnected("Not connected to QuickBooks yet. Run: python -m qbjobs connect")
        self.tokens = json.loads(self.path.read_text())
        if self.tokens.get("env") != config.QBO_ENV:
            raise NotConnected(f"You're signed in to a {self.tokens.get('env')} company but QBO_ENV is "
                               f"{config.QBO_ENV}. Run python -m qbjobs connect again after switching.")
        self.env = config.QBO_ENV
        self.realm_id = self.tokens["realm_id"]
        self.http = session or requests.Session()

    def _refresh(self) -> None:
        tok = _token_call({"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"]})
        self.tokens = _save_tokens(tok, self.realm_id, self.path)   # Intuit rotates the refresh token

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        if time.time() > self.tokens["expires_at"]:
            self._refresh()
        url = f"{API_BASE[self.env]}/v3/company/{self.realm_id}/{path}"
        params = {**(params or {}), "minorversion": MINOR_VERSION}
        for attempt in (1, 2):
            r = self.http.request(method, url, params=params, json=body, timeout=60, headers={
                "Authorization": f"Bearer {self.tokens['access_token']}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            })
            if r.status_code == 401 and attempt == 1:
                self._refresh()
                continue
            break
        if r.status_code >= 400:
            tid = r.headers.get("intuit_tid", "")
            try:
                errs = r.json().get("Fault", {}).get("Error", [])
                msg = "; ".join(f"{e.get('Message')}: {e.get('Detail')}" for e in errs) or r.text[:300]
            except ValueError:
                msg = r.text[:300]
            raise QBOError(f"QuickBooks said {r.status_code}: {msg}" + (f" (intuit_tid {tid})" if tid else ""))
        return r.json()

    # reads
    def query(self, sql: str) -> list[dict]:
        """Run a QuickBooks query (select * from Customer ...), following pages of 1000."""
        out, start = [], 1
        while True:
            res = self._request("GET", "query", {"query": f"{sql} STARTPOSITION {start} MAXRESULTS 1000"})
            rows = next((v for k, v in res.get("QueryResponse", {}).items() if isinstance(v, list)), [])
            out.extend(rows)
            if len(rows) < 1000:
                return out
            start += 1000

    def read(self, entity: str, entity_id: str) -> dict:
        res = self._request("GET", f"{entity.lower()}/{entity_id}")
        return next(v for k, v in res.items() if k.lower() == entity.lower())

    def company_name(self) -> str:
        return self.read("companyinfo", self.realm_id).get("CompanyName", "?")

    # writes: create only, and only what ALLOWED_WRITES names
    def create(self, entity: str, body: dict, request_id: str) -> dict:
        if entity.lower() not in ALLOWED_WRITES:
            raise WriteNotAllowed(f"This tool doesn't create {entity} records in QuickBooks. "
                                  f"Allowed: {', '.join(sorted(ALLOWED_WRITES))}.")
        # requestid makes a retry safe: QuickBooks returns the first result instead of a duplicate
        res = self._request("POST", entity.lower(), {"requestid": request_id}, body)
        return next(v for k, v in res.items() if k.lower() == entity.lower())


def quote(value: str) -> str:
    """Escape a value for a QuickBooks query string."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
