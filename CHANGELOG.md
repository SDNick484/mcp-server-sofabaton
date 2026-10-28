# Changelog

## [Unreleased]

## [0.3.0] - 2026-10-27

### Added

- `find_remote` without sofabaton-x-server (X2, MQTT-only setups): with `SOFABATON_HUB_IP` set, it opens a
  short session on the hub's own TCP protocol (`CALL_ME` on UDP 8102, the hub connects back on
  `SOFABATON_DIRECT_PORT`, default 8200), checks the banner says X2, sends the one find-remote frame and hangs
  up. The hub doesn't acknowledge it, so the result says the beep is the only confirmation. Works only while
  the Sofabaton app is closed; otherwise it times out with that advice. Seen working on an X2 on 2026-10-27.
  `get_status` lists `find_remote` and a matching limitation; `doctor` checks the session without the
  find-remote frame, so it stays quiet. New assumptions `S-DIRECT-CALL-ME`, `S-DIRECT-BANNER`,
  `S-DIRECT-FIND` (hardware-verified), `S-DIRECT-LINGER` and `S-DIRECT-EXCLUSIVE`; HARDWARE_VALIDATION.md
  step 8a. `SOFABATON_HUB_IP` is ignored, with a config problem, when a server is configured.
- `serve --http`: `/healthz` reports whether the hub is reachable (200 with the hub name and path, `server`
  or `mqtt`) or not (503), instead of always 200 while the process is up. It reads the current state only:
  it never waits for MQTT or triggers a reconnect. The container smoke test checks it.
- `doctor --json` carries the shared registry summary under `assumptions` (every count, and every
  unverified, contradicted and hardware-verified assumption in full), the same block as the sibling servers.

### Fixed

- MQTT: a connection that dropped while idle is brought back before a command is sent, instead of the call
  failing until the next retry (up to a minute later). `MqttHub.ensure_connected()` wakes the reconnect loop
  and waits up to 2 s; a rejected login still fails at once and isn't retried.
- MQTT: a failed publish now says so ("MQTT publish failed (...)"), drops the connection and reconnects
  immediately, so it can't leave the hub marked disconnected over a connection that is actually up.
- A repeated press or command that fails part-way says how many went out ("Sent 2 of 5 presses before
  disconnect: ..."), so a retry doesn't resend the ones the hub already got. A failure on the first press is
  reported unchanged.

### Changed

- Synced the shared files to mcp-server-shared `b405485` (`remote.py`, `logsafe.py`, `tasks.py`,
  `ratelimit.py`, their tests and `scripts/sync_shared.py`), which also adds the shared `registry.py`.
- The assumption record and its queries (`Assumption`, `unverified`, `contradicted`) now come from the
  shared `registry.py`; `assumptions.py` keeps only the Sofabaton list. `unverified()` means "not yet seen
  on hardware" (`simulator-only`), so a contradicted assumption is no longer counted as unconfirmed.
  `doctor` lists contradicted assumptions in full, ahead of the unconfirmed ones (none today), and its
  `--json` report gains `contradicted_assumptions`.
- Rate limits use the shared `TokenBucket` and `refusal` (`ratelimit.py`) instead of a private copy.
  A refused call now says "try again in 1s" rather than "0s" when less than a second is left, and
  "it's more than the limit allows at once" when a call can never fit.

## [0.2.1] - 2026-10-17

### Changed

- MQTT support is included: `aiomqtt` is a regular dependency, so `pip install
  mcp-server-sofabaton` is all an X2 needs. The `[mqtt]` extra still resolves (to nothing), so
  existing install commands keep working.

## [0.2.0] - 2026-10-16

The first release on PyPI, and the first tested on a real hub.

### Seen working on hardware

On one X2 over MQTT (no sofabaton-x-server): login, the activity and device lists, an activity's
buttons, macros and favorites; `start_activity` and `power_off`, confirmed by the hub;
`press_button`; a macro and favorites through `send_command`; the settle window after an
activity change; and Wifi Device presses on `<MAC>/up`. Details in README's "Tested on" and in
`assumptions.py`.

### Changed

- Over MQTT, `list_commands` and `send_command` with a *device* as the target are refused at once,
  saying why and what would enable them: the X2 never answers `device/<MAC>/keys_request`. An
  activity's own buttons, macros and favorites are unaffected. `get_status` lists this under
  `limitations`.
- `doctor` expects that silence instead of warning about it, and `doctor --dump` records every MQTT
  message it saw (MAC masked).
- The Alpine install script now always installs the code it was given, even at the same version
  number, so re-running it upgrades.
- Without Cloudflare Access, `serve --http` refuses a `--public-host` and answers 403 to requests
  a tunnel or proxy forwarded, so a tunnel without Access can't reach it (mcp-server-shared #4).
  `--insecure-no-auth` turns this off, as before.

### Added

- `deploy/alpine/`: an OpenRC service with the `[mqtt]` extra, every setting in one env file, the
  broker password in its own file, and `run.sh doctor|check|call` with the service's environment.
- Issue templates, including a hardware report for testers.

### Fixed

- On Python 3.11, stopping the MQTT connection could hang (a cancellation lost inside
  `asyncio.wait_for`, fixed in CPython 3.12). Tasks are now stopped by `tasks.stop_task`, shared with
  the sibling servers.

## [0.1.0]

Installable from git only. Built against a simulator: sofabaton-x-server's REST API (recorded from
0.2.4) and an X2 over MQTT, with every protocol detail recorded as an assumption.
