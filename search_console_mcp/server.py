#!/usr/bin/env python

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

"""Entry point: `search-console-mcp` runs the server, `... auth` signs in."""

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

import search_console_mcp.coordinator as coordinator
from search_console_mcp import auth

TOOL_GROUPS_ENV_VAR = "SEARCH_CONSOLE_MCP_TOOL_GROUPS"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search-console-mcp",
        description=(
            "Google Search Console MCP Server. Run `search-console-mcp auth"
            " --help` to sign in."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {coordinator.SERVER_VERSION}",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help=(
            "Enable submit_sitemap and delete_sitemap (requires the "
            "https://www.googleapis.com/auth/webmasters scope)."
        ),
    )
    parser.add_argument(
        "--tools",
        metavar="GROUPS",
        default=None,
        help=(
            "Comma-separated tool groups to register: "
            f"{', '.join(coordinator.TOOL_GROUPS)}. Defaults to all groups, "
            f"or to ${TOOL_GROUPS_ENV_VAR} when set. list_sites and get_site "
            "are always registered."
        ),
    )
    return parser


def _build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search-console-mcp auth",
        description=(
            "Sign in to Google in your browser and save a token for the"
            " server. Uses a Desktop OAuth client from a Google Cloud project"
            " with the Search Console API enabled."
        ),
    )
    parser.add_argument(
        "--client-secrets",
        metavar="PATH",
        default=os.environ.get(auth.OAUTH_CLIENT_ENV_VAR),
        help=(
            "Desktop OAuth client JSON downloaded from Google Cloud. Defaults"
            f" to ${auth.OAUTH_CLIENT_ENV_VAR}."
        ),
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Request the webmasters scope (needed for --allow-write).",
    )
    parser.add_argument(
        "--token-file",
        metavar="PATH",
        default=None,
        help=(
            f"Where to save the token. Defaults to ${auth.TOKEN_FILE_ENV_VAR}"
            f" or {auth.token_path()}."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port for Google's redirect. 0 picks a free port.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the sign-in link instead of opening a browser.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses server arguments, resolving tool groups from --tools or env.

    Exits with a usage error if a group is unknown.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.tools is not None:
        source, value = "--tools", args.tools
    else:
        source, value = TOOL_GROUPS_ENV_VAR, os.environ.get(TOOL_GROUPS_ENV_VAR)
    try:
        args.tool_groups = coordinator.parse_tool_groups(value)
    except ValueError as exc:
        parser.error(f"{source}: {exc}")
    return args


def run_auth(argv: Sequence[str]) -> None:
    """Runs the OAuth sign-in flow and saves the token."""
    parser = _build_auth_parser()
    args = parser.parse_args(argv)
    if not args.client_secrets:
        parser.error(
            f"--client-secrets (or ${auth.OAUTH_CLIENT_ENV_VAR}) is required."
        )
    try:
        saved = auth.run_oauth_flow(
            Path(args.client_secrets).expanduser(),
            allow_write=args.allow_write,
            output=(
                Path(args.token_file).expanduser() if args.token_file else None
            ),
            port=args.port,
            open_browser=not args.no_browser,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    scope = "read + write" if args.allow_write else "read-only"
    print(f"Saved {scope} token to {saved} (permissions 0600).")
    if args.token_file:
        print(
            f"Set {auth.TOKEN_FILE_ENV_VAR}={saved} in the server's"
            " environment so it finds this token."
        )


def run_server(argv: Sequence[str] | None = None):
    """Runs the MCP server over standard I/O."""
    args = parse_args(argv)
    app = coordinator.create_server(
        allow_write=args.allow_write, tool_groups=args.tool_groups
    )
    print(
        f"Starting MCP Stdio Server: {app.name} {app.version}"
        f" (tool groups: {', '.join(sorted(args.tool_groups))};"
        f" write tools {'on' if args.allow_write else 'off'})",
        file=sys.stderr,
    )
    app.run("stdio")


def main(argv: Sequence[str] | None = None):
    """Console entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "auth":
        run_auth(argv[1:])
        return
    try:
        run_server(argv)
    except KeyboardInterrupt:
        print("\nMCP Server (stdio) stopped by user.", file=sys.stderr)


if __name__ == "__main__":
    main()
