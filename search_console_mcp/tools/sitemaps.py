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

"""Tools for sitemaps."""

from typing import Any
from urllib.parse import urlparse

from search_console_mcp.client import WEBMASTERS, encode_site, get_client
from search_console_mcp.tools import utils


def _sitemap_url(value: str) -> str:
    text = str(value).strip()
    if urlparse(text).scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid sitemap URL '{value}'. Pass the full URL, e.g."
            " 'https://www.example.com/sitemap.xml'."
        )
    return text


def _int(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _describe(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": entry.get("path"),
        "type": entry.get("type"),
        "is_sitemaps_index": entry.get("isSitemapsIndex", False),
        "is_pending": entry.get("isPending", False),
        "last_submitted": entry.get("lastSubmitted"),
        "last_downloaded": entry.get("lastDownloaded"),
        "errors": _int(entry.get("errors", 0)),
        "warnings": _int(entry.get("warnings", 0)),
        "contents": [
            {
                "type": c.get("type"),
                "submitted": _int(c.get("submitted")),
                # Google stopped populating 'indexed'; use URL Inspection.
                "indexed": _int(c.get("indexed")),
            }
            for c in entry.get("contents", [])
        ],
    }


def _base(site_url: str) -> str:
    return f"{WEBMASTERS}/sites/{encode_site(utils.site_url(site_url))}"


async def list_sitemaps(
    site_url: str, sitemap_index: str | None = None
) -> dict[str, Any]:
    """Lists the sitemaps submitted for a property, with their status.

    Shows when Google last downloaded each sitemap, error and warning counts,
    whether it's still pending, and how many URLs it lists. Google no longer
    reports indexed counts here; use inspect_url for index status.

    Args:
        site_url: The property, exactly as list_sites returns it.
        sitemap_index: Optional sitemap index URL; lists only the sitemaps in
          that index.
    """
    params = {}
    if sitemap_index:
        params["sitemapIndex"] = _sitemap_url(sitemap_index)
    data = await get_client().request(
        "GET", f"{_base(site_url)}/sitemaps", params=params or None
    )
    sitemaps = [_describe(e) for e in data.get("sitemap", [])]
    return {"sitemaps": sitemaps, "count": len(sitemaps)}


async def get_sitemap(site_url: str, sitemap_url: str) -> dict[str, Any]:
    """Gets one sitemap's status: downloads, errors, warnings and URL counts.

    Args:
        site_url: The property, exactly as list_sites returns it.
        sitemap_url: The sitemap's full URL.
    """
    feed = encode_site(_sitemap_url(sitemap_url))
    data = await get_client().request(
        "GET", f"{_base(site_url)}/sitemaps/{feed}"
    )
    return _describe(data)


async def submit_sitemap(site_url: str, sitemap_url: str) -> dict[str, Any]:
    """Submits (or resubmits) a sitemap so Google fetches it.

    Needs --allow-write, the webmasters scope, and Owner or Full permission.

    Args:
        site_url: The property, exactly as list_sites returns it.
        sitemap_url: The sitemap's full URL, on a host the property covers.
    """
    url = _sitemap_url(sitemap_url)
    await get_client().request(
        "PUT", f"{_base(site_url)}/sitemaps/{encode_site(url)}"
    )
    return {"submitted": url, "site_url": utils.site_url(site_url)}


async def delete_sitemap(site_url: str, sitemap_url: str) -> dict[str, Any]:
    """Removes a sitemap from Search Console's sitemap report.

    Google stops reading it from Search Console; the file on your server and
    URLs already indexed are not affected. Needs --allow-write, the
    webmasters scope, and Owner or Full permission.

    Args:
        site_url: The property, exactly as list_sites returns it.
        sitemap_url: The sitemap's full URL, exactly as list_sitemaps shows it.
    """
    url = _sitemap_url(sitemap_url)
    await get_client().request(
        "DELETE", f"{_base(site_url)}/sitemaps/{encode_site(url)}"
    )
    return {"deleted": url, "site_url": utils.site_url(site_url)}
