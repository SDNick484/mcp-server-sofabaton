#!/bin/sh
# CI: install and run the service in a stock python:3.12-alpine container,
# against a simulated X2 on both paths (a fake sofabaton-x-server, and the
# in-package MQTT broker with a fake X2). Proves the dependencies install on
# musl (pydantic-core, cryptography, paho-mqtt under aiomqtt), the install
# layout and permissions, that OpenRC starts and stops it, that run.sh gives
# subcommands the service's environment, and that an MCP call over HTTP
# reaches the hub on both paths.
#   docker run --rm -v "$PWD:/src" -w /src python:3.12-alpine sh deploy/alpine/smoke-test.sh
set -eux

# On failure, say where as a GitHub annotation (the job log isn't always at
# hand; annotations are), with the end of the service log if there is one.
report() {
	status=$?
	if [ "$status" != 0 ]; then
		tail_log=$(tail -n 30 "${STEP_LOG:-/var/log/mcp-server-sofabaton/server.log}" 2>/dev/null | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')
		echo "::error title=Alpine smoke test failed (exit $status)::last step: ${STEP:-?}%0A$tail_log"
	fi
}
trap report EXIT

STEP='install'
apk add --no-cache openrc curl >/dev/null
sh deploy/alpine/install.sh /src

STEP='upgrade'
# Re-running install.sh must replace the code even though the version number hasn't changed
# (pip would otherwise keep the installed copy). Install a copy with a marker, then look for it.
cp -r /src /tmp/src2
echo '# smoke-test upgrade marker' >>/tmp/src2/src/sofabaton_mcp/__init__.py
sh /tmp/src2/deploy/alpine/install.sh /tmp/src2
grep -q 'smoke-test upgrade marker' /opt/mcp-server-sofabaton/venv/lib/python3*/site-packages/sofabaton_mcp/__init__.py

STEP='pytest on musl'
# The full test suite on musl, from the installed venv
/opt/mcp-server-sofabaton/venv/bin/pip install --quiet "/src[dev]"
STEP_LOG=/tmp/pytest.log
cd /src
/opt/mcp-server-sofabaton/venv/bin/pytest -q -p no:cacheprovider -rf >"$STEP_LOG" 2>&1 || { cat "$STEP_LOG"; false; }
tail -n 3 "$STEP_LOG"
cd /
STEP_LOG=''

ETC=/etc/mcp-server-sofabaton
RUN=/opt/mcp-server-sofabaton/run.sh
BIN=/opt/mcp-server-sofabaton/venv/bin/mcp-server-sofabaton
STEP='simulator'
# A simulated X2: fake sofabaton-x-server on 8480, broker + fake X2 on 1883.
# It writes the matching SOFABATON_* lines, which go into the service's env file.
$BIN simulate --model X2 --no-prompt --port 8480 --mqtt-port 1883 --write-env /tmp/sim.env >/tmp/sim.log 2>&1 &
for _ in $(seq 100); do grep -q SOFABATON_MQTT_MAC /tmp/sim.env 2>/dev/null && break; sleep 0.1; done
cat /tmp/sim.env
sed 's/^export //' /tmp/sim.env >>"$ETC/env"

STEP='run.sh doctor'
# A subcommand through run.sh, as the service user: same environment as the service
su -s /bin/sh mcp-sofabaton -c "$RUN doctor --timeout 5"

STEP='service start'
# OpenRC in a container: it needs to believe it has booted, and that the
# network (which the service `need`s) is provided by the host
printf 'rc_sys="docker"\nrc_provide="loopback net"\n' >>/etc/rc.conf
mkdir -p /run/openrc && touch /run/openrc/softlevel
rc-service mcp-server-sofabaton start
for _ in $(seq 50); do curl -fsS http://127.0.0.1:8714/healthz && break; sleep 0.2; done
rc-service mcp-server-sofabaton status

STEP='service user'
# The process runs as the service user, not root. (busybox ps truncates user
# names to 8 characters, so ask /proc for the owner of the server's process.)
# shellcheck disable=SC2009
pid=$(ps -o pid,args | grep '[m]cp-server-sofabaton serve' | awk 'NR==1 {print $1}')
[ -n "$pid" ] && [ "$(stat -c %U "/proc/$pid")" = mcp-sofabaton ]
STEP='file modes'
[ "$(stat -c '%a %U:%G' $ETC/env)" = "640 root:mcp-sofabaton" ]
[ "$(stat -c '%a %U' /var/lib/mcp-server-sofabaton)" = "700 mcp-sofabaton" ]

STEP='mcp over http'
# A real MCP session against the service: initialize, then get_status, which
# must show the X2 on the server path with the broker connected (structuredContent
# is compact JSON, so the patterns have no spaces).
URL=http://127.0.0.1:8714/sofabaton/mcp
H='-H Content-Type:application/json -H Accept:application/json,text/event-stream'
# shellcheck disable=SC2086
SID=$(curl -fsS -D - -o /dev/null $H "$URL" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
	| tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id" {print $2}')
[ -n "$SID" ]
# shellcheck disable=SC2086
curl -fsS $H -H "mcp-session-id: $SID" "$URL" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
ok=no
for _ in $(seq 30); do
	# shellcheck disable=SC2086
	out=$(curl -fsS $H -H "mcp-session-id: $SID" "$URL" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_status","arguments":{}}}')
	if echo "$out" | grep -q '"model":"X2"' && echo "$out" | grep -q '"via":"server"' && echo "$out" | grep -q '"connected":true'; then
		ok=yes
		break
	fi
	sleep 0.5
done
echo "$out" | tail -c 900
[ "$ok" = yes ]

STEP='service stop'
rc-service mcp-server-sofabaton stop
cat /var/log/mcp-server-sofabaton/server.log
echo "Alpine smoke test passed"
