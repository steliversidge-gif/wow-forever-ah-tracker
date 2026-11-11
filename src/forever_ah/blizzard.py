"""Thin client for the (undocumented) WoW: Forever Game Data API.

Verified against the live API on 2026-11-11:
  * namespaces are dynamic-forever-{region} and static-forever-{region}
  * there are no realms; auction houses hang off rulesets and the index URL
    needs its trailing slash
  * every ruleset returns the same list of houses
  * responses carry Last-Modified and honour If-Modified-Since (304)
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth.battle.net/token"
TIMEOUT = (10, 180)
WOWHEAD_ITEM_URL = "https://www.wowhead.com/forever/item={item_id}&xml"


def parse_wowhead_item(xml: str) -> dict | None:
    """Turn Wowhead's item XML into the fields we use, shaped like Blizzard's item JSON."""
    name = re.search(r"<name><!\[CDATA\[(.*?)\]\]></name>", xml, re.S)
    if "<error>" in xml or not name:
        return None

    def text(tag: str) -> str | None:
        m = re.search(rf"<{tag}\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", xml, re.S)
        return m.group(1) if m and m.group(1) else None

    def number(key: str) -> int | None:
        m = re.search(rf'"{key}":(\d+)', xml)
        return int(m.group(1)) if m else None

    level = text("level")
    return {
        "name": name.group(1),
        "quality": {"name": text("quality")},
        "item_class": {"name": text("class")},
        "item_subclass": {"name": text("subclass")},
        "level": int(level) if level and level.isdigit() else None,
        "required_level": number("reqlevel"),
        "sell_price": number("sellprice"),
    }


class BlizzardError(RuntimeError):
    pass


def http_date_to_utc(value: str) -> str:
    """'Wed, 11 Nov 2026 12:25:12 GMT' -> '2026-11-11 12:25:12' (UTC, SQLite friendly)."""
    return parsedate_to_datetime(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


class BlizzardClient:
    def __init__(self, client_id: str, client_secret: str, region: str = "eu", locale: str = "en_GB"):
        if not client_id or not client_secret:
            raise BlizzardError(
                "BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET are not set. "
                "Put them in .env (see .env.example)."
            )
        self._auth = (client_id, client_secret)
        self.region = region
        self.locale = locale
        self.base = f"https://{region}.api.blizzard.com"
        self._token: str | None = None
        self._token_expiry = 0.0
        self.session = requests.Session()
        retry = Retry(total=4, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"), respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))

    # -- auth -----------------------------------------------------------------
    def token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        r = self.session.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                              auth=self._auth, timeout=TIMEOUT)
        if r.status_code != 200:
            raise BlizzardError(f"OAuth token request failed with HTTP {r.status_code}")
        body = r.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 3600))
        return self._token

    # -- transport ------------------------------------------------------------
    def get(self, path: str, namespace: str, if_modified_since: str | None = None) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token()}"}
        if if_modified_since:
            headers["If-Modified-Since"] = if_modified_since
        params = {"namespace": namespace, "locale": self.locale}
        r = self.session.get(self.base + path, headers=headers, params=params, timeout=TIMEOUT)
        if r.status_code == 401:  # token revoked early; fetch a new one once
            self._token = None
            headers["Authorization"] = f"Bearer {self.token()}"
            r = self.session.get(self.base + path, headers=headers, params=params, timeout=TIMEOUT)
        return r

    @property
    def dynamic_ns(self) -> str:
        return f"dynamic-forever-{self.region}"

    # -- auction houses -------------------------------------------------------
    def house_index(self, ruleset: int) -> list[dict]:
        r = self.get(f"/data/wow/ruleset/{ruleset}/auctions/", self.dynamic_ns)
        if r.status_code != 200:
            raise BlizzardError(f"Auction house index returned HTTP {r.status_code}")
        return [{"id": h["id"], "name": h.get("name", "")} for h in r.json().get("auctions", [])]

    def house_auctions(self, ruleset: int, house_id: int, since: str | None = None):
        """Returns (http_last_modified, auctions) or None when unchanged since `since`."""
        r = self.get(f"/data/wow/ruleset/{ruleset}/auctions/{house_id}", self.dynamic_ns, since)
        if r.status_code == 304:
            return None
        if r.status_code != 200:
            raise BlizzardError(f"House {house_id} auctions returned HTTP {r.status_code}")
        return r.headers.get("Last-Modified", ""), (r.json().get("auctions") or [])

    def house_commodities(self, ruleset: int, house_id: int) -> list[dict]:
        r = self.get(f"/data/wow/ruleset/{ruleset}/auctions/{house_id}/commodities", self.dynamic_ns)
        if r.status_code != 200:
            raise BlizzardError(f"House {house_id} commodities returned HTTP {r.status_code}")
        return r.json().get("auctions") or []

    # -- items ----------------------------------------------------------------
    def item(self, item_id: int) -> tuple[dict | None, str | None]:
        """Item data plus the namespace it came from.

        About 43% of auctioned item ids 404 in static-forever (launch week), but
        nearly all of those are original-game items that Classic Era still serves.
        """
        for ns in (f"static-forever-{self.region}", f"static-classic1x-{self.region}",
                   f"static-{self.region}"):
            r = self.get(f"/data/wow/item/{item_id}", ns)
            if r.status_code == 200:
                return r.json(), ns
            if r.status_code != 404:
                raise BlizzardError(f"Item {item_id} returned HTTP {r.status_code} from {ns}")
        return self._wowhead_item(item_id)

    def _wowhead_item(self, item_id: int) -> tuple[dict | None, str | None]:
        """Last resort for items Blizzard's API does not serve yet (mostly new Forever items).

        Unofficial, so any failure just leaves the item unnamed. Uses a plain
        request, never the session's Blizzard token.
        """
        try:
            r = requests.get(WOWHEAD_ITEM_URL.format(item_id=item_id), timeout=20,
                             headers={"User-Agent": "forever-ah-tracker"})
            data = parse_wowhead_item(r.text) if r.status_code == 200 else None
        except requests.RequestException as exc:
            log.debug("Wowhead lookup for item %s failed: %s", item_id, exc)
            return None, None
        return (data, "wowhead-forever") if data else (None, None)
