# Contributing

The most useful contribution right now is a **hardware report**: run
[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) (or just try the tools) on your hub and
[tell us what happened](https://github.com/SDNick484/mcp-server-sofabaton/issues/new?template=hardware_report.yml).
Most of what the server assumes about Sofabaton hubs has only run against a simulator.

## Working on the code

```sh
git clone https://github.com/SDNick484/mcp-server-sofabaton.git && cd mcp-server-sofabaton
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check . && ruff format --check . && mypy
mcp-server-sofabaton simulate      # a fake hub to point the server at, no hardware needed
```

The rules the code follows (no sofabaton-x-server token, only the MQTT topics in `protocol.py`,
every unconfirmed protocol detail recorded as an assumption, ...) are in [CLAUDE.md](CLAUDE.md).
`remote.py`, `logsafe.py`, `tasks.py` and their tests are shared with sibling servers and come from
[mcp-server-shared](https://github.com/SDNick484/mcp-server-shared); change them there.

## Releasing

1. Bump `__version__` in `src/sofabaton_mcp/__init__.py` (`pyproject.toml` reads it from there) and
   add a `## [x.y.z]` section to [CHANGELOG.md](CHANGELOG.md). Merge that to `main`.
2. Tag the merge commit and push the tag:

   ```sh
   git checkout main && git pull
   git tag -a v0.2.0 -m v0.2.0 && git push origin v0.2.0
   ```

3. `.github/workflows/release.yml` checks the tag against the version and the changelog, builds,
   publishes to PyPI and creates the GitHub release with the changelog section as its notes.

### One-time setup for PyPI (trusted publishing, no token)

1. Make a [PyPI](https://pypi.org/account/register/) account with two-factor authentication.
2. Account → Publishing → **Add a new pending publisher** (GitHub): project
   `mcp-server-sofabaton`, owner `SDNick484`, repository `mcp-server-sofabaton`, workflow
   `release.yml`, environment `pypi`. "Pending" means the project is created by its first upload.
3. In the GitHub repo: Settings → Environments → **New environment** `pypi`. Optionally add
   yourself as a required reviewer, so every publish waits for your click.

To rehearse first, do the same on [TestPyPI](https://test.pypi.org) and point a copy of the
publish step at it with `repository-url: https://test.pypi.org/legacy/`.
