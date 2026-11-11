# Google Search Console MCP Server (read-only by default)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

An [MCP](https://modelcontextprotocol.io) server for
[Google Search Console](https://search.google.com/search-console), using the
[Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index)
(Search Analytics, Sites and Sitemaps) and the
[URL Inspection API](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect).
Use it from Claude, ChatGPT, Cursor, Codex, Gemini or any MCP client to report
on organic search performance, compare periods, check sitemaps and inspect how
Google indexes your pages.

- **search analytics**: clicks, impressions, CTR and position by query, page, country, device, date or search appearance, for web, image, video, news, Discover and Google News
- **period comparisons**: this period against the last one or last year, with winners and losers, and brand vs non-brand totals
- **page and query reports**: the queries a page ranks for, and the pages that compete for a query
- **URL Inspection**: index status, coverage reason, Google's canonical against yours, last crawl, rich results; one URL or a paced batch of up to 200
- **sitemaps**: status, errors and warnings; submit and delete with `--allow-write`
- **built-in Google sign-in**: no `gcloud` needed

Part of the AI Shopping Feeds MCP family: [Google Ads](https://github.com/AI-Shopping-Feeds/google-ads-mcp) · [Google Merchant Centre](https://github.com/AI-Shopping-Feeds/google-merchant-centre-mcp) · [Google Analytics](https://github.com/AI-Shopping-Feeds/google-analytics-mcp) · [YouTube Studio](https://github.com/AI-Shopping-Feeds/youtube-studio-mcp) · **Google Search Console**.

## Let your agent set it up

Paste this into Claude Code, Codex, Cursor or any agent that can read web pages and edit files:

```text
Set up the Google Search Console MCP server for me by following https://raw.githubusercontent.com/AI-Shopping-Feeds/search-console-mcp/main/skills/search-console-mcp/SKILL.md
```

The agent checks whether it's already connected, installs the runner if you
agree, asks for your OAuth client file and has you sign in to Google in your
browser, adds the server to your client's config without touching your other
servers, and checks it works by calling `list_sites`.

The same file is an [Agent Skill](https://agentskills.io), so your agent can keep it as a guide to using the tools well. For Claude Code, copy the folder into your skills:

```bash
git clone --depth 1 https://github.com/AI-Shopping-Feeds/search-console-mcp.git /tmp/search-console-mcp
cp -r /tmp/search-console-mcp/skills/search-console-mcp ~/.claude/skills/
```

Other agents that support skills load the same `skills/search-console-mcp` folder from their own skills directory.

## What it can do

12 tools: 10 read tools, plus 2 write tools when the server runs with `--allow-write`.

### Read

| Area | Tools |
|---|---|
| Properties | `list_sites`, `get_site` |
| Search analytics | `query_search_analytics` (any dimensions, filters and search type, up to 25,000 rows a page) |
| Reports | `compare_performance` (period over period or year over year, with brand split), `get_queries_for_page`, `get_pages_for_query` |
| Sitemaps | `list_sitemaps`, `get_sitemap` |
| URL Inspection | `inspect_url`, `batch_inspect_urls` (up to 200 URLs, paced to the quota) |

### Write

Only listed when the server starts with `--allow-write`.

| Area | Tools |
|---|---|
| Sitemaps | `submit_sitemap`, `delete_sitemap` |

Nothing else writes. Search Console's API can't change settings, remove URLs or request indexing.

### Safety

- The server is read-only unless it starts with `--allow-write`. Without it, it asks Google only for the `webmasters.readonly` scope.
- Tools carry MCP titles and annotations; `delete_sitemap` is marked destructive so clients can ask first.
- `batch_inspect_urls` keeps to Google's URL Inspection quota (600 a minute and 2,000 a day per property) and stops cleanly if Google says the quota is used up.
- The sign-in token is saved with `0600` permissions in your user config directory, never in the project.

## Supported clients

| Client | Works? | How |
|---|---|---|
| Claude Code | ✅ | Local server, `claude mcp add` (below) |
| Claude Desktop | ✅ | Local server, `claude_desktop_config.json` |
| Cursor | ✅ | Local server, `~/.cursor/mcp.json` |
| VS Code (Copilot) | ✅ | Local server, `.vscode/mcp.json` (uses `servers` instead of `mcpServers`) |
| Codex | ✅ | Local server, `~/.codex/config.toml` |
| Gemini CLI | ✅ | Local server, `~/.gemini/settings.json` |
| Windsurf and other local clients | ✅ | Local server, same JSON entry |
| Claude.ai and ChatGPT | ❌ not yet | These need a remote server with sign-in. This server only runs locally (stdio). Use a local client. |

## Setup

### 1. Install uv

The server runs with `uvx` from [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS) and needs Python 3.10 or later. It installs straight
from this repository:

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git search-console-mcp
```

Add `--allow-write` after `search-console-mcp` to enable the sitemap write tools (here and in the client configs below). Pin a release by adding `@vX.Y.Z` to the URL.

### 2. Sign in to Google

1. **Google Cloud project:** enable the
   [Google Search Console API](https://console.cloud.google.com/apis/library/searchconsole.googleapis.com)
   and create an OAuth client of type **Desktop app**. Download its JSON.
2. **Sign in** as a Google user with access to your properties. Your browser
   opens Google's sign-in page; the token is saved to
   `~/.config/search-console-mcp/token.json` (Windows:
   `%APPDATA%\search-console-mcp\token.json`). Add `--allow-write` for the
   write scope.

   ```shell
   uvx --from git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git \
     search-console-mcp auth --client-secrets /abs/path/client_secret.json
   ```

Service accounts and Application Default Credentials also work: [docs/credentials.md](docs/credentials.md).

### 3. Add it to your client

Keep your other servers in each file. With the built-in sign-in, no
environment variables are needed.

**Claude Code**

```bash
claude mcp add --scope user search-console \
  -- uvx --from git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git search-console-mcp
```

**Claude Desktop, Cursor, Gemini CLI, Windsurf** (and VS Code under `servers`)

```json
{
  "mcpServers": {
    "search-console": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git", "search-console-mcp"]
    }
  }
}
```

Desktop apps often can't find `uvx` on the PATH; if the server doesn't start, use
the full path from `which uvx` as the `command`.

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.search-console]
command = "uvx"
args = ["--from", "git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git", "search-console-mcp"]
```

### 4. Check it works

Restart or reload the client, then ask it to "list my Search Console properties". It should
call `list_sites`.

## Configuration

| Variable | Required | What it does |
|---|---|---|
| `SEARCH_CONSOLE_TOKEN_FILE` | no | Token saved by `search-console-mcp auth`. Default: your config directory (see above) |
| `SEARCH_CONSOLE_OAUTH_CLIENT` | no | Desktop OAuth client JSON for `search-console-mcp auth`, instead of `--client-secrets` |
| `SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY` | no | Service account key; used instead of the token when set |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | Application Default Credentials; used when there's no key or token |
| `--allow-write` (flag) | no | Enables `submit_sitemap` and `delete_sitemap`; needs the `webmasters` scope |
| `--tools` (flag) or `SEARCH_CONSOLE_MCP_TOOL_GROUPS` | no | Comma-separated tool groups to register, e.g. `--tools analytics,inspection`. The flag wins over the variable. Default: all groups |

Tool groups keep the tool list short when you only need part of it. `list_sites` and `get_site` are always registered, and write tools still need `--allow-write`. An unknown group stops the server at startup.

| Group | Read tools | Write tools (`--allow-write`) |
|---|---|---|
| `analytics` | `query_search_analytics`, `compare_performance`, `get_queries_for_page`, `get_pages_for_query` | none |
| `sitemaps` | `list_sitemaps`, `get_sitemap` | `submit_sitemap`, `delete_sitemap` |
| `inspection` | `inspect_url`, `batch_inspect_urls` | none |

## Example prompts

```text
Compare the last 3 months with the 3 months before: clicks, impressions, CTR and position, split into brand and non-brand for "example".
```

```text
Which queries lost the most clicks this month compared with last month?
```

```text
Which pages compete for "running shoes", and which queries does https://www.example.com/collections/shoes rank for?
```

```text
Inspect the home page and these product URLs. Are any not indexed, or is Google choosing a different canonical?
```

## More docs

- [Credentials](docs/credentials.md): the built-in sign-in, service accounts and Application Default Credentials
- [Agent skill](skills/search-console-mcp/SKILL.md): setup and usage guide for agents

## Contributing and licence

Issues and pull requests are welcome on [this repository](https://github.com/AI-Shopping-Feeds/search-console-mcp). See
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [Apache License 2.0](LICENSE).

- The server sends your Search Console data to the agent or LLM you connect it to.

---

Try out [AI Shopping Feeds](https://aishoppingfeeds.com/), our shopping feed management tool (including Google Shopping feeds).
