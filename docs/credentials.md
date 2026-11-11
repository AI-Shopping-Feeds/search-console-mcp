# Search Console credentials

The server needs a Google identity with access to your Search Console
properties. There are three ways to provide one. It uses the first it finds,
in this order:

1. [A service account key](#service-account), from
   `SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY`.
2. [A user token from the built-in sign-in](#built-in-sign-in-recommended),
   saved by `search-console-mcp auth`.
3. [Application Default Credentials](#application-default-credentials), from
   `GOOGLE_APPLICATION_CREDENTIALS` or a `gcloud` login.

## Scopes

| Mode | Scope |
|---|---|
| Read-only (default) | `https://www.googleapis.com/auth/webmasters.readonly` |
| `--allow-write` (submit and delete sitemaps) | `https://www.googleapis.com/auth/webmasters` |

The `webmasters` scope includes everything `webmasters.readonly` allows, so a
read + write token also works when the server runs read-only. Write tools
also need Owner or Full permission on the property.

## Enable the API

In a [Google Cloud project](https://console.cloud.google.com/), enable the
[Google Search Console API](https://console.cloud.google.com/apis/library/searchconsole.googleapis.com).
The same project can hold OAuth clients for other Google APIs, such as the
Google Analytics MCP server's.

## Built-in sign-in (recommended)

No `gcloud` needed.

1. **Create a Desktop OAuth client.** In the Cloud console, open
   [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients),
   choose **Create client**, application type **Desktop app**, and download
   the JSON. If the consent screen is in *Testing*, add your Google account
   as a test user. (An existing Desktop client works too, as long as its
   project has the Search Console API enabled.)
2. **Sign in.** This opens Google's sign-in page in your browser; sign in as
   a user with access to your properties.

   ```shell
   uvx --from git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git \
     search-console-mcp auth --client-secrets /abs/path/client_secret.json
   ```

   Add `--allow-write` to request the `webmasters` scope for write tools.
   Use `--no-browser` on a machine without a browser; it prints the link
   instead. The client file can also come from `SEARCH_CONSOLE_OAUTH_CLIENT`.

3. **Done.** The refresh token is saved with `0600` permissions (directory
   `0700`) to:

   | OS | Path |
   |---|---|
   | macOS, Linux | `$XDG_CONFIG_HOME/search-console-mcp/token.json`, by default `~/.config/search-console-mcp/token.json` |
   | Windows | `%APPDATA%\search-console-mcp\token.json` |

   The server finds it there; no environment variable is needed. To keep
   tokens for several Google accounts, save each with `--token-file` and set
   `SEARCH_CONSOLE_TOKEN_FILE` per server entry.

To sign out, delete the token file and remove the app's access at
[myaccount.google.com/connections](https://myaccount.google.com/connections).

If the consent screen's publishing status is *Testing*, Google expires refresh
tokens after seven days; run `auth` again, or publish the app (internal apps
in a Workspace organisation don't expire).

## Service account

Good for scheduled jobs and shared machines.

1. Create a service account in the project and download a JSON key.
2. In Search Console, open **Settings → Users and permissions** for the
   property and add the service account's email (`...@...iam.gserviceaccount.com`)
   as a user. Restricted is enough for reads; writes need Full or Owner.
3. Set `SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY=/abs/path/key.json` in the
   server's environment.

The service account sees only properties it has been added to.

## Application Default Credentials

If you already use `gcloud`, ADC works without the built-in sign-in:

```shell
gcloud auth application-default login \
  --scopes https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/cloud-platform \
  --client-id-file=PATH_TO_DESKTOP_OAUTH_CLIENT_JSON
```

Swap `webmasters.readonly` for `webmasters` for write tools. Set
`GOOGLE_APPLICATION_CREDENTIALS` to the file it prints, or leave it unset to
use gcloud's default location. ADC is only used when there is no service
account key and no saved token.

## Keep credentials private

The OAuth client JSON, token and service account key all grant access to your
Search Console data. Don't commit them to git, paste them into chat or share
them. This repository's `.gitignore` ignores the usual file names.
