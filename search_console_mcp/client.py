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

"""HTTP client for the Search Console API (webmasters v3 and v1)."""

import asyncio
import importlib.metadata
from typing import Any, Callable
from urllib.parse import quote

import httpx

from search_console_mcp.auth import TokenProvider

API_ROOT = "https://searchconsole.googleapis.com"
WEBMASTERS = f"{API_ROOT}/webmasters/v3"
URL_INSPECTION = f"{API_ROOT}/v1/urlInspection/index:inspect"

_RETRY_STATUSES = {500, 502, 503, 504}
_RETRY_DELAYS = (0.5, 1.5, 4.0)


def _user_agent() -> str:
    try:
        version = importlib.metadata.version("search-console-mcp")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return f"search-console-mcp/{version}"


class SearchConsoleAPIError(RuntimeError):
    """An error response from the Search Console API."""

    def __init__(self, status_code: int, message: str, reason: str = ""):
        self.status_code = status_code
        self.reason = reason
        super().__init__(message)


class QuotaExceededError(SearchConsoleAPIError):
    """A 429 response: a per-minute or per-day quota is used up."""


def _hint(status_code: int, message: str) -> str:
    lowered = message.lower()
    if "has not been used" in lowered or "service_disabled" in lowered:
        return (
            " Enable the Google Search Console API in the Google Cloud project"
            " that owns your OAuth client or service account."
        )
    if status_code == 403:
        return (
            " The signed-in Google account (or service account) has no access"
            " to this property, or needs Owner or Full permission for this"
            " action. Call list_sites to see the exact property strings, such"
            " as 'sc-domain:example.com' or 'https://www.example.com/'."
        )
    if status_code == 404:
        return (
            " Check the property string with list_sites; URL-prefix properties"
            " end with '/'."
        )
    if status_code == 429:
        return (
            " Quota used up. URL Inspection allows 2,000 calls a day and 600 a"
            " minute per property; Search Analytics has per-minute limits."
        )
    return ""


def encode_site(site_url: str) -> str:
    """Encodes a property string for use as one path segment."""
    return quote(site_url, safe="")


class SearchConsoleClient:
    """Async client that signs requests with Google credentials."""

    def __init__(
        self,
        allow_write: bool = False,
        token_provider: Callable[[], str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self._tokens = TokenProvider(allow_write)
        self._get_token = token_provider or self._tokens.access_token
        self._transport = transport
        self._sleep = sleep

    async def request(
        self,
        method: str,
        url: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends a request and returns the decoded JSON body ({} if empty).

        Retries server errors a few times.

        Raises:
            SearchConsoleAPIError: For an error response.
        """
        token = await asyncio.to_thread(self._get_token)
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": _user_agent(),
        }
        async with httpx.AsyncClient(
            transport=self._transport, timeout=60.0
        ) as http:
            for attempt in range(len(_RETRY_DELAYS) + 1):
                response = await http.request(
                    method, url, json=json, params=params, headers=headers
                )
                if response.status_code in _RETRY_STATUSES and attempt < len(
                    _RETRY_DELAYS
                ):
                    await self._sleep(_RETRY_DELAYS[attempt])
                    continue
                break
        if response.is_success:
            return response.json() if response.content else {}
        raise _error_from(response)


def _error_from(response: httpx.Response) -> SearchConsoleAPIError:
    status = response.status_code
    message, reason = response.text[:500], ""
    try:
        error = response.json().get("error", {})
        message = error.get("message") or message
        reason = error.get("status", "")
    except ValueError:
        pass
    text = f"Search Console API error {status}"
    if reason:
        text += f" {reason}"
    text += f": {message}{_hint(status, message)}"
    cls = QuotaExceededError if status == 429 else SearchConsoleAPIError
    return cls(status, text, reason)


_client: SearchConsoleClient | None = None


def configure(allow_write: bool = False, **kwargs: Any) -> SearchConsoleClient:
    """Creates the shared client the tools use."""
    global _client
    _client = SearchConsoleClient(allow_write=allow_write, **kwargs)
    return _client


def get_client() -> SearchConsoleClient:
    """Returns the shared client, creating a read-only one if needed."""
    global _client
    if _client is None:
        _client = SearchConsoleClient()
    return _client
