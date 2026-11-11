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

"""A fake Search Console API for tests, served through httpx.MockTransport."""

import json
from typing import Any, Callable

import httpx

from search_console_mcp import client as sc_client


class FakeAPI:
    """Records requests and answers them with a handler function.

    The handler gets (method, path, json_body, params) and returns either a
    dict (200 response) or (status_code, dict).
    """

    def __init__(self, handler: Callable[..., Any]):
        self.handler = handler
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        record = {
            "method": request.method,
            "path": request.url.raw_path.decode().split("?")[0],
            "json": body,
            "params": dict(request.url.params),
            "headers": dict(request.headers),
        }
        self.requests.append(record)
        result = self.handler(
            record["method"], record["path"], body, record["params"]
        )
        status, payload = result if isinstance(result, tuple) else (200, result)
        return httpx.Response(status, json=payload)

    def install(
        self, allow_write: bool = False
    ) -> sc_client.SearchConsoleClient:
        """Points the shared client at this fake."""

        async def no_sleep(_):
            return None

        return sc_client.configure(
            allow_write=allow_write,
            token_provider=lambda: "test-token",
            transport=httpx.MockTransport(self),
            sleep=no_sleep,
        )


def analytics_rows(*rows: tuple[list[str], int, int, float, float]):
    return {
        "rows": [
            {
                "keys": keys,
                "clicks": clicks,
                "impressions": impressions,
                "ctr": ctr,
                "position": position,
            }
            for keys, clicks, impressions, ctr, position in rows
        ],
        "responseAggregationType": "byProperty",
    }
