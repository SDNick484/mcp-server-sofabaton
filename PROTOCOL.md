# What this server assumes about Sofabaton hubs, and where each claim comes from

Sofabaton publishes no API documentation. Everything below comes from other people's client
code, their bench notes, or our own observation of sofabaton-x-server, and **none of it has been
checked against a hub by this project yet**. Each claim has an assumption id
(`src/sofabaton_mcp/assumptions.py`), a confidence level and a source. The code cites the id
where it depends on the claim, `doctor` lists the unconfirmed ones, and
[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) has the step that confirms each one.

## Sources

| Tag | Source                                                                                                                                                                                  | What it is                                                                                                                       |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| [S] | [sofabaton-x-server](https://pypi.org/project/sofabaton-x-server/) 0.2.4                                                                                                                | Run here with no hub attached. Its OpenAPI document is in `tests/fixtures/`, and some behavior was read from its source        |
| [M] | [m3tac0de/home-assistant-sofabaton-x1s](https://github.com/m3tac0de/home-assistant-sofabaton-x1s), `docs/protocol/live-hub-testing.md` (checked at c33d4dc, 2026-10-05)               | The sofabaton-x author's bench notes against real X1S and X2 hubs. **Measured**                                                 |
| [Y] | [yomonpet/ha-sofabaton-hub](https://github.com/yomonpet/ha-sofabaton-hub) (e57ffcb, 2026-08-20)                                                                                         | A Home Assistant integration for the X2 over MQTT. [M] calls it the official one; that isn't confirmed on sofabaton.com        |
| [H] | [RepairGuyDK/com.repairguydk.sofabatonx2](https://github.com/RepairGuyDK/com.repairguydk.sofabatonx2) (c328b6b, 2026-08-30)                                                              | A Homey app for the X2 over MQTT                                                                                                 |

**Confidence:**
- **high**: measured on a real hub by [M], or recorded from the real server [S].
- **medium**: one or two independent client implementations agree, but nobody published a
  measurement.
- **low**: our own choice or guess. The simulator implements it; nothing confirms it.

## Which model can do what

| Feature                                        | X1 / X1S (server)        | X2 (server)     | X2 (server + MQTT)   | X2 (MQTT only)   |
| ---------------------------------------------- | ------------------------ | --------------- | -------------------- | ---------------- |
| List activities, devices, commands             | yes                      | yes             | yes                  | yes              |
| Start / power off an activity, with confirmation | yes                    | yes             | yes                  | yes              |
| Remote buttons, macros, favorites, device commands | yes                  | yes             | yes                  | yes ¹            |
| Find the remote                                | yes                      | yes             | yes (not while the app is open) | no ²  |
| Model, firmware, MAC                           | yes                      | yes             | yes                  | no               |
| Wifi Device presses (`get_recent_presses`)     | yes (HTTP callback)      | yes             | yes                  | yes (`<MAC>/up`) |
| Live activity state (pushed early in the macro) | no                      | no              | yes                  | yes              |
| Control while the Sofabaton app is open        | no                       | no              | yes (falls back to MQTT) | yes          |

¹ Over MQTT, hard buttons go through an activity (`activity/<MAC>/keys_control`); a device's own
commands go through `device/<MAC>/keys_control`.
² None of the topics in the sources makes the remote beep.

`S-X1-NO-MQTT` (high): only the X2 has MQTT. sofabaton-x-server reaches all three models over the
proxy protocol, which is why the X1 and X1S are fully supported through it.

## sofabaton-x-server (any model)

| Id             | Claim                                                                                                                                                                                                                                                                       | Source                                                                                     | Confidence |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------- |
| `S-REST-API`   | Version 0.2.x serves the routes and fields we use under `/api/v1`                                                                                                                                                                                                         | [S] OpenAPI recorded from 0.2.4; `test_openapi_contract.py` checks every route and field | high       |
| `S-REST-MODES` | `observe` (the app holds the proxy) refuses commands with 409. With no hub session, catalog reads and `send`/`start`/`stop` get 503 (`send` and `start`/`stop` look the id up first, and `find-remote` gets 409). Bodies are RFC 9457 problems with `mode` | [S] 503 observed on 0.2.4; the per-route mapping was read from its `routes_hub_data.py`; observe mode itself not observed | medium     |
| `S-REST-START` | A start/stop is confirmed when `GET /activity` shows it, within 30 s                                                                                                                                                                                                      | [S] route semantics; 30 s is our timeout                                                   | low        |

## X2 MQTT

The X2 keeps a client connection to the broker set in the Sofabaton app (Me → Connect to Home
Assistant). `<MAC>` is the hub's MAC as 12 hex digits with no separators.

### What the hub publishes (we subscribe)

| Topic                                 | Payload                                       | Meaning                                                                       | Id               | Confidence |
| ------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------- | ---------------- | ---------- |
| `<MAC>/up`                            | `{"device_id": 3, "key_id": 1}`               | A key bound to an MQTT Wifi Device was pressed. QoS 0, never retained        | `S-MQTT-UP`      | high [M]   |
| `activity/<MAC>/activity_control_up`  | `{"activity_id": 101, "state": "on"}`         | Every activity change, including ours, *early* in the power macro. `255` means all off | `S-MQTT-STATE` | high [M][Y] |
| `activity/<MAC>/list`                 | `{"data": [{"activity_id", "activity_name", "state"}]}` | Reply to `list_request`                                            | `S-MQTT-LISTS`   | medium [Y][H] |
| `activity/<MAC>/keys_list`            | `{"data": [{"key_id", "key_name"}]}`          | Reply to `keys_request`                                                       | `S-MQTT-LISTS`   | medium     |
| `activity/<MAC>/macro_keys_list`      | `{"data": [{"key_id", "key_name"}]}`          | Reply to `macro_keys_request`                                                 | `S-MQTT-LISTS`   | medium     |
| `activity/<MAC>/favorites_keys_list`  | `{"data": [{"key_id", "key_name", "device_id"}]}` | Reply to `favorites_keys_request`                                         | `S-MQTT-LISTS`   | medium     |
| `device/<MAC>/list`                   | `{"data": [{"device_id", "device_name"}]}`    | Reply to `device/<MAC>/list_request`                                          | `S-MQTT-LISTS`   | medium     |
| `device/<MAC>/keys_list`              | `{"data": [{"key_id", "key_name"}]}`          | Reply to `device/<MAC>/keys_request`                                          | `S-MQTT-LISTS`   | medium     |

Retained messages are dropped. A retained `activity_control_up` describes some past moment, not
now (the same rule sofabaton-x applies).

### What we publish

| Topic                                     | Payload                                              | Does                                       | Id                | Confidence   |
| ----------------------------------------- | ---------------------------------------------------- | ------------------------------------------ | ----------------- | ------------ |
| `activity/<MAC>/activity_control_down`    | `{"data": {"activity_id": 101, "state": "on"}}`      | Start (`on`) or stop (`off`) an activity   | `S-MQTT-CONTROL`  | medium [Y]   |
| `activity/<MAC>/keys_control`             | `{"data": {"activity_id": 101, "key_id": 182}}`      | A hard button through an activity (`key_id` = the ButtonName code; VOL_UP = 182) | `S-MQTT-KEYS` | medium [Y] |
| `activity/<MAC>/macro_keys_control`       | `{"data": {"activity_id": 101, "key_id": 40}}`       | Run a macro                                | `S-MQTT-MACRO`    | medium [Y]   |
| `activity/<MAC>/favorites_keys_control`   | `{"data": {"activity_id": <device id>, "key_id": 9}}` | A favorite. The *device* id goes in `activity_id` ("Due to firmware design issue") | `S-MQTT-FAVORITE` | low [Y] only |
| `device/<MAC>/keys_control`               | `{"data": {"device_id": 1, "key_id": 7}}`            | A device's own command                     | `S-MQTT-DEVICE`   | high [M][H]  |
| `activity/<MAC>/list_request`             | `{"data": "activity_list"}`                          | Ask for the activity list                  | `S-MQTT-LISTS`    | medium       |
| `activity/<MAC>/{keys,macro_keys,favorites_keys}_request` | `{"data": {"activity_id": 101}}`    | Ask for an activity's keys, macros or favorites | `S-MQTT-LISTS` | medium    |
| `device/<MAC>/list_request`               | `{"data": "device_list"}`                            | Ask for the device list                    | `S-MQTT-LISTS`    | medium       |
| `device/<MAC>/keys_request`               | `{"data": {"device_id": 1}}`                         | Ask for a device's commands                | `S-MQTT-LISTS`    | medium       |

### Behavior

| Id                     | Claim                                                                                                                                                         | Source                                                                    | Confidence |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------- |
| `S-MQTT-MAC-CASE`      | The MAC is UPPERCASE in every topic                                                                                                                            | [M] measured for `<MAC>/up` and `device/<MAC>/keys_control`; `activity/` inferred | medium |
| `S-MQTT-SERIAL`        | The hub handles one request at a time, so we send them one by one, 200 ms apart                                                                               | [Y] and [H] both do this ("the hub is single-threaded")                     | medium     |
| `S-MQTT-REPLY-TIMEOUT` | A list request is answered within 5 s                                                                                                                         | none: our timeout                                                         | low        |
| `S-MQTT-SETTLE`        | After an announced activity change, the macro finishes within `SOFABATON_MQTT_SETTLE_S` (6 s). Presses over MQTT wait until then                             | none. [M] notes mid-macro commands can interrupt it, and there's no completion signal | low |
| `S-MQTT-IDS`           | Nothing says MQTT ids equal sofabaton-x-server's, so ids never cross transports: names are resolved per path                                                  | none: a design choice that avoids depending on it                         | low        |

## What isn't known (and how the code copes)

- **Whether MQTT ids match REST ids.** Probably they do: both are the hub's own ids. But
  nothing confirms it, so the hybrid client resolves names separately on each path, and the
  tests give the two transports deliberately different id spaces (`mqtt_id_offset=1000`).
- **When a macro finishes, over MQTT.** The X2 announces the change at the start. The server
  path polls until the hub reports it; the MQTT path holds presses off for a settle window
  instead, and `get_status` shows `transition` while it runs.
- **Lowercase MACs.** If the X2 doesn't answer on uppercase topics, `doctor` retries in
  lowercase and tells you which one worked.
- **Discovery.** Hubs advertise `_x1hub._udp.local.` (X1, X1S) and `_sofabaton_hub._udp.local.`
  (X2), per sofabaton-x-server's docs in [M]'s repo. This server doesn't browse mDNS: the server
  path gets the MAC from sofabaton-x-server, and the MQTT-only path needs `SOFABATON_MQTT_MAC`.
