#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client for the Kaspi.kz Shop API (v2).

Behaviour of the live API, established by probing it against a real merchant
account rather than by reading the docs:

  * ``page[size]`` is capped at 100.
  * A ``creationDate`` filter spanning more than ~14 days returns an error,
    so any longer period has to be walked in windows.
  * Order entries already carry the SKU and product name under
    ``attributes.offer.{code,name}`` — the extra request to ``/products``
    that the docs imply is unnecessary.
  * All timestamps are Unix milliseconds. Kazakhstan is UTC+5 and does not
    observe DST, so a fixed offset is correct.

The client retries on 429 and 5xx with exponential backoff and never retries
a 4xx that is not 429 — those are caller errors and retrying only burns quota.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

BASE_URL = "https://kaspi.kz/shop/api/v2"

#: Kazakhstan time. Fixed offset — the country abolished DST.
KZ = timezone(timedelta(hours=5))

#: The API rejects a creationDate range wider than roughly 14 days.
#: 13 leaves a margin for boundary rounding.
WINDOW_DAYS = 13

#: Hard cap enforced by the API.
MAX_PAGE_SIZE = 100

RETRYABLE = (429, 500, 502, 503, 504)


class KaspiApiError(RuntimeError):
    """A non-retryable API failure."""


def to_ms(dt: datetime) -> int:
    """Datetime -> Unix milliseconds, as the API expects."""
    return int(dt.timestamp() * 1000)


def from_ms(value: int) -> datetime:
    """Unix milliseconds -> aware datetime in Kazakhstan time."""
    return datetime.fromtimestamp(value / 1000, tz=KZ)


def windows(start: datetime, end: datetime, days: int = WINDOW_DAYS
            ) -> Iterator[tuple[datetime, datetime]]:
    """
    Split [start, end] into half-open windows no wider than ``days``.

    The API's range limit makes this mandatory for any period longer than
    two weeks. Windows are inclusive of both ends, so each one stops 1 ms
    before the next begins — no order is fetched twice, none is skipped.
    """
    cursor = start
    step = timedelta(days=days)
    while cursor < end:
        stop = min(cursor + step - timedelta(milliseconds=1), end)
        yield cursor, stop
        cursor = cursor + step


class KaspiClient:
    """
    Thin, dependency-free client over the Kaspi Shop API.

    Uses only the standard library on purpose: this tends to run on a
    merchant's laptop or a small VPS, where "pip install failed" is a real
    support burden.
    """

    def __init__(self, token: str, *, pause: float = 0.25,
                 max_retry: int = 5, timeout: int = 90) -> None:
        if not token:
            raise ValueError("Kaspi API token is required")
        self.token = token
        self.pause = pause
        self.max_retry = max_retry
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """GET a path, retrying transient failures with exponential backoff."""
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        for attempt in range(1, self.max_retry + 1):
            request = urllib.request.Request(url, headers={
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
                "X-Auth-Token": self.token,
                "User-Agent": "kaspi-shop-analytics/1.0",
            })
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))

            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:300]
                # 4xx other than 429 means the request itself is wrong.
                # Retrying it wastes quota and hides the real problem.
                if exc.code not in RETRYABLE or attempt == self.max_retry:
                    raise KaspiApiError(f"HTTP {exc.code} on {url}: {body}") from exc
                wait = 2 ** attempt
                print(f"  HTTP {exc.code} -> retry in {wait}s ({body[:120]})", flush=True)
                time.sleep(wait)

            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == self.max_retry:
                    raise KaspiApiError(f"network unreachable: {exc}") from exc
                wait = 2 ** attempt
                print(f"  network: {exc} -> retry in {wait}s", flush=True)
                time.sleep(wait)

        raise KaspiApiError(f"request failed: {url}")

    # -- resources ---------------------------------------------------------

    def orders(self, start: datetime, end: datetime) -> Iterator[dict]:
        """
        Yield every order created in [start, end], walking date windows and
        pages transparently.
        """
        for window_start, window_end in windows(start, end):
            page = 0
            page_count = None
            while True:
                body = self.get("/orders", {
                    "page[number]": page,
                    "page[size]": MAX_PAGE_SIZE,
                    "filter[orders][creationDate][$ge]": to_ms(window_start),
                    "filter[orders][creationDate][$le]": to_ms(window_end),
                })
                meta = body.get("meta") or {}
                if page_count is None:
                    page_count = meta.get("pageCount") or 0
                    print(
                        f"window {window_start:%d.%m} .. {window_end:%d.%m}"
                        f" -> orders: {meta.get('totalCount', 0)}",
                        flush=True,
                    )

                rows = body.get("data") or []
                yield from rows

                page += 1
                if page >= page_count or not rows:
                    break
                time.sleep(self.pause)
            time.sleep(self.pause)

    def order_entries(self, order_id: str) -> list[dict]:
        """Line items of one order. SKU and name come embedded in the entry."""
        body = self.get(f"/orders/{order_id}/entries")
        return body.get("data") or []
