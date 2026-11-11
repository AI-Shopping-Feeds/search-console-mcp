# Copyright 2026 AI Shopping Feeds
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""URL Inspection tools: index status, canonicals and rich results."""

import asyncio
import collections
import datetime
import time
from typing import Any, Callable

from mcp.server.mcpserver import Context

from search_console_mcp.client import (
    URL_INSPECTION,
    QuotaExceededError,
    SearchConsoleAPIError,
    get_client,
)
from search_console_mcp.tools import utils

# Google's URL Inspection quota, per property.
PER_MINUTE_LIMIT = 600
PER_DAY_LIMIT = 2000
MAX_BATCH_URLS = 200


def _pacific_today() -> datetime.date:
    # The daily quota resets at midnight Pacific time.
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # No tz database (e.g. Windows without tzdata).
        tz = datetime.timezone(datetime.timedelta(hours=-8))
    return datetime.datetime.now(tz).date()


class InspectionQuota:
    """Client-side pacing for the URL Inspection quota, per property.

    Counts only calls made by this server process; other tools using the same
    property share Google's quota without showing up here.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], datetime.date] = _pacific_today,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self._clock = clock
        self._today = today
        self._sleep = sleep
        self._recent: dict[str, collections.deque] = {}
        self._daily: dict[tuple[str, datetime.date], int] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop = None

    def _get_lock(self) -> asyncio.Lock:
        # An asyncio.Lock belongs to one event loop; make one per loop.
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    def used_today(self, site: str) -> int:
        return self._daily.get((site, self._today()), 0)

    async def acquire(
        self, site: str, per_minute: int = PER_MINUTE_LIMIT
    ) -> bool:
        """Waits for a per-minute slot. Returns False once today's quota is
        used up."""
        async with self._get_lock():
            day_key = (site, self._today())
            if self._daily.get(day_key, 0) >= PER_DAY_LIMIT:
                return False
            recent = self._recent.setdefault(site, collections.deque())
            while True:
                now = self._clock()
                while recent and now - recent[0] >= 60:
                    recent.popleft()
                if len(recent) < per_minute:
                    break
                await self._sleep(60 - (now - recent[0]) + 0.01)
            recent.append(now)
            self._daily[day_key] = self._daily.get(day_key, 0) + 1
            return True


_quota = InspectionQuota()


def summarise(url: str, result: dict[str, Any]) -> dict[str, Any]:
    """Flattens an inspection result into the fields people act on."""
    index = result.get("indexStatusResult", {})
    google_canonical = index.get("googleCanonical")
    user_canonical = index.get("userCanonical")
    summary: dict[str, Any] = {
        "url": url,
        "verdict": index.get("verdict"),
        "coverage_state": index.get("coverageState"),
        "indexing_state": index.get("indexingState"),
        "robots_txt_state": index.get("robotsTxtState"),
        "page_fetch_state": index.get("pageFetchState"),
        "last_crawl_time": index.get("lastCrawlTime"),
        "crawled_as": index.get("crawledAs"),
        "google_canonical": google_canonical,
        "user_canonical": user_canonical,
        "canonical_mismatch": bool(
            google_canonical
            and user_canonical
            and google_canonical != user_canonical
        ),
        "sitemaps": index.get("sitemap", []),
        "referring_urls": index.get("referringUrls", []),
        "inspection_result_link": result.get("inspectionResultLink"),
    }
    mobile = result.get("mobileUsabilityResult")
    if mobile:
        summary["mobile_usability"] = {
            "verdict": mobile.get("verdict"),
            "issues": [
                i.get("message") or i.get("issueType")
                for i in mobile.get("issues", [])
            ],
        }
    rich = result.get("richResultsResult")
    if rich:
        summary["rich_results"] = {
            "verdict": rich.get("verdict"),
            "types": [
                {
                    "type": item.get("richResultType"),
                    "items": len(item.get("items", [])),
                    "issues": [
                        {
                            "severity": issue.get("severity"),
                            "message": issue.get("issueMessage"),
                        }
                        for entry in item.get("items", [])
                        for issue in entry.get("issues", [])
                    ],
                }
                for item in rich.get("detectedItems", [])
            ],
        }
    amp = result.get("ampResult")
    if amp:
        summary["amp"] = {
            "verdict": amp.get("verdict"),
            "url": amp.get("url"),
            "indexing_state": amp.get("indexingState"),
        }
    return summary


async def _inspect(site: str, url: str, language_code: str) -> dict[str, Any]:
    data = await get_client().request(
        "POST",
        URL_INSPECTION,
        json={
            "inspectionUrl": url,
            "siteUrl": site,
            "languageCode": language_code,
        },
    )
    return data.get("inspectionResult", {})


async def inspect_url(
    site_url: str,
    url: str,
    language_code: str = "en-US",
    include_raw: bool = False,
) -> dict[str, Any]:
    """Inspects a URL in Google's index, as the URL Inspection tool does.

    Shows whether the URL is indexed and why not (coverage state), robots.txt
    and fetch status, last crawl, the canonical Google chose versus the one
    the page declares, sitemaps and referring URLs, and rich result and
    mobile usability findings. It reports Google's indexed copy, not a live
    test.

    Quota: 2,000 inspections a day and 600 a minute per property.

    Args:
        site_url: The property, exactly as list_sites returns it. The URL must
          belong to it.
        url: Full URL to inspect. A path such as '/products/x' works for
          URL-prefix properties.
        language_code: Language for issue messages, e.g. 'en-GB'. Default
          'en-US'.
        include_raw: Also return Google's full response.
    """
    site = utils.site_url(site_url)
    full_url = utils.page_url(site, url)
    if not await _quota.acquire(site):
        raise RuntimeError(
            f"This server has used the daily URL Inspection quota"
            f" ({PER_DAY_LIMIT}) for {site}. It resets at midnight Pacific."
        )
    result = await _inspect(site, full_url, language_code)
    summary = summarise(full_url, result)
    if include_raw:
        summary["raw"] = result
    return summary


async def batch_inspect_urls(
    site_url: str,
    urls: list[str],
    language_code: str = "en-US",
    max_per_minute: int = PER_MINUTE_LIMIT,
    concurrency: int = 4,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Inspects up to 200 URLs, paced to Google's URL Inspection quota.

    Runs a few inspections at a time, never more than max_per_minute per
    minute or 2,000 per property per day (counted by this server). Stops
    cleanly if Google reports the quota is used up; URLs not inspected are
    listed under 'skipped'. Returns a short summary (index verdicts, coverage
    states, canonical mismatches) and one result per URL as inspect_url gives.

    Args:
        site_url: The property, exactly as list_sites returns it.
        urls: Full URLs (paths work for URL-prefix properties). Duplicates are
          inspected once.
        language_code: Language for issue messages. Default 'en-US'.
        max_per_minute: Pacing limit, 1 to 600. Default 600.
        concurrency: Inspections in flight at once, 1 to 10. Default 4.
    """
    site = utils.site_url(site_url)
    if not urls:
        raise ValueError("urls is empty.")
    if not 1 <= max_per_minute <= PER_MINUTE_LIMIT:
        raise ValueError(f"max_per_minute must be 1 to {PER_MINUTE_LIMIT}.")
    if not 1 <= concurrency <= 10:
        raise ValueError("concurrency must be 1 to 10.")
    unique = list(dict.fromkeys(utils.page_url(site, u) for u in urls))
    if len(unique) > MAX_BATCH_URLS:
        raise ValueError(
            f"{len(unique)} URLs given; inspect at most {MAX_BATCH_URLS} per"
            " call and call again for the rest."
        )

    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    stop = asyncio.Event()
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def work(url: str) -> None:
        nonlocal done
        async with semaphore:
            if stop.is_set():
                skipped[url] = "quota used up"
            elif not await _quota.acquire(site, max_per_minute):
                stop.set()
                skipped[url] = "daily quota used up (counted by this server)"
            else:
                try:
                    results[url] = summarise(
                        url, await _inspect(site, url, language_code)
                    )
                except QuotaExceededError as exc:
                    stop.set()
                    skipped[url] = str(exc)
                except SearchConsoleAPIError as exc:
                    errors[url] = str(exc)
            done += 1
            if ctx is not None:
                await ctx.report_progress(
                    done, len(unique), f"Inspected {done} of {len(unique)}"
                )

    await asyncio.gather(*(work(u) for u in unique))

    ordered = [results[u] for u in unique if u in results]
    verdicts = collections.Counter(r["verdict"] for r in ordered)
    coverage = collections.Counter(r["coverage_state"] for r in ordered)
    return {
        "site_url": site,
        "requested": len(unique),
        "inspected": len(ordered),
        "summary": {
            "verdicts": dict(verdicts),
            "coverage_states": dict(coverage),
            "not_indexed": [
                r["url"] for r in ordered if r["verdict"] != "PASS"
            ],
            "canonical_mismatches": [
                {
                    "url": r["url"],
                    "user_canonical": r["user_canonical"],
                    "google_canonical": r["google_canonical"],
                }
                for r in ordered
                if r["canonical_mismatch"]
            ],
        },
        "errors": [{"url": u, "error": e} for u, e in errors.items()],
        "skipped": [{"url": u, "reason": r} for u, r in skipped.items()],
        "quota": {
            "used_today_by_this_server": _quota.used_today(site),
            "daily_limit": PER_DAY_LIMIT,
        },
        "results": ordered,
    }
