# Changelog

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
