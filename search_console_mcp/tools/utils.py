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

"""Validation and conversion helpers shared by the tools."""

import datetime
import re
from typing import Any
from urllib.parse import urljoin, urlparse

DIMENSIONS = {
    "query": "query",
    "page": "page",
    "country": "country",
    "device": "device",
    "date": "date",
    "searchappearance": "searchAppearance",
    "search_appearance": "searchAppearance",
    "hour": "hour",
}

SEARCH_TYPES = {
    "web": "web",
    "image": "image",
    "video": "video",
    "news": "news",
    "discover": "discover",
    "googlenews": "googleNews",
    "google_news": "googleNews",
}

OPERATORS = {
    "equals": "equals",
    "notequals": "notEquals",
    "not_equals": "notEquals",
    "contains": "contains",
    "notcontains": "notContains",
    "not_contains": "notContains",
    "includingregex": "includingRegex",
    "including_regex": "includingRegex",
    "regex": "includingRegex",
    "excludingregex": "excludingRegex",
    "excluding_regex": "excludingRegex",
}

DATA_STATES = {"final": "final", "all": "all", "hourly_all": "hourly_all"}

AGGREGATION_TYPES = {
    "auto": "auto",
    "bypage": "byPage",
    "by_page": "byPage",
    "byproperty": "byProperty",
    "by_property": "byProperty",
    "bynewsshowcasepanel": "byNewsShowcasePanel",
}

MAX_ROW_LIMIT = 25000

_DAYS_AGO = re.compile(r"^(\d+)daysago$")


def _pick(value: str, allowed: dict[str, str], what: str) -> str:
    key = str(value).strip().lower()
    if key not in allowed:
        valid = sorted(set(allowed.values()))
        raise ValueError(
            f"Unknown {what} '{value}'. Valid values: {', '.join(valid)}."
        )
    return allowed[key]


def dimension(value: str) -> str:
    return _pick(value, DIMENSIONS, "dimension")


def search_type(value: str) -> str:
    return _pick(value, SEARCH_TYPES, "search type")


def data_state(value: str) -> str:
    return _pick(value, DATA_STATES, "data state")


def aggregation_type(value: str) -> str:
    return _pick(value, AGGREGATION_TYPES, "aggregation type")


def today() -> datetime.date:
    return datetime.date.today()


def resolve_date(value: str | datetime.date) -> datetime.date:
    """Parses YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'."""
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    lowered = text.lower()
    if lowered == "today":
        return today()
    if lowered == "yesterday":
        return today() - datetime.timedelta(days=1)
    match = _DAYS_AGO.match(lowered)
    if match:
        return today() - datetime.timedelta(days=int(match.group(1)))
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"Invalid date '{value}'. Use YYYY-MM-DD, 'today', 'yesterday' or"
            " 'NdaysAgo' (e.g. '28daysAgo')."
        ) from None


def date_range(
    start_date: str | datetime.date, end_date: str | datetime.date
) -> tuple[datetime.date, datetime.date]:
    start, end = resolve_date(start_date), resolve_date(end_date)
    if start > end:
        raise ValueError(f"start_date {start} is after end_date {end}.")
    return start, end


def site_url(value: str) -> str:
    """Validates a property string: 'sc-domain:example.com' or a URL prefix."""
    text = str(value).strip()
    if text.startswith("sc-domain:"):
        if len(text) == len("sc-domain:"):
            raise ValueError("Domain property is missing its domain.")
        return text
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return text if text.endswith("/") else text + "/"
    raise ValueError(
        f"Invalid property '{value}'. Use 'sc-domain:example.com' for a domain"
        " property or 'https://www.example.com/' for a URL-prefix property."
        " Call list_sites to see your properties."
    )


def page_url(site: str, url: str) -> str:
    """Turns a path into a full URL for URL-prefix properties.

    Domain properties can cover several hosts (www, apex, subdomains), so they
    need full URLs.
    """
    text = str(url).strip()
    if urlparse(text).scheme in ("http", "https"):
        return text
    if site.startswith("sc-domain:"):
        raise ValueError(
            f"'{url}' isn't a full URL. For a domain property ({site}), pass"
            " full URLs such as 'https://www.example.com/page'."
        )
    return urljoin(site, text)


def filters_to_groups(
    filters: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Converts [{dimension, operator, expression}] to dimensionFilterGroups.

    All filters are combined with AND, the only group type the API supports.
    """
    if not filters:
        return []
    converted = []
    for item in filters:
        if not isinstance(item, dict) or "expression" not in item:
            raise ValueError(
                "Each filter needs 'dimension', 'operator' and 'expression',"
                " e.g. {'dimension': 'query', 'operator': 'contains',"
                " 'expression': 'shoes'}."
            )
        converted.append(
            {
                "dimension": dimension(item.get("dimension", "")),
                "operator": _pick(
                    item.get("operator", "equals"), OPERATORS, "operator"
                ),
                "expression": str(item["expression"]),
            }
        )
    return [{"groupType": "and", "filters": converted}]


def metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Returns clicks, impressions, CTR and position from an API row."""
    return {
        "clicks": row.get("clicks", 0),
        "impressions": row.get("impressions", 0),
        "ctr": round(row.get("ctr", 0.0), 4),
        "position": round(row.get("position", 0.0), 2),
    }


def row_to_dict(dims: list[str], row: dict[str, Any]) -> dict[str, Any]:
    """Names an API row's keys after its dimensions."""
    out = dict(zip(dims, row.get("keys", [])))
    out.update(metrics(row))
    return out
