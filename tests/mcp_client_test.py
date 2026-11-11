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

"""End-to-end tests through an in-memory mcp.Client against the real server."""

import contextlib
import importlib.metadata
import io
import json
import os
import unittest
from unittest.mock import patch

from mcp import Client

from search_console_mcp import coordinator, server
from tests.fakes import FakeAPI

# Expected (read, write) tool counts per group, excluding list_sites and
# get_site, which every configuration registers.
_GROUP_COUNTS = {
    "analytics": (4, 0),
    "sitemaps": (2, 2),
    "inspection": (2, 0),
}
_ALWAYS = {"list_sites", "get_site"}


async def _list_tools(**kwargs):
    async with Client(coordinator.create_server(**kwargs)) as client:
        return (await client.list_tools()).tools


class TestServerMetadata(unittest.IsolatedAsyncioTestCase):

    async def test_version_name_and_instructions(self):
        async with Client(coordinator.create_server()) as client:
            self.assertEqual(
                client.server_info.version,
                importlib.metadata.version("search-console-mcp"),
            )
            self.assertEqual(
                client.server_info.name, "Google Search Console MCP Server"
            )
            instructions = client.instructions
        lines = instructions.splitlines()
        self.assertTrue(4 <= len(lines) <= 8, lines)
        for expected in (
            "list_sites",
            "compare_performance",
            "batch_inspect_urls",
            "--allow-write",
            "webmasters",
            "--tools",
            "SEARCH_CONSOLE_MCP_TOOL_GROUPS",
        ):
            self.assertIn(expected, instructions)

    async def test_every_tool_has_a_title_and_annotations(self):
        tools = await _list_tools(allow_write=True)
        self.assertEqual(len(tools), 12)
        titles = [t.title for t in tools]
        self.assertEqual(len(set(titles)), len(titles), "titles are unique")
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertTrue(tool.title[0].isupper())
                self.assertIsNotNone(tool.annotations)
                self.assertTrue(tool.annotations.open_world_hint)
                self.assertEqual(
                    tool.annotations.read_only_hint,
                    tool.name in coordinator._READ_ONLY_TOOL_NAMES,
                )
                self.assertTrue(tool.description)
                self.assertIsNone(tool.output_schema)
                self.assertNotIn("ctx", tool.input_schema["properties"])

    async def test_write_annotations(self):
        tools = {t.name: t for t in await _list_tools(allow_write=True)}
        delete = tools["delete_sitemap"].annotations
        submit = tools["submit_sitemap"].annotations
        self.assertTrue(delete.destructive_hint)
        self.assertTrue(delete.idempotent_hint)
        self.assertFalse(submit.destructive_hint)
        self.assertTrue(submit.idempotent_hint)
        for name in coordinator._READ_ONLY_TOOL_NAMES:
            self.assertFalse(tools[name].annotations.destructive_hint)

    async def test_tool_counts_with_and_without_allow_write(self):
        read = {t.name for t in await _list_tools()}
        everything = {t.name for t in await _list_tools(allow_write=True)}
        self.assertEqual(len(read), 10)
        self.assertEqual(
            everything - read, {"submit_sitemap", "delete_sitemap"}
        )


class TestToolGroups(unittest.IsolatedAsyncioTestCase):

    async def test_each_group_alone(self):
        for group, (read_count, write_count) in _GROUP_COUNTS.items():
            with self.subTest(group=group):
                read = {t.name for t in await _list_tools(tool_groups=group)}
                rw = {
                    t.name
                    for t in await _list_tools(
                        tool_groups=group, allow_write=True
                    )
                }
                self.assertTrue(_ALWAYS <= read)
                self.assertEqual(len(read), 2 + read_count)
                self.assertEqual(len(rw), 2 + read_count + write_count)
                for name in rw - _ALWAYS:
                    self.assertEqual(coordinator._TOOL_GROUP_OF[name], group)

    async def test_groups_cover_every_tool(self):
        self.assertEqual(
            sum(r + w for r, w in _GROUP_COUNTS.values()) + 2,
            len(coordinator._TOOL_GROUP_OF),
        )
        self.assertEqual(set(_GROUP_COUNTS), set(coordinator.TOOL_GROUPS))

    async def test_multiple_groups_and_whitespace(self):
        names = {
            t.name
            for t in await _list_tools(tool_groups=" Analytics , inspection ,")
        }
        self.assertIn("query_search_analytics", names)
        self.assertIn("inspect_url", names)
        self.assertNotIn("list_sitemaps", names)
        self.assertEqual(len(names), 2 + 4 + 2)

    async def test_empty_selects_every_group(self):
        self.assertEqual(len(await _list_tools(tool_groups="")), 10)
        self.assertEqual(len(await _list_tools(tool_groups=None)), 10)

    def test_unknown_group_fails_at_startup(self):
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            server.main(["--tools", "analytics,bogus"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "--tools: Unknown tool group(s): bogus", stderr.getvalue()
        )

    def test_unknown_group_in_env_var_fails(self):
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {server.TOOL_GROUPS_ENV_VAR: "nope"}),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            server.parse_args([])
        self.assertIn(
            "SEARCH_CONSOLE_MCP_TOOL_GROUPS: Unknown tool group(s): nope",
            stderr.getvalue(),
        )

    def test_flag_overrides_env_var(self):
        with patch.dict(os.environ, {server.TOOL_GROUPS_ENV_VAR: "sitemaps"}):
            self.assertEqual(server.parse_args([]).tool_groups, {"sitemaps"})
            self.assertEqual(
                server.parse_args(["--tools", "inspection"]).tool_groups,
                {"inspection"},
            )

    async def test_calling_a_tool_from_a_disabled_group_explains(self):
        app = coordinator.create_server(tool_groups="sitemaps")
        async with Client(app) as client:
            result = await client.call_tool(
                "inspect_url",
                {"site_url": "sc-domain:example.com", "url": "x"},
            )
        self.assertTrue(result.is_error)
        self.assertIn("'inspection' tool group", result.content[0].text)


class TestToolCalls(unittest.IsolatedAsyncioTestCase):

    async def test_list_sites_returns_property_types(self):
        fake = FakeAPI(
            lambda m, p, j, q: {
                "siteEntry": [
                    {
                        "siteUrl": "sc-domain:example.com",
                        "permissionLevel": "siteOwner",
                    },
                    {
                        "siteUrl": "https://www.example.com/",
                        "permissionLevel": "siteFullUser",
                    },
                ]
            }
        )
        app = coordinator.create_server()
        fake.install()
        async with Client(app) as client:
            result = await client.call_tool("list_sites", {})
        self.assertFalse(result.is_error)
        data = json.loads(result.content[0].text)
        self.assertEqual(data["count"], 2)
        by_url = {s["site_url"]: s for s in data["sites"]}
        self.assertEqual(
            by_url["sc-domain:example.com"]["property_type"], "domain"
        )
        self.assertEqual(
            by_url["https://www.example.com/"]["property_type"], "url_prefix"
        )
        self.assertEqual(fake.requests[0]["path"], "/webmasters/v3/sites")
        self.assertEqual(
            fake.requests[0]["headers"]["authorization"], "Bearer test-token"
        )

    async def test_api_error_reaches_the_client_with_a_hint(self):
        fake = FakeAPI(
            lambda m, p, j, q: (
                403,
                {
                    "error": {
                        "code": 403,
                        "message": "User does not have sufficient permission",
                        "status": "PERMISSION_DENIED",
                    }
                },
            )
        )
        app = coordinator.create_server()
        fake.install()
        async with Client(app) as client:
            result = await client.call_tool(
                "get_site", {"site_url": "sc-domain:example.com"}
            )
        self.assertTrue(result.is_error)
        text = result.content[0].text
        self.assertIn("403 PERMISSION_DENIED", text)
        self.assertIn("list_sites", text)

    async def test_validation_error_reaches_the_client(self):
        async with Client(coordinator.create_server()) as client:
            result = await client.call_tool(
                "get_site", {"site_url": "example.com"}
            )
        self.assertTrue(result.is_error)
        self.assertIn("sc-domain:example.com", result.content[0].text)

    async def test_write_tool_without_allow_write_explains(self):
        async with Client(coordinator.create_server()) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertNotIn("submit_sitemap", names)
            result = await client.call_tool(
                "submit_sitemap",
                {
                    "site_url": "sc-domain:example.com",
                    "sitemap_url": "https://example.com/sitemap.xml",
                },
            )
        self.assertTrue(result.is_error)
        self.assertIn("is a write tool", result.content[0].text)
        self.assertIn("--allow-write", result.content[0].text)

    async def test_submit_and_delete_sitemap_with_allow_write(self):
        fake = FakeAPI(lambda m, p, j, q: {})
        app = coordinator.create_server(allow_write=True)
        fake.install(allow_write=True)
        args = {
            "site_url": "sc-domain:example.com",
            "sitemap_url": "https://example.com/sitemap.xml",
        }
        async with Client(app) as client:
            submitted = await client.call_tool("submit_sitemap", args)
            deleted = await client.call_tool("delete_sitemap", args)
        self.assertFalse(submitted.is_error)
        self.assertFalse(deleted.is_error)
        path = (
            "/webmasters/v3/sites/sc-domain%3Aexample.com/sitemaps/"
            "https%3A%2F%2Fexample.com%2Fsitemap.xml"
        )
        self.assertEqual(
            [(r["method"], r["path"]) for r in fake.requests],
            [("PUT", path), ("DELETE", path)],
        )


if __name__ == "__main__":
    unittest.main()
