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

"""Tests for helpers and the HTTP client."""

import datetime
import unittest
from unittest.mock import patch

from search_console_mcp import client as sc_client
from search_console_mcp.tools import utils
from tests.fakes import FakeAPI


class TestUtils(unittest.TestCase):

    def test_dates(self):
        with patch.object(
            utils, "today", return_value=datetime.date(2026, 3, 1)
        ):
            self.assertEqual(
                utils.resolve_date("today"), datetime.date(2026, 3, 1)
            )
            self.assertEqual(
                utils.resolve_date("Yesterday"), datetime.date(2026, 2, 28)
            )
            self.assertEqual(
                utils.resolve_date("28daysAgo"), datetime.date(2026, 2, 1)
            )
        self.assertEqual(
            utils.resolve_date("2026-01-05"), datetime.date(2026, 1, 5)
        )
        with self.assertRaisesRegex(ValueError, "NdaysAgo"):
            utils.resolve_date("last week")

    def test_site_url(self):
        self.assertEqual(
            utils.site_url("sc-domain:example.com"), "sc-domain:example.com"
        )
        self.assertEqual(
            utils.site_url("https://www.example.com"),
            "https://www.example.com/",
        )
        for bad in ("example.com", "sc-domain:", "ftp://example.com/"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                utils.site_url(bad)

    def test_page_url(self):
        self.assertEqual(
            utils.page_url("https://www.example.com/", "/a/b"),
            "https://www.example.com/a/b",
        )
        self.assertEqual(
            utils.page_url("sc-domain:example.com", "https://example.com/x"),
            "https://example.com/x",
        )
        with self.assertRaises(ValueError):
            utils.page_url("sc-domain:example.com", "/x")

    def test_aliases(self):
        self.assertEqual(
            utils.dimension("search_appearance"), "searchAppearance"
        )
        self.assertEqual(utils.search_type("Discover"), "discover")
        self.assertEqual(utils.aggregation_type("by_page"), "byPage")
        self.assertEqual(
            utils.filters_to_groups(
                [{"dimension": "query", "operator": "regex", "expression": 1}]
            )[0]["filters"][0],
            {
                "dimension": "query",
                "operator": "includingRegex",
                "expression": "1",
            },
        )
        with self.assertRaisesRegex(ValueError, "expression"):
            utils.filters_to_groups([{"dimension": "query"}])

    def test_encode_site(self):
        self.assertEqual(
            sc_client.encode_site("https://www.example.com/"),
            "https%3A%2F%2Fwww.example.com%2F",
        )


class TestClient(unittest.IsolatedAsyncioTestCase):

    async def test_retries_server_errors(self):
        calls = []

        def handler(method, path, body, params):
            calls.append(1)
            if len(calls) < 3:
                return (503, {"error": {"code": 503, "message": "busy"}})
            return {"ok": True}

        client = FakeAPI(handler).install()
        self.assertEqual(
            await client.request("GET", "https://x/y"), {"ok": True}
        )
        self.assertEqual(len(calls), 3)

    async def test_gives_up_after_retries(self):
        client = FakeAPI(
            lambda m, p, j, q: (503, {"error": {"message": "busy"}})
        ).install()
        with self.assertRaisesRegex(sc_client.SearchConsoleAPIError, "busy"):
            await client.request("GET", "https://x/y")

    async def test_quota_and_disabled_api_errors(self):
        client = FakeAPI(
            lambda m, p, j, q: (
                429,
                {"error": {"message": "Quota", "status": "RESOURCE_EXHAUSTED"}},
            )
        ).install()
        with self.assertRaises(sc_client.QuotaExceededError) as raised:
            await client.request("POST", "https://x/y", json={})
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("2,000 calls a day", str(raised.exception))

        client = FakeAPI(
            lambda m, p, j, q: (
                403,
                {
                    "error": {
                        "message": "Google Search Console API has not been"
                        " used in project 123 before or it is disabled.",
                        "status": "PERMISSION_DENIED",
                    }
                },
            )
        ).install()
        with self.assertRaisesRegex(
            sc_client.SearchConsoleAPIError, "Enable the Google Search Console"
        ):
            await client.request("GET", "https://x/y")


if __name__ == "__main__":
    unittest.main()
