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

"""Tools for Search Console properties (sites)."""

from typing import Any

from search_console_mcp.client import WEBMASTERS, encode_site, get_client
from search_console_mcp.tools import utils


def _describe(entry: dict[str, Any]) -> dict[str, Any]:
    url = entry.get("siteUrl", "")
    return {
        "site_url": url,
        "property_type": (
            "domain" if url.startswith("sc-domain:") else "url_prefix"
        ),
        "permission_level": entry.get("permissionLevel"),
    }


async def list_sites() -> dict[str, Any]:
    """Lists the Search Console properties the signed-in account can see.

    Returns each property's exact `site_url` (pass it unchanged to every other
    tool), whether it's a domain property ('sc-domain:example.com') or a
    URL-prefix property ('https://www.example.com/'), and your permission
    level (siteOwner, siteFullUser, siteRestrictedUser or
    siteUnverifiedUser; unverified properties return no data).
    """
    data = await get_client().request("GET", f"{WEBMASTERS}/sites")
    sites = sorted(
        (_describe(e) for e in data.get("siteEntry", [])),
        key=lambda s: s["site_url"],
    )
    return {"sites": sites, "count": len(sites)}


async def get_site(site_url: str) -> dict[str, Any]:
    """Gets one property's type and your permission level.

    Args:
        site_url: The property, exactly as list_sites returns it, e.g.
          'sc-domain:example.com' or 'https://www.example.com/'.
    """
    site = utils.site_url(site_url)
    data = await get_client().request(
        "GET", f"{WEBMASTERS}/sites/{encode_site(site)}"
    )
    return _describe(data)
