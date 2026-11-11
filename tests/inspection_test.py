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

"""Tests for the URL Inspection tools and quota pacing."""

import datetime
import unittest
from unittest.mock import patch

from search_console_mcp.tools import inspection
from tests.fakes import FakeAPI

_SITE = "sc-domain:example.com"


def _result(url: str, verdict: str = "PASS", **index) -> dict:
    status = {
        "verdict": verdict,
        "coverageState": (
            "Submitted and indexed"
            if verdict == "PASS"
            else "Excluded by 'noindex' tag"
        ),
        "robotsTxtState": "ALLOWED",
        "indexingState": "INDEXING_ALLOWED",
        "pageFetchState": "SUCCESSFUL",
        "lastCrawlTime": "2026-11-01T10:00:00Z",
        "crawledAs": "MOBILE",
        "googleCanonical": url,
        "userCanonical": url,
    }
    status.update(index)
    return {
        "inspectionResult": {
            "inspectionResultLink": "https://search.google.com/x",
            "indexStatusResult": status,
            "richResultsResult": {
                "verdict": "PASS",
                "detectedItems": [
                    {
                        "richResultType": "Product snippets",
                        "items": [
                            {
                                "name": "Shoe",
                                "issues": [
                                    {
                                        "issueMessage": "Missing field 'review'",
                                        "severity": "WARNING",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        }
    }


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TestInspectUrl(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.quota = patch.object(
            inspection, "_quota", inspection.InspectionQuota()
        )
        self.quota.start()

    def tearDown(self):
        self.quota.stop()

    async def test_summary_flags_canonical_mismatch(self):
        url = "https://www.example.com/products/shoe-a"
        fake = FakeAPI(
            lambda m, p, j, q: _result(
                url,
                "NEUTRAL",
                googleCanonical="https://www.example.com/products/shoe",
            )
        )
        fake.install()
        result = await inspection.inspect_url(_SITE, url, "en-GB")
        self.assertEqual(
            fake.requests[0]["json"],
            {"inspectionUrl": url, "siteUrl": _SITE, "languageCode": "en-GB"},
        )
        self.assertEqual(
            fake.requests[0]["path"], "/v1/urlInspection/index:inspect"
        )
        self.assertTrue(result["canonical_mismatch"])
        self.assertEqual(result["verdict"], "NEUTRAL")
        self.assertEqual(
            result["rich_results"]["types"][0]["issues"][0]["severity"],
            "WARNING",
        )
        self.assertNotIn("raw", result)

    async def test_path_for_url_prefix_property(self):
        fake = FakeAPI(lambda m, p, j, q: _result(j["inspectionUrl"]))
        fake.install()
        result = await inspection.inspect_url(
            "https://www.example.com/", "/pages/about", include_raw=True
        )
        self.assertEqual(result["url"], "https://www.example.com/pages/about")
        self.assertFalse(result["canonical_mismatch"])
        self.assertIn("indexStatusResult", result["raw"])


class TestBatchInspect(unittest.IsolatedAsyncioTestCase):

    async def test_dedupes_summarises_and_reports_errors(self):
        def handler(method, path, body, params):
            url = body["inspectionUrl"]
            if url.endswith("/broken"):
                return (500, {"error": {"code": 500, "message": "boom"}})
            if url.endswith("/hidden"):
                return _result(url, "NEUTRAL")
            return _result(url)

        fake = FakeAPI(handler)
        fake.install()
        with patch.object(inspection, "_quota", inspection.InspectionQuota()):
            result = await inspection.batch_inspect_urls(
                _SITE,
                [
                    "https://example.com/a",
                    "https://example.com/a",
                    "https://example.com/hidden",
                    "https://example.com/broken",
                ],
            )
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["inspected"], 2)
        self.assertEqual(
            result["summary"]["not_indexed"], ["https://example.com/hidden"]
        )
        self.assertEqual(
            result["summary"]["verdicts"], {"PASS": 1, "NEUTRAL": 1}
        )
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("boom", result["errors"][0]["error"])
        self.assertEqual(result["quota"]["used_today_by_this_server"], 3)

    async def test_stops_on_quota_error(self):
        def handler(method, path, body, params):
            return (
                429,
                {"error": {"code": 429, "message": "Quota exceeded"}},
            )

        FakeAPI(handler).install()
        with patch.object(inspection, "_quota", inspection.InspectionQuota()):
            result = await inspection.batch_inspect_urls(
                _SITE,
                [f"https://example.com/{i}" for i in range(6)],
                concurrency=1,
            )
        self.assertEqual(result["inspected"], 0)
        self.assertEqual(len(result["skipped"]), 6)
        self.assertIn("Quota exceeded", result["skipped"][0]["reason"])
        self.assertEqual(result["skipped"][1]["reason"], "quota used up")

    async def test_rejects_large_batches(self):
        with self.assertRaisesRegex(ValueError, "at most 200"):
            await inspection.batch_inspect_urls(
                _SITE, [f"https://example.com/{i}" for i in range(201)]
            )


class TestQuota(unittest.IsolatedAsyncioTestCase):

    async def test_paces_per_minute(self):
        clock = FakeClock()
        quota = inspection.InspectionQuota(clock=clock, sleep=clock.sleep)
        for _ in range(3):
            self.assertTrue(await quota.acquire(_SITE, per_minute=2))
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 60.01)

    async def test_daily_limit_per_site_and_day(self):
        day = [datetime.date(2026, 11, 11)]
        quota = inspection.InspectionQuota(today=lambda: day[0])
        quota._daily[(_SITE, day[0])] = inspection.PER_DAY_LIMIT
        self.assertFalse(await quota.acquire(_SITE))
        self.assertTrue(await quota.acquire("sc-domain:other.com"))
        day[0] = datetime.date(2026, 11, 12)
        self.assertTrue(await quota.acquire(_SITE))
        self.assertEqual(quota.used_today(_SITE), 1)

    async def test_inspect_url_explains_daily_limit(self):
        quota = inspection.InspectionQuota()
        quota._daily[(_SITE, inspection._pacific_today())] = 2000
        with patch.object(inspection, "_quota", quota):
            with self.assertRaisesRegex(RuntimeError, "daily URL Inspection"):
                await inspection.inspect_url(_SITE, "https://example.com/")


if __name__ == "__main__":
    unittest.main()
