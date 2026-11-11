# How to contribute

Issues and pull requests are welcome.

## Code style

The code follows [PEP 8](https://www.python.org/dev/peps/pep-0008/) with an
80-character line width, formatted with [`black`](https://github.com/psf/black).

```shell
pip install -e ".[dev]"
nox -s format
```

## Tests

Add or update tests in `tests/`. They run against a fake API, so they need no
Google credentials.

```shell
nox -s tests*
```

Or, in a virtual environment with the package installed:

```shell
python -m unittest discover -s tests -p "*_test.py"
```

## Try a branch in your client

Point your MCP client at a local checkout:

```bash
claude mcp add --scope user search-console-dev \
  -- uvx --refresh --from /abs/path/to/search-console-mcp search-console-mcp
```

Or at a pushed branch with
`git+https://github.com/AI-Shopping-Feeds/search-console-mcp.git@BRANCH`.

Never commit OAuth client files, tokens, service account keys or real
Search Console data. Use `example.com` in tests and docs.
