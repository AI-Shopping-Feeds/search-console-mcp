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

"""Tests for the Search Analytics tools."""

import datetime
import unittest
from unittest.mock import patch

from search_console_mcp.tools import analytics
from tests.fakes import FakeAPI, analytics_rows

_TODAY = datetime.date(2026, 11, 11)
_QUERY_PATH = (
    "/webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query"
)


def _patch_today():
    return patch("search_console_mcp.tools.utils.today", return_value=_TODAY)


class TestQuerySearchAnalytics(unittest.IsolatedAsyncioTestCase):

    async def test_builds_the_request_and_names_rows(self):
        fake = FakeAPI(
            lambda m, p, j, q: analytics_rows(
                (["shoes", "gbr"], 10, 100, 0.1, 3.456),
                (["boots", "gbr"], 5, 80, 0.0625, 7.1),
            )
        )
        fake.install()
        with _patch_today():
            result = await analytics.query_search_analytics(
                "sc-domain:example.com",
                dimensions=["query", "country"],
                filters=[
                    {
                        "dimension": "page",
                        "operator": "not_contains",
                        "expression": "/blog/",
                    }
                ],
                search_type="image",
                row_limit=2,
            )
        request = fake.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], _QUERY_PATH)
        self.assertEqual(
            request["json"],
            {
                "startDate": "2026-10-12",
                "endDate": "2026-11-08",
                "type": "image",
                "dataState": "final",
                "aggregationType": "auto",
                "dimensions": ["query", "country"],
                "dimensionFilterGroups": [
                    {
                        "groupType": "and",
                        "filters": [
                            {
                                "dimension": "page",
                                "operator": "notContains",
                                "expression": "/blog/",
                            }
                        ],
                    }
                ],
                "rowLimit": 2,
                "startRow": 0,
            },
        )
        self.assertEqual(
            result["rows"][0],
            {
                "query": "shoes",
                "country": "gbr",
                "clicks": 10,
                "impressions": 100,
                "ctr": 0.1,
                "position": 3.46,
            },
        )
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_start_row"], 2)

    async def test_last_page_has_no_more(self):
        FakeAPI(
            lambda m, p, j, q: analytics_rows((["a"], 1, 2, 0.5, 1.0))
        ).install()
        result = await analytics.query_search_analytics(
            "https://www.example.com/",
            "2026-01-01",
            "2026-01-31",
            ["query"],
            row_limit=10,
        )
        self.assertFalse(result["has_more"])
        self.assertIsNone(result["next_start_row"])

    async def test_rejects_bad_input(self):
        site = "sc-domain:example.com"
        with self.assertRaisesRegex(ValueError, "Unknown dimension"):
            await analytics.query_search_analytics(site, dimensions=["url"])
        with self.assertRaisesRegex(ValueError, "row_limit"):
            await analytics.query_search_analytics(site, row_limit=25001)
        with self.assertRaisesRegex(ValueError, "Unknown operator"):
            await analytics.query_search_analytics(
                site,
                filters=[
                    {
                        "dimension": "query",
                        "operator": "like",
                        "expression": "x",
                    }
                ],
            )
        with self.assertRaisesRegex(ValueError, "after end_date"):
            await analytics.query_search_analytics(
                site, "2026-02-01", "2026-01-01"
            )


class TestComparePerformance(unittest.IsolatedAsyncioTestCase):

    async def test_totals_rows_and_brand_split(self):
        def handler(method, path, body, params):
            current = body["startDate"] == "2026-10-01"
            filters = body.get("dimensionFilterGroups", [])
            op = filters[0]["filters"][0]["operator"] if filters else None
            if "dimensions" in body:
                if current:
                    return analytics_rows(
                        (["acme shoes"], 50, 500, 0.1, 2.0),
                        (["red shoes"], 30, 900, 0.033, 6.0),
                        (["new query"], 5, 50, 0.1, 9.0),
                    )
                return analytics_rows(
                    (["acme shoes"], 40, 450, 0.089, 2.5),
                    (["red shoes"], 45, 1000, 0.045, 4.0),
                    (["gone query"], 8, 70, 0.11, 8.0),
                )
            if op == "includingRegex":
                return analytics_rows(
                    ([], 50, 500, 0.1, 2.0)
                    if current
                    else ([], 40, 450, 0.089, 2.5)
                )
            if op == "excludingRegex":
                return analytics_rows(
                    ([], 35, 950, 0.037, 6.2)
                    if current
                    else ([], 53, 1070, 0.05, 4.3)
                )
            return analytics_rows(
                ([], 100, 1600, 0.0625, 4.0)
                if current
                else ([], 100, 1600, 0.0625, 4.0)
            )

        fake = FakeAPI(handler)
        fake.install()
        result = await analytics.compare_performance(
            "sc-domain:example.com",
            dimension="query",
            start_date="2026-10-01",
            end_date="2026-10-31",
            brand_terms=["Acme", "a.c.m.e"],
        )
        self.assertEqual(
            result["previous_period"],
            {"start_date": "2026-08-31", "end_date": "2026-09-30"},
        )
        brand_bodies = [
            r["json"]
            for r in fake.requests
            if r["json"].get("dimensionFilterGroups")
        ]
        self.assertEqual(
            brand_bodies[0]["dimensionFilterGroups"][0]["filters"][0][
                "expression"
            ],
            "(?i)Acme|a\\.c\\.m\\.e",
        )
        split = result["brand_split"]
        self.assertEqual(split["brand"]["change"]["clicks"], 10)
        self.assertEqual(split["non_brand"]["change"]["clicks"], -18)
        self.assertEqual(
            split["anonymised_or_unmatched"]["current"],
            {"clicks": 15, "impressions": 150},
        )
        self.assertEqual(result["totals"]["change"]["clicks_pct"], 0.0)

        rows = {r["key"]: r for r in result["rows"]}
        self.assertEqual(result["rows_compared"], 4)
        self.assertEqual(result["rows"][0]["key"], "acme shoes")
        self.assertEqual(rows["red shoes"]["clicks_change"], -15)
        self.assertEqual(rows["red shoes"]["position_change"], 2.0)
        self.assertTrue(rows["new query"]["new"])
        self.assertIsNone(rows["new query"]["position_change"])
        self.assertTrue(rows["gone query"]["lost"])
        self.assertEqual(rows["gone query"]["clicks"], 0)

    async def test_sort_by_biggest_losers(self):
        FakeAPI(
            lambda m, p, j, q: (
                analytics_rows((["a"], 1, 1, 1.0, 1.0))
                if j["startDate"] == "2026-10-01"
                else analytics_rows(
                    (["a"], 5, 5, 1.0, 1.0), (["b"], 20, 20, 1.0, 1.0)
                )
            )
        ).install()
        result = await analytics.compare_performance(
            "sc-domain:example.com",
            "query",
            "2026-10-01",
            "2026-10-31",
            sort_by="clicks_change",
            descending=False,
            row_limit=1,
        )
        self.assertEqual([r["key"] for r in result["rows"]], ["b"])

    async def test_previous_year(self):
        fake = FakeAPI(lambda m, p, j, q: analytics_rows())
        fake.install()
        result = await analytics.compare_performance(
            "sc-domain:example.com",
            start_date="2024-02-29",
            end_date="2024-03-31",
            comparison="previous_year",
        )
        self.assertEqual(
            result["previous_period"],
            {"start_date": "2023-02-28", "end_date": "2023-03-31"},
        )
        self.assertEqual(result["totals"]["current"]["clicks"], 0)
        self.assertNotIn("rows", result)

    async def test_paging_fetches_beyond_25000(self):
        calls = []

        def handler(method, path, body, params):
            calls.append((body["startRow"], body["rowLimit"]))
            if body["rowLimit"] == 1:
                return analytics_rows()
            count = body["rowLimit"] if body["startRow"] == 0 else 3
            return analytics_rows(
                *[
                    ([f"q{body['startRow'] + i}"], 1, 1, 1.0, 1.0)
                    for i in range(count)
                ]
            )

        FakeAPI(handler).install()
        rows = await analytics._fetch_rows(
            "sc-domain:example.com", {"startDate": "x"}, 30000
        )
        self.assertEqual(len(rows), 25003)
        self.assertEqual(calls, [(0, 25000), (25000, 5000)])

    def test_brand_regex_needs_terms(self):
        with self.assertRaises(ValueError):
            analytics.brand_regex([" ", ""])


class TestPageAndQueryReports(unittest.IsolatedAsyncioTestCase):

    async def test_queries_for_page_resolves_path(self):
        def handler(method, path, body, params):
            if "dimensions" in body:
                return analytics_rows((["shoes"], 3, 30, 0.1, 5.0))
            return analytics_rows(([], 9, 90, 0.1, 5.0))

        fake = FakeAPI(handler)
        fake.install()
        result = await analytics.get_queries_for_page(
            "https://www.example.com/", "/products/shoe"
        )
        self.assertEqual(
            result["page"], "https://www.example.com/products/shoe"
        )
        self.assertEqual(result["page_totals"]["clicks"], 9)
        self.assertEqual(result["queries"][0]["query"], "shoes")
        page_filter = fake.requests[0]["json"]["dimensionFilterGroups"][0][
            "filters"
        ][0]
        self.assertEqual(page_filter["operator"], "equals")

    async def test_domain_property_needs_full_url(self):
        with self.assertRaisesRegex(ValueError, "full URL"):
            await analytics.get_queries_for_page(
                "sc-domain:example.com", "/products/shoe"
            )

    async def test_pages_for_query_flags_cannibalisation(self):
        FakeAPI(
            lambda m, p, j, q: analytics_rows(
                (["https://example.com/a"], 3, 30, 0.1, 5.0),
                (["https://example.com/b"], 1, 40, 0.025, 9.0),
            )
        ).install()
        result = await analytics.get_pages_for_query(
            "sc-domain:example.com", "red shoes", match="contains"
        )
        self.assertTrue(result["multiple_pages_rank"])
        self.assertEqual(result["pages"][1]["page"], "https://example.com/b")


if __name__ == "__main__":
    unittest.main()
