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

"""Builds the Search Console MCP server.

Tools are registered on the MCP SDK's MCPServer, which derives each tool's
input schema from the function signature. create_server returns the
MCPServer, which server.py runs over stdio.
"""

import functools
import importlib.metadata
import inspect
from typing import Any, Callable, Iterable

import google.auth.exceptions
import httpx
from mcp import types as mcp_types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from search_console_mcp import client as sc_client
from search_console_mcp.tools.analytics import (
    compare_performance,
    get_pages_for_query,
    get_queries_for_page,
    query_search_analytics,
)
from search_console_mcp.tools.inspection import batch_inspect_urls, inspect_url
from search_console_mcp.tools.sitemaps import (
    delete_sitemap,
    get_sitemap,
    list_sitemaps,
    submit_sitemap,
)
from search_console_mcp.tools.sites import get_site, list_sites

SERVER_NAME = "Google Search Console MCP Server"


def _package_version() -> str:
    try:
        return importlib.metadata.version("search-console-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


SERVER_VERSION = _package_version()

SERVER_INSTRUCTIONS = """\
Google Search Console: search performance, sitemaps and URL Inspection.
Start with list_sites and pass each site_url exactly as it returns it (sc-domain:example.com or https://www.example.com/).
Data runs two to three days behind; dates default to 30daysAgo..3daysAgo. Use compare_performance for period-over-period reports.
URL Inspection allows 2,000 calls a day and 600 a minute per property; batch_inspect_urls paces itself.
submit_sitemap and delete_sitemap need --allow-write and the webmasters scope; nothing else writes.
Limit tools to groups with --tools or SEARCH_CONSOLE_MCP_TOOL_GROUPS: analytics, sitemaps, inspection."""

# Tool groups that --tools / SEARCH_CONSOLE_MCP_TOOL_GROUPS can select.
TOOL_GROUPS = ("analytics", "sitemaps", "inspection")

# Every tool: (function, title, group). A group of None means the tool is
# always registered.
_READ_ONLY_TOOLS: list[tuple[Callable[..., Any], str, str | None]] = [
    (list_sites, "List Search Console properties", None),
    (get_site, "Get a Search Console property", None),
    (query_search_analytics, "Query search analytics", "analytics"),
    (compare_performance, "Compare search performance", "analytics"),
    (get_queries_for_page, "Get queries for a page", "analytics"),
    (get_pages_for_query, "Get pages for a query", "analytics"),
    (list_sitemaps, "List sitemaps", "sitemaps"),
    (get_sitemap, "Get a sitemap", "sitemaps"),
    (inspect_url, "Inspect a URL", "inspection"),
    (batch_inspect_urls, "Inspect a batch of URLs", "inspection"),
]

# Only registered when the server runs with --allow-write.
_WRITE_TOOLS: list[tuple[Callable[..., Any], str, str | None]] = [
    (submit_sitemap, "Submit a sitemap", "sitemaps"),
    (delete_sitemap, "Delete a sitemap", "sitemaps"),
]

# Write tools whose effects can't simply be undone, marked destructive.
_DESTRUCTIVE_TOOL_NAMES = {"delete_sitemap"}

# Write tools that have no further effect when repeated with the same
# arguments.
_IDEMPOTENT_TOOL_NAMES = {"submit_sitemap", "delete_sitemap"}

_READ_ONLY_TOOL_NAMES = {t[0].__name__ for t in _READ_ONLY_TOOLS}
_WRITE_TOOL_NAMES = {t[0].__name__ for t in _WRITE_TOOLS}
_TOOL_GROUP_OF = {t[0].__name__: t[2] for t in _READ_ONLY_TOOLS + _WRITE_TOOLS}


def parse_tool_groups(value: str | Iterable[str] | None) -> frozenset[str]:
    """Parses a comma-separated list of tool groups.

    An empty or missing value selects every group.

    Raises:
        ValueError: If a group isn't one of TOOL_GROUPS.
    """
    if value is None:
        return frozenset(TOOL_GROUPS)
    items = value.split(",") if isinstance(value, str) else value
    groups = {item.strip().lower() for item in items if item.strip()}
    if not groups:
        return frozenset(TOOL_GROUPS)
    unknown = sorted(groups - set(TOOL_GROUPS))
    if unknown:
        raise ValueError(
            f"Unknown tool group(s): {', '.join(unknown)}."
            f" Valid groups: {', '.join(TOOL_GROUPS)}."
        )
    return frozenset(groups)


# Errors whose message helps the agent fix the call. MCP SDK 2 hides the text
# of any exception that isn't a ToolError.
_EXPECTED_ERRORS = (
    ValueError,
    RuntimeError,
    PermissionError,
    FileNotFoundError,
    google.auth.exceptions.GoogleAuthError,
    httpx.HTTPError,
)


def sanitize_mcp_schema_properties(node: dict) -> None:
    """Makes additionalProperties a boolean, which some MCP clients need."""
    if not isinstance(node, dict):
        return
    if "additionalProperties" in node:
        if not isinstance(node["additionalProperties"], bool):
            node["additionalProperties"] = True
    for child in node.values():
        if isinstance(child, dict):
            sanitize_mcp_schema_properties(child)
        elif isinstance(child, list):
            for element in child:
                if isinstance(element, dict):
                    sanitize_mcp_schema_properties(element)


def _report_errors(fn: Callable[..., Any]):
    """Re-raises expected errors as ToolError so clients see the message."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except _EXPECTED_ERRORS as exc:
            raise ToolError(
                f"Failed to execute tool '{fn.__name__}': {exc}"
            ) from exc

    return wrapper


def _annotations(name: str, is_write: bool) -> mcp_types.ToolAnnotations:
    if not is_write:
        return mcp_types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            open_world_hint=True,
        )
    return mcp_types.ToolAnnotations(
        read_only_hint=False,
        destructive_hint=name in _DESTRUCTIVE_TOOL_NAMES,
        idempotent_hint=name in _IDEMPOTENT_TOOL_NAMES,
        open_world_hint=True,
    )


class SearchConsoleMCPServer(MCPServer):
    """MCPServer that explains why a known but unregistered tool is missing."""

    def __init__(self, allow_write: bool, tool_groups: frozenset[str]):
        super().__init__(
            name=SERVER_NAME,
            version=SERVER_VERSION,
            instructions=SERVER_INSTRUCTIONS,
        )
        self._allow_write = allow_write
        self._tool_groups = tool_groups

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context=None
    ):
        if name in _WRITE_TOOL_NAMES and not self._allow_write:
            raise ToolError(
                f"Tool '{name}' is a write tool and is disabled by default."
                " Start the server with the --allow-write flag to enable write"
                " tools."
            )
        group = _TOOL_GROUP_OF.get(name)
        if group is not None and group not in self._tool_groups:
            raise ToolError(
                f"Tool '{name}' is in the '{group}' tool group, which this"
                " server wasn't started with. Add it to --tools or"
                " SEARCH_CONSOLE_MCP_TOOL_GROUPS to enable it."
            )
        return await super().call_tool(name, arguments, context)


def create_server(
    allow_write: bool = False,
    tool_groups: str | Iterable[str] | None = None,
) -> SearchConsoleMCPServer:
    """Creates the MCP server.

    Args:
        allow_write: Registers the write tools and requests the webmasters
          scope instead of webmasters.readonly.
        tool_groups: Tool groups to register (see TOOL_GROUPS), as an iterable
          or a comma-separated string. None or empty registers every group.
          list_sites and get_site are always registered.

    Raises:
        ValueError: If tool_groups names an unknown group.
    """
    groups = parse_tool_groups(tool_groups)
    sc_client.configure(allow_write=allow_write)
    mcp = SearchConsoleMCPServer(allow_write, groups)
    tools = [(*t, False) for t in _READ_ONLY_TOOLS]
    if allow_write:
        tools += [(*t, True) for t in _WRITE_TOOLS]
    for fn, title, group, is_write in tools:
        if group is not None and group not in groups:
            continue
        mcp.add_tool(
            _report_errors(fn),
            name=fn.__name__,
            title=title,
            description=inspect.cleandoc(fn.__doc__ or ""),
            annotations=_annotations(fn.__name__, is_write),
            # Dict return annotations would only publish a generic object
            # schema, wrapped in {"result": ...}.
            structured_output=False,
        )
    for tool in mcp._tool_manager.list_tools():
        sanitize_mcp_schema_properties(tool.parameters)
    return mcp
