# Changelog

## 0.1.0

First release.

- Read tools: `list_sites`, `get_site`, `query_search_analytics`, `compare_performance` (period over period or year over year, with brand split), `get_queries_for_page`, `get_pages_for_query`, `list_sitemaps`, `get_sitemap`, `inspect_url` and `batch_inspect_urls` (paced to the URL Inspection quota).
- Write tools with `--allow-write`: `submit_sitemap` and `delete_sitemap`.
- Built-in Google sign-in (`search-console-mcp auth`), plus service accounts and Application Default Credentials.
- Tool groups (`--tools`, `SEARCH_CONSOLE_MCP_TOOL_GROUPS`), MCP titles and annotations, and an Agent Skill.
