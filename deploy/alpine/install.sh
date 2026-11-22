#!/bin/sh
# Install mcp-server-sofabaton as an OpenRC service on Alpine (e.g. a Proxmox LXC).
# Same layout as mcp-server-webos's and -harmony's deploy/alpine, so they share an LXC.
#
#   sh deploy/alpine/install.sh [SOURCE]      # SOURCE: a checkout dir or a pip spec
#
# Layout (each part owned so the service can read its settings and change
# nothing else):
#   /opt/mcp-server-sofabaton/venv     the code                           root:root               0755
#   /opt/mcp-server-sofabaton/run.sh   the service command                root:root               0755
#   /opt/mcp-server-sofabaton/COMMIT   the installed commit, if any       root:root               0644
#   /etc/mcp-server-sofabaton/env      every setting (SOFABATON_*,        root:mcp-sofabaton      0640
#                                        HTTP and Access)
#   /etc/mcp-server-sofabaton/mqtt-password   the broker password, if any root:mcp-sofabaton     0640
#   /var/lib/mcp-server-sofabaton/     the service's working dir          mcp-sofabaton           0700
#                                        (doctor --dump writes here)
#   /var/log/mcp-server-sofabaton/     server.log                         mcp-sofabaton           0750
#   /etc/init.d/mcp-server-sofabaton   the OpenRC script
# Unlike the siblings, Sofabaton keeps no state of its own: every setting is an
# environment variable, so all of them live in the env file, and run.sh runs any
# subcommand with exactly that environment (run.sh doctor, run.sh check).
# Re-running upgrades the code and keeps the settings.
# COMMIT (the SHA installed, and this repo) is what /healthz and /sofabaton/mcp/healthz report; the
# default SOURCE is pinned to main's current commit first, so the stamp names what pip installed.
set -eu

SRC="${1:-git+https://github.com/SDNick484/mcp-server-sofabaton}"
USER_NAME=mcp-sofabaton
PREFIX=/opt/mcp-server-sofabaton
ETC=/etc/mcp-server-sofabaton
STATE=/var/lib/mcp-server-sofabaton
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

# python3 and its venv module; git for the default git+https source. Every
# compiled dependency ships a musl wheel, so no compiler is needed (the smoke
# test proves it in a stock Alpine container).
apk add --no-cache python3 py3-pip openrc git >/dev/null

if ! id "$USER_NAME" >/dev/null 2>&1; then
	addgroup -S "$USER_NAME"
	adduser -S -D -H -h "$STATE" -s /sbin/nologin -G "$USER_NAME" "$USER_NAME"
fi

# Which commit SOURCE is, worked out before pip (commit.sh, shared with the sibling servers)
. "$HERE/commit.sh"
REPO=SDNick484/mcp-server-sofabaton
pinned=$(commit_source "$SRC" "$REPO")
SRC=$(printf '%s\n' "$pinned" | sed -n 1p)
COMMIT=$(printf '%s\n' "$pinned" | sed -n 2p)

# A git+https URL (pinned to a commit above) or a checkout directory; pip takes either as it is. MQTT
# support (aiomqtt) is a regular dependency, so there's no extra to add.
SPEC="$SRC"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$SPEC"
# Every commit carries the same version number, so pip calls an installed copy "already satisfied"
# and keeps it: re-running this script would upgrade nothing. Reinstall the package itself (not its
# dependencies) so it always gets the code from SOURCE.
"$PREFIX/venv/bin/pip" install --quiet --force-reinstall --no-deps "$SPEC"
# Only now that pip succeeded (set -e stops before this otherwise), so COMMIT never names code that isn't installed
write_commit "$PREFIX" "$COMMIT" "$REPO"
install -m 0755 "$HERE/run.sh" "$PREFIX/run.sh"

install -d -m 0750 -o root -g "$USER_NAME" "$ETC"
[ -e "$ETC/env" ] || install -m 0640 -o root -g "$USER_NAME" "$HERE/env.example" "$ETC/env"
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$STATE"
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" /var/log/mcp-server-sofabaton
install -m 0755 "$HERE/mcp-server-sofabaton.initd" /etc/init.d/mcp-server-sofabaton

AS_SERVICE="su -s /bin/sh $USER_NAME -c"
echo "Installed $("$PREFIX/venv/bin/mcp-server-sofabaton" --version)."
echo "Next: set SOFABATON_URL and/or SOFABATON_MQTT_* in $ETC/env (the broker password in"
echo "$ETC/mqtt-password, 0640 root:$USER_NAME), check as the service user, then start:"
echo "  $AS_SERVICE '$PREFIX/run.sh doctor'"
echo "  $AS_SERVICE '$PREFIX/run.sh check'"
echo "  rc-update add mcp-server-sofabaton default && rc-service mcp-server-sofabaton start"
