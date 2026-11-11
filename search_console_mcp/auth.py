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

"""Google credentials for the Search Console API.

Three ways to sign in, tried in this order:

1. A service account key, from SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY. Add the
   service account's email as a user on the Search Console property.
2. A user token saved by `search-console-mcp auth`, which runs Google's
   installed-app OAuth flow with a Desktop OAuth client. The token lives in
   the user's config directory (or SEARCH_CONSOLE_TOKEN_FILE) with 0600
   permissions.
3. Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS or a
   gcloud login).
"""

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Sequence

import google.auth
import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials

READ_ONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"

SERVICE_ACCOUNT_ENV_VAR = "SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY"
TOKEN_FILE_ENV_VAR = "SEARCH_CONSOLE_TOKEN_FILE"
OAUTH_CLIENT_ENV_VAR = "SEARCH_CONSOLE_OAUTH_CLIENT"

_APP_DIR_NAME = "search-console-mcp"


def scopes_for(allow_write: bool) -> list[str]:
    """Returns the OAuth scopes to request.

    The webmasters scope already covers reads, so write mode asks for it alone.
    """
    return [WRITE_SCOPE] if allow_write else [READ_ONLY_SCOPE]


def config_dir() -> Path:
    """Returns this server's directory in the user's config directory."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming"
        )
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / _APP_DIR_NAME


def token_path() -> Path:
    """Returns where the OAuth user token is read from and saved to."""
    override = os.environ.get(TOKEN_FILE_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return config_dir() / "token.json"


def save_token(path: Path, token_json: str) -> None:
    """Writes the token file atomically, readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token_json)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    if os.name != "nt":
        path.chmod(0o600)


def run_oauth_flow(
    client_secrets: Path,
    allow_write: bool = False,
    output: Path | None = None,
    port: int = 0,
    open_browser: bool = True,
) -> Path:
    """Runs the installed-app OAuth flow and saves the user token.

    Opens Google's sign-in page in the browser, waits for the redirect to a
    local port, then saves the refresh token.

    Returns:
        The path the token was saved to.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    _check_client_file(client_secrets)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets), scopes=scopes_for(allow_write)
    )
    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        prompt="consent",
        access_type="offline",
        authorization_prompt_message=(
            "Sign in to Google in your browser. If it didn't open, visit:\n{url}"
        ),
        success_message=(
            "Signed in. You can close this tab and return to your terminal."
        ),
    )
    destination = output or token_path()
    save_token(destination, creds.to_json())
    return destination


def _check_client_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"OAuth client file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} isn't valid JSON: {exc}") from exc
    if "installed" not in data:
        kind = next(iter(data), "unknown")
        raise ValueError(
            f"{path} is a '{kind}' OAuth client. Create a 'Desktop app' OAuth"
            " client in Google Cloud and download its JSON."
        )


def _signin_help(allow_write: bool) -> str:
    flag = " --allow-write" if allow_write else ""
    return (
        "Sign in once with: search-console-mcp auth --client-secrets"
        f" PATH_TO_DESKTOP_OAUTH_CLIENT.json{flag}. Or set"
        f" {SERVICE_ACCOUNT_ENV_VAR} to a service account key, or"
        " GOOGLE_APPLICATION_CREDENTIALS to Application Default Credentials."
        " See docs/credentials.md."
    )


def load_credentials(allow_write: bool) -> tuple[Any, str]:
    """Finds credentials, returning (credentials, description of the source).

    Raises:
        RuntimeError: If no credentials are available or a token lacks the
          scope that write mode needs.
    """
    scopes = scopes_for(allow_write)

    key_path = os.environ.get(SERVICE_ACCOUNT_ENV_VAR)
    if key_path:
        path = Path(key_path).expanduser()
        if not path.is_file():
            raise RuntimeError(
                f"{SERVICE_ACCOUNT_ENV_VAR} points to {path}, which doesn't"
                " exist."
            )
        creds = service_account.Credentials.from_service_account_file(
            str(path), scopes=scopes
        )
        return creds, f"service account {creds.service_account_email}"

    path = token_path()
    if path.is_file():
        info = json.loads(path.read_text(encoding="utf-8"))
        granted = set(info.get("scopes") or [])
        if allow_write and WRITE_SCOPE not in granted:
            raise RuntimeError(
                f"The token in {path} is read-only, but the server runs with"
                " --allow-write. " + _signin_help(True)
            )
        # Keep the token's own scopes: asking for a different scope on refresh
        # fails with invalid_scope.
        creds = UserCredentials.from_authorized_user_info(info)
        return creds, f"OAuth user token {path}"

    try:
        creds, _ = _adc(scopes)
    except google.auth.exceptions.DefaultCredentialsError as exc:
        raise RuntimeError(
            "No Google credentials found. " + _signin_help(allow_write)
        ) from exc
    return creds, "Application Default Credentials"


def _adc(scopes: Sequence[str]):
    # google.auth.default may run gcloud; stop it inheriting the MCP stdio
    # handles, which deadlocks on Windows.
    original_popen = subprocess.Popen

    def safe_popen(*args, **kwargs):
        if kwargs.get("stdin") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        return original_popen(*args, **kwargs)

    subprocess.Popen = safe_popen
    try:
        return google.auth.default(scopes=list(scopes))
    finally:
        subprocess.Popen = original_popen


class TokenProvider:
    """Thread-safe access-token source that loads and refreshes credentials."""

    def __init__(self, allow_write: bool = False):
        self._allow_write = allow_write
        self._lock = threading.Lock()
        self._credentials = None
        self.source = None

    def access_token(self) -> str:
        """Returns a valid access token, refreshing it if needed (blocking)."""
        with self._lock:
            if self._credentials is None:
                self._credentials, self.source = load_credentials(
                    self._allow_write
                )
            if not self._credentials.valid:
                self._credentials.refresh(Request())
            return self._credentials.token
