#!/bin/sh
# What the OpenRC service runs (as user mcp-sofabaton). Loads the environment
# file, then replaces itself with the server, so supervise-daemon watches the
# server process itself. Every Sofabaton setting is an environment variable, so
# give it a subcommand to run that with exactly the service's settings:
#   su -s /bin/sh mcp-sofabaton -c '/opt/mcp-server-sofabaton/run.sh doctor'
#   su -s /bin/sh mcp-sofabaton -c '/opt/mcp-server-sofabaton/run.sh call get_status'
# With no arguments it does what the service does: serve --http.
set -eu
ENV_FILE="${MCP_SOFABATON_ENV:-/etc/mcp-server-sofabaton/env}"
if [ -r "$ENV_FILE" ]; then
	set -a  # export every variable the file sets
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
fi
BIN=/opt/mcp-server-sofabaton/venv/bin/mcp-server-sofabaton
if [ "$#" -gt 0 ]; then
	exec "$BIN" "$@"
fi
# MCP_SERVE_ARGS is split into words on purpose (e.g. "--dry-run --no-redact")
# shellcheck disable=SC2086
exec "$BIN" serve --http ${MCP_SERVE_ARGS:-}
