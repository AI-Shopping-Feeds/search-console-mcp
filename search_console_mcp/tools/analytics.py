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

"""Search Analytics tools: performance queries and comparison reports."""

import asyncio
import datetime
from typing import Any

from search_console_mcp.client import WEBMASTERS, encode_site, get_client
from search_console_mcp.tools import utils

_SORT_KEYS = (
    "clicks",
    "impressions",
    "ctr",
    "position",
    "clicks_change",
    "impressions_change",
    "position_change",
)

# Characters with a meaning in RE2 regular expressions.
_REGEX_SPECIAL = set("\\.^$|?*+()[]{}")


def _regex_escape(term: str) -> str:
    return "".join("\\" + c if c in _REGEX_SPECIAL else c for c in term)


def brand_regex(terms: list[str]) -> str:
    """Builds a case-insensitive RE2 pattern matching any of the terms."""
    cleaned = [t.strip() for t in terms if t and t.strip()]
    if not cleaned:
        raise ValueError("brand_terms needs at least one non-empty term.")
    return "(?i)" + "|".join(_regex_escape(t) for t in cleaned)


def _body(
    start: datetime.date,
    end: datetime.date,
    dims: list[str],
    search_type: str,
    groups: list[dict[str, Any]],
    aggregation: str = "auto",
    state: str = "final",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "type": search_type,
        "dataState": state,
        "aggregationType": aggregation,
    }
    if dims:
        body["dimensions"] = dims
    if groups:
        body["dimensionFilterGroups"] = groups
    return body


async def _query(site: str, body: dict[str, Any]) -> dict[str, Any]:
    return await get_client().request(
        "POST",
        f"{WEBMASTERS}/sites/{encode_site(site)}/searchAnalytics/query",
        json=body,
    )


async def _fetch_rows(
    site: str, body: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    """Fetches up to `limit` rows, paging 25,000 at a time."""
    rows: list[dict[str, Any]] = []
    while len(rows) < limit:
        page_size = min(utils.MAX_ROW_LIMIT, limit - len(rows))
        page = dict(body, rowLimit=page_size, startRow=len(rows))
        batch = (await _query(site, page)).get("rows", [])
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


async def _totals(site: str, body: dict[str, Any]) -> dict[str, Any]:
    rows = (await _query(site, dict(body, rowLimit=1))).get("rows", [])
    return utils.metrics(rows[0] if rows else {})


def _pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _change(cur: dict[str, Any], prev: dict[str, Any]) -> dict[str, Any]:
    return {
        "clicks": cur["clicks"] - prev["clicks"],
        "clicks_pct": _pct(cur["clicks"], prev["clicks"]),
        "impressions": cur["impressions"] - prev["impressions"],
        "impressions_pct": _pct(cur["impressions"], prev["impressions"]),
        "ctr_points": round((cur["ctr"] - prev["ctr"]) * 100, 2),
        # Negative means a better (higher) average position.
        "position": round(cur["position"] - prev["position"], 2),
    }


def _previous_period(
    start: datetime.date, end: datetime.date, comparison: str
) -> tuple[datetime.date, datetime.date]:
    if comparison == "previous_period":
        length = end - start
        prev_end = start - datetime.timedelta(days=1)
        return prev_end - length, prev_end
    if comparison == "previous_year":

        def shift(d: datetime.date) -> datetime.date:
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # 29 February
                return d.replace(year=d.year - 1, day=28)

        return shift(start), shift(end)
    raise ValueError(
        f"Unknown comparison '{comparison}'. Use 'previous_period' or"
        " 'previous_year'."
    )


async def query_search_analytics(
    site_url: str,
    start_date: str = "30daysAgo",
    end_date: str = "3daysAgo",
    dimensions: list[str] | None = None,
    search_type: str = "web",
    filters: list[dict[str, Any]] | None = None,
    aggregation_type: str = "auto",
    data_state: str = "final",
    row_limit: int = 1000,
    start_row: int = 0,
) -> dict[str, Any]:
    """Queries Search Console performance: clicks, impressions, CTR, position.

    Rows come back sorted by clicks. With no dimensions you get one row of
    totals. Data is usually two to three days behind; the most recent days
    are partial unless data_state is 'all'.

    Args:
        site_url: The property, exactly as list_sites returns it.
        start_date: YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'.
        end_date: Same formats. Defaults to '3daysAgo' (complete data).
        dimensions: Any of 'query', 'page', 'country', 'device', 'date',
          'searchAppearance' (and 'hour' with data_state 'hourly_all').
          Countries are ISO 3166-1 alpha-3 codes in lower case (e.g. 'gbr');
          devices are DESKTOP, MOBILE or TABLET.
        search_type: 'web' (default), 'image', 'video', 'news', 'discover' or
          'googleNews'. Discover and Google News don't support the query
          dimension.
        filters: Filters combined with AND, each
          {'dimension': 'query', 'operator': 'contains', 'expression': 'shoes'}.
          Operators: equals, notEquals, contains, notContains, includingRegex,
          excludingRegex (RE2 syntax; prefix '(?i)' for case-insensitive).
        aggregation_type: 'auto', 'byPage' or 'byProperty'. Totals by page
          count a URL once per result; by property count the site once.
        data_state: 'final' (complete days only), 'all' (includes fresh,
          partial data) or 'hourly_all'.
        row_limit: Rows to return, 1 to 25,000. Default 1,000.
        start_row: Zero-based offset for paging; use next_start_row from the
          previous call.

    Note: queries Google treats as anonymised are left out of rows that have
    the query dimension, so those rows add up to less than the totals.
    """
    site = utils.site_url(site_url)
    start, end = utils.date_range(start_date, end_date)
    if not 1 <= row_limit <= utils.MAX_ROW_LIMIT:
        raise ValueError(f"row_limit must be 1 to {utils.MAX_ROW_LIMIT}.")
    if start_row < 0:
        raise ValueError("start_row can't be negative.")
    dims = [utils.dimension(d) for d in dimensions or []]
    body = _body(
        start,
        end,
        dims,
        utils.search_type(search_type),
        utils.filters_to_groups(filters),
        utils.aggregation_type(aggregation_type),
        utils.data_state(data_state),
    )
    body.update(rowLimit=row_limit, startRow=start_row)
    data = await _query(site, body)
    rows = [utils.row_to_dict(dims, r) for r in data.get("rows", [])]
    has_more = len(rows) == row_limit
    result: dict[str, Any] = {
        "site_url": site,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "search_type": body["type"],
        "dimensions": dims,
        "response_aggregation_type": data.get("responseAggregationType"),
        "row_count": len(rows),
        "start_row": start_row,
        "has_more": has_more,
        "next_start_row": start_row + len(rows) if has_more else None,
        "rows": rows,
    }
    if data.get("metadata"):
        result["metadata"] = data["metadata"]
    return result


async def compare_performance(
    site_url: str,
    dimension: str | None = None,
    start_date: str = "30daysAgo",
    end_date: str = "3daysAgo",
    comparison: str = "previous_period",
    search_type: str = "web",
    filters: list[dict[str, Any]] | None = None,
    brand_terms: list[str] | None = None,
    sort_by: str = "clicks",
    descending: bool = True,
    row_limit: int = 50,
    fetch_limit: int = 5000,
) -> dict[str, Any]:
    """Compares a period with the one before it (or the year before).

    Returns totals for both periods with the change, and, with a dimension,
    the top rows (e.g. top queries or pages) with their previous values. Use
    it for "top queries this month vs last month", winners and losers, and
    brand vs non-brand trends.

    Args:
        site_url: The property, exactly as list_sites returns it.
        dimension: Optional 'query', 'page', 'country', 'device' or
          'searchAppearance'. Leave out for totals only.
        start_date: Start of the current period. Default '30daysAgo'.
        end_date: End of the current period. Default '3daysAgo'.
        comparison: 'previous_period' (same length, immediately before) or
          'previous_year' (same dates a year earlier).
        search_type: 'web' (default), 'image', 'video', 'news', 'discover' or
          'googleNews'.
        filters: Same as query_search_analytics; applied to both periods.
        brand_terms: Optional brand terms, e.g. ['acme', 'acme shoes']. Adds
          brand and non-brand totals (queries containing any term, case
          insensitive) for both periods.
        sort_by: 'clicks', 'impressions', 'ctr', 'position', 'clicks_change',
          'impressions_change' or 'position_change' (current period).
        descending: Sort order. Use False with 'clicks_change' for the
          biggest losers.
        row_limit: Rows to return after sorting. Default 50.
        fetch_limit: Rows fetched per period before joining, up to 25,000.
          Rows outside it count as zero, so raise it for long-tail work.

    Note: changes in position are current minus previous, so negative means
    better. Brand plus non-brand totals are lower than the overall total
    because Google leaves anonymised queries out of query filters; the gap is
    reported as 'anonymised_or_unmatched'.
    """
    site = utils.site_url(site_url)
    start, end = utils.date_range(start_date, end_date)
    prev_start, prev_end = _previous_period(start, end, comparison)
    stype = utils.search_type(search_type)
    groups = utils.filters_to_groups(filters)
    base_filters = groups[0]["filters"] if groups else []
    if sort_by not in _SORT_KEYS:
        raise ValueError(
            f"Unknown sort_by '{sort_by}'. Use one of: {', '.join(_SORT_KEYS)}."
        )
    if not 1 <= fetch_limit <= utils.MAX_ROW_LIMIT:
        raise ValueError(f"fetch_limit must be 1 to {utils.MAX_ROW_LIMIT}.")
    if row_limit < 1:
        raise ValueError("row_limit must be at least 1.")

    def body(s, e, dims=(), extra=()):
        flt = base_filters + list(extra)
        grp = [{"groupType": "and", "filters": flt}] if flt else []
        return _body(s, e, list(dims), stype, grp)

    periods = ((start, end), (prev_start, prev_end))
    jobs = [_totals(site, body(s, e)) for s, e in periods]
    if brand_terms:
        pattern = brand_regex(brand_terms)
        for op in ("includingRegex", "excludingRegex"):
            flt = [
                {"dimension": "query", "operator": op, "expression": pattern}
            ]
            jobs += [_totals(site, body(s, e, extra=flt)) for s, e in periods]
    dim = utils.dimension(dimension) if dimension else None
    if dim:
        jobs += [
            _fetch_rows(site, body(s, e, [dim]), fetch_limit)
            for s, e in periods
        ]
    results = await asyncio.gather(*jobs)

    cur, prev = results[0], results[1]
    out: dict[str, Any] = {
        "site_url": site,
        "search_type": stype,
        "comparison": comparison,
        "current_period": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        "previous_period": {
            "start_date": prev_start.isoformat(),
            "end_date": prev_end.isoformat(),
        },
        "totals": {
            "current": cur,
            "previous": prev,
            "change": _change(cur, prev),
        },
    }
    idx = 2
    if brand_terms:
        b_cur, b_prev, n_cur, n_prev = results[idx : idx + 4]
        idx += 4

        def rest(total, a, b):
            return {
                "clicks": total["clicks"] - a["clicks"] - b["clicks"],
                "impressions": total["impressions"]
                - a["impressions"]
                - b["impressions"],
            }

        out["brand_split"] = {
            "brand_terms": brand_terms,
            "brand": {
                "current": b_cur,
                "previous": b_prev,
                "change": _change(b_cur, b_prev),
            },
            "non_brand": {
                "current": n_cur,
                "previous": n_prev,
                "change": _change(n_cur, n_prev),
            },
            "anonymised_or_unmatched": {
                "current": rest(cur, b_cur, n_cur),
                "previous": rest(prev, b_prev, n_prev),
            },
        }
    if dim:
        out["dimension"] = dim
        out["rows"], out["rows_compared"] = _join(
            results[idx], results[idx + 1], sort_by, descending, row_limit
        )
    return out


def _join(cur_rows, prev_rows, sort_by, descending, row_limit):
    empty = utils.metrics({})
    cur = {r["keys"][0]: utils.metrics(r) for r in cur_rows}
    prev = {r["keys"][0]: utils.metrics(r) for r in prev_rows}
    joined = []
    for key in cur.keys() | prev.keys():
        c, p = cur.get(key, empty), prev.get(key, empty)
        joined.append(
            {
                "key": key,
                **c,
                "clicks_previous": p["clicks"],
                "impressions_previous": p["impressions"],
                "ctr_previous": p["ctr"],
                "position_previous": p["position"],
                "clicks_change": c["clicks"] - p["clicks"],
                "impressions_change": c["impressions"] - p["impressions"],
                "position_change": (
                    round(c["position"] - p["position"], 2)
                    if c["impressions"] and p["impressions"]
                    else None
                ),
                "new": key not in prev,
                "lost": key not in cur,
            }
        )

    def sort_key(row):
        value = row[sort_by]
        # Rows without a value (e.g. no position change) sort last.
        if value is None:
            return (1, 0)
        return (0, -value if descending else value)

    joined.sort(key=sort_key)
    return joined[:row_limit], len(joined)


def _match_filter(dim: str, value: str, match: str) -> dict[str, Any]:
    operators = {"exact": "equals", "contains": "contains"}
    if match not in operators:
        raise ValueError("match must be 'exact' or 'contains'.")
    return {"dimension": dim, "operator": operators[match], "expression": value}


async def get_queries_for_page(
    site_url: str,
    page_url: str,
    start_date: str = "30daysAgo",
    end_date: str = "3daysAgo",
    match: str = "exact",
    search_type: str = "web",
    row_limit: int = 100,
) -> dict[str, Any]:
    """Lists the search queries a page shows up for, with the page's totals.

    Args:
        site_url: The property, exactly as list_sites returns it.
        page_url: Full URL of the page. A path such as '/products/x' works for
          URL-prefix properties.
        start_date: YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'.
        end_date: Same formats. Default '3daysAgo'.
        match: 'exact' (default) or 'contains' (e.g. every URL with
          '/blogs/').
        search_type: 'web' (default), 'image', 'video' or 'news'.
        row_limit: Queries to return, up to 25,000. Default 100.

    The page totals include anonymised queries, so they're usually higher
    than the sum of the query rows.
    """
    site = utils.site_url(site_url)
    url = page_url if match == "contains" else utils.page_url(site, page_url)
    page_filter = [_match_filter("page", url, match)]
    totals, rows = await asyncio.gather(
        query_search_analytics(
            site, start_date, end_date, None, search_type, page_filter,
            "byPage", row_limit=1,
        ),
        query_search_analytics(
            site, start_date, end_date, ["query"], search_type, page_filter,
            "byPage", row_limit=row_limit,
        ),
    )  # fmt: skip
    total = totals["rows"][0] if totals["rows"] else utils.metrics({})
    return {
        "site_url": site,
        "page": url,
        "match": match,
        "start_date": rows["start_date"],
        "end_date": rows["end_date"],
        "page_totals": total,
        "query_count": rows["row_count"],
        "has_more": rows["has_more"],
        "queries": rows["rows"],
    }


async def get_pages_for_query(
    site_url: str,
    query: str,
    start_date: str = "30daysAgo",
    end_date: str = "3daysAgo",
    match: str = "exact",
    search_type: str = "web",
    row_limit: int = 100,
) -> dict[str, Any]:
    """Lists the pages that rank for a query, to spot cannibalisation.

    Several pages sharing impressions for the same query often means they
    compete with each other.

    Args:
        site_url: The property, exactly as list_sites returns it.
        query: The search query.
        start_date: YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'.
        end_date: Same formats. Default '3daysAgo'.
        match: 'exact' (default) or 'contains' (every query containing it).
        search_type: 'web' (default), 'image', 'video' or 'news'.
        row_limit: Pages to return, up to 25,000. Default 100.
    """
    site = utils.site_url(site_url)
    result = await query_search_analytics(
        site,
        start_date,
        end_date,
        ["page"],
        search_type,
        [_match_filter("query", query, match)],
        "byPage",
        row_limit=row_limit,
    )
    pages = result["rows"]
    return {
        "site_url": site,
        "query": query,
        "match": match,
        "start_date": result["start_date"],
        "end_date": result["end_date"],
        "page_count": len(pages),
        "multiple_pages_rank": len(pages) > 1,
        "has_more": result["has_more"],
        "pages": pages,
    }
