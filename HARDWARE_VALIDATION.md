# Hardware validation (Sofabaton)

Everything in this repo has been verified **against the simulator only**. This is the
checklist for the first session with a real hub. The steps are ordered so each one depends only
on the ones before it. Each step names the assumptions it confirms (ids from
`src/sofabaton_mcp/assumptions.py`; the evidence behind each is in [PROTOCOL.md](PROTOCOL.md)).

Plan on about 45 minutes for an X2 with MQTT. Steps 1 to 5 and 10 are all an X1 or X1S needs.

## Before you start

You'll need:
- sofabaton-x-server 0.2.x running, with your hub added and **claimed** (an admin account set up)
- for the X2 steps: the MQTT broker the X2 is linked to (Sofabaton app: Me → Connect to Home
  Assistant), with a username and password if it needs them
- the TV and receiver in view, so you can see what each command does

Install on any machine on the LAN (the LXC is fine) and run the tests first:

```sh
git clone https://github.com/SDNick484/mcp-server-sofabaton.git && cd mcp-server-sofabaton
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # expect: all passed, 1 skipped (no recordings yet)
export SOFABATON_URL=http://<server>:8480
```

**Recording a result.** When a step confirms an assumption, set its `status` to
`"hardware-verified"` in `assumptions.py`. When it contradicts one, set
`"hardware-contradicted"`, put what you saw in `note`, and keep the output. Commit both.
`doctor` stops listing an assumption once it's verified.

**Tool calls** use `mcp-server-sofabaton call <tool> key=value ...`. It runs the tool through
the same MCP layer the model uses, so the arguments are validated the same way. Add `--dry-run`
to see what would be sent without sending it. `call tools` lists the tools.

---

## 1. The server and the hub's identity

```sh
mcp-server-sofabaton doctor
```

Expected (your names, firmware and MAC will differ):

```
OK   config
OK   server    sofabaton-x-server 0.2.4 at http://x.x.x.70:8480
OK   hub       hub <id>: session up, mode control
OK   identity  X2 '<hub name>', firmware <n>, MAC xxxxxxxx<last 4>
OK   catalog   <n> activities, <n> devices over REST
...
All checks passed.
```

- A `WARN ... unclaimed` line means the server still has no admin account. Claim it before
  going on.
- A `WARN Built against sofabaton-x-server 0.2.x` line means the version differs. Run
  `pytest tests/test_openapi_contract.py` against a fresh recording of its schema (see that
  file's docstring).

Then record what the server returns:

```sh
mcp-server-sofabaton doctor --dump captures/
cp captures/sofabaton.json tests/fixtures/recorded/home.json
pytest -q tests/test_recorded.py      # expect: passed (the replay of your hub's real payloads)
```

Read `home.json` before committing it. It has your hub, activity and device names. The MAC and
IP addresses are redacted.

Confirms: **S-REST-API** (the server half; if `test_recorded` passes).

## 2. The names the model will use

```sh
mcp-server-sofabaton check
```

Expected: `Hub: <name> (X2)`, `Commands go via: server`, then your activities and devices.
These are the exact names to use below. Note one activity that drives the TV (call it
`$ACT`), a device with a command you can see work, such as a receiver input (`$DEV`, `$CMD`),
and a macro and a favorite from `call list_commands target="$ACT"`.

```sh
ACT="Watch Shield"; DEV="Onkyo Receiver"; CMD="Input HDMI 2"     # yours here
```

## 3. Dry run

```sh
mcp-server-sofabaton call --dry-run start_activity activity="$ACT"
```

Expected: `"outcome": "dry_run"`, and `sent` shows the exact `POST .../activities/<id>/start`
that would go out. Nothing happens on the TV.

## 4. Start, press and stop through the server

```sh
time mcp-server-sofabaton call start_activity activity="$ACT"
```

Expected: the TV and devices power on; the call returns `"outcome": "done", "via": "server"`
only after the hub reports the activity running. Note the time: it must be under 30 s.

```sh
mcp-server-sofabaton call press_button button=VOL_UP repeat=2
mcp-server-sofabaton call send_command command="$CMD" target="$DEV"
mcp-server-sofabaton call find_remote          # the remote beeps
mcp-server-sofabaton call power_off
```

Expected: volume up twice, the input changes, the remote beeps, everything turns off. Each
returns `"outcome": "done"`.

Confirms: **S-REST-START** (if the time was under 30 s and `done` came after the TV was on).

## 5. When the Sofabaton app is open (server only)

Open the Sofabaton app on a phone, so it connects to the hub through sofabaton-x-server's
proxy. Then, **without** MQTT configured:

```sh
mcp-server-sofabaton call get_status | grep -E '"mode"|limitations' -A2
mcp-server-sofabaton call press_button button=VOL_UP
```

Expected: `"mode": "observe"`. The limitations say commands are refused until the app is
closed, and on an X2 that MQTT would keep it controllable. The press fails with `... close the
app on every phone and tablet`.

Close the app and check that `doctor` shows `mode control` again.

Confirms: **S-REST-MODES** (the 409 in observe mode). If the press fails some other way,
capture `call get_status` and the error.

---

The steps below need an **X2**. For an X1 or X1S, skip to step 10.

## 6. MQTT: login, the MAC in topics, the list replies

```sh
export SOFABATON_MQTT_URL=mqtt://<user>:<password>@<broker>:1883
mcp-server-sofabaton doctor
```

Expected, in addition to step 1:

```
OK   mqtt      broker x.x.x.<n>:1883: logged in
OK   x2        answered over MQTT: <n> activities, running: nothing
```

What a failure means:
- `FAIL mqtt ... login rejected`: wrong user or password in `SOFABATON_MQTT_URL`.
- `FAIL x2 ... answered on the *lowercase*-MAC topics`: **S-MQTT-MAC-CASE is contradicted**.
  Record it. Every topic in `protocol.py` would then need the lowercase MAC.
- `FAIL x2 no reply to activity/<MAC>/list_request`: the X2 isn't linked to this broker, or
  it doesn't answer list requests. Run step 7 to see whether it publishes anything at all.
- `WARN The X2 didn't answer activity/<MAC>/macro_keys_request` (or another list): that list
  shape is unconfirmed. Note which one.

Record the MQTT side too, and compare the ids:

```sh
mcp-server-sofabaton doctor --dump captures/
cp captures/sofabaton.json tests/fixtures/recorded/home.json
pytest -q tests/test_recorded.py
python -c "
import json; d = json.load(open('captures/sofabaton.json'))
rest = {a['name']: a['activity_id'] for a in d['server']['activities']}
mqtt = {n: i for i, n, _ in d['mqtt']['activities']}
print('REST:', rest); print('MQTT:', mqtt); print('same ids:', rest == mqtt)"
```

Confirms: **S-MQTT-MAC-CASE**, **S-MQTT-LISTS** (if `test_recorded` passes with no
warnings), **S-MQTT-REPLY-TIMEOUT** (doctor waits 5 s per reply), **S-MQTT-SERIAL** (doctor's
list requests go one after another, 200 ms apart). **S-MQTT-IDS**: write down `same ids`. The
code doesn't depend on the answer, but it tells us whether a later simplification is safe.

## 7. What the X2 publishes on its own

```sh
mcp-server-sofabaton doctor --listen 60
```

During the 60 seconds:
1. Start `$ACT` **from the physical remote**. Watch when the line appears compared to when the
   TV finishes powering on, and write down roughly how many seconds the macro took.
2. Press **OFF** on the remote.
3. If a key is bound to an MQTT Wifi Device, press it. (To set one up in the Sofabaton app:
   add a "Wifi Device" of type MQTT and bind it to a key.)

Expected, under `Heard on the broker:`:

```
activity/xxxxxxxx<last 4>/activity_control_up: b'{"activity_id":101,"state":"on"}'
activity/xxxxxxxx<last 4>/activity_control_up: b'{"activity_id":255,"state":"off"}'
xxxxxxxx<last 4>/up: b'{"device_id":<n>,"key_id":<n>}'
```

Lines marked `(retained)` are expected for `activity_control_up`; the server ignores them.

Confirms: **S-MQTT-STATE** (the shape, `255` for OFF, and that it arrives *early*: before the
TV finishes), **S-MQTT-UP**. Keep the whole output either way. Paste it into the
`note` of anything it contradicts.

## 8. Control over MQTT only (no server)

```sh
SBX_URL=$SOFABATON_URL; export SOFABATON_URL=none
export SOFABATON_MQTT_MAC=<the X2's MAC>           # from your router, or `doctor` in step 1 (last 4 shown)
mcp-server-sofabaton call get_status | grep -E '"via"|"model"'     # "mqtt", "X2"
```

Then, one at a time, watching the TV:

| Command                                                                                    | Expected                                                          | Confirms          |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- | ----------------- |
| `mcp-server-sofabaton call start_activity activity="$ACT"`                                  | Everything powers on; `"via": "mqtt"`                             | S-MQTT-CONTROL    |
| (after the TV is fully on) `mcp-server-sofabaton call press_button button=VOL_UP repeat=2`  | Volume up twice                                                   | S-MQTT-KEYS       |
| `mcp-server-sofabaton call send_command command="<a macro from step 2>"`                    | The macro runs                                                    | S-MQTT-MACRO      |
| `mcp-server-sofabaton call send_command command="<a favorite from step 2>"`                 | The favorite opens (e.g. the app or channel)                      | S-MQTT-FAVORITE   |
| `mcp-server-sofabaton call send_command command="$CMD" target="$DEV"`                       | The device does it                                                | S-MQTT-DEVICE     |
| `mcp-server-sofabaton call find_remote`                                                    | Refused: `find_remote needs sofabaton-x-server`                  | (expected)        |
| `mcp-server-sofabaton call power_off`                                                      | Everything turns off                                              | S-MQTT-CONTROL    |

Each `call` is a separate process, and the settle window (presses held off for 6 s after an
activity change over MQTT) lives in the server's memory, so you won't see it refuse anything
here. The tests cover the guard itself. In step 11, with `SOFABATON_URL=none` (the window only
applies on the MQTT path), ask Claude to start an activity and turn
the volume up in one breath, and the second call should be refused with `power macro may still
be running ... Try again in Ns`.

**S-MQTT-SETTLE:** compare the macro time you noted in step 7 with 6 s. If the macro takes
longer, set `SOFABATON_MQTT_SETTLE_S` to it (plus a second) and write the number in the note.
If presses sent during the macro interrupted it, the guard is doing its job.

**S-MQTT-FAVORITE** is the least certain (one source, and a firmware quirk). If the favorite
goes to the wrong device or nothing happens, record what did happen.

## 9. The headline X2 feature: control while the app is open

```sh
export SOFABATON_URL=$SBX_URL          # server and MQTT together
```

Open the Sofabaton app on a phone, then:

```sh
mcp-server-sofabaton call get_status | grep -E '"via"|"mode"|limitations' -A2
mcp-server-sofabaton call start_activity activity="$ACT"
mcp-server-sofabaton call power_off
```

Expected: `"mode": "observe"`, `"via": "mqtt"`, a limitation saying commands go over MQTT
meanwhile, and the activity starts and stops with the app still open. Close the app.

This confirms nothing new, but it's the reason MQTT is worth setting up. If it fails here
after step 8 passed, capture `get_status`.

## 10. Wifi Device presses through the server (any model)

Bind a key to a Wifi Device that calls back to sofabaton-x-server (its control panel: the
hub's Wifi Devices page). Press it, then:

```sh
mcp-server-sofabaton call get_recent_presses
```

Expected: the press, with its label and `last_seq`. On an X2 over MQTT only, the same tool
reads the `<MAC>/up` presses from step 7 instead.

## 11. With Claude (optional)

Add the server to Claude Code (see the README) and ask: "What's on the living room TV? Turn it
up a little." The model should call `get_status` first and use `press_button`. If it does
something odd, the tool descriptions are what to fix.

---

## Which step confirms what

| Assumption             | Step   | Confidence before | Can an X2 owner confirm it?                      |
| ---------------------- | ------ | ----------------- | ------------------------------------------------ |
| `S-REST-API`           | 1      | high              | yes                                              |
| `S-REST-MODES`         | 5      | medium            | yes (the 409; the 503 needs the hub unplugged)   |
| `S-REST-START`         | 4      | low               | yes                                              |
| `S-X1-NO-MQTT`         | none   | high              | no: needs an X1 or X1S. Stays simulator-only     |
| `S-MQTT-UP`            | 7      | high              | yes, with a Wifi Device bound to a key           |
| `S-MQTT-STATE`         | 7      | high              | yes                                              |
| `S-MQTT-MAC-CASE`      | 6      | medium            | yes                                              |
| `S-MQTT-CONTROL`       | 8      | medium            | yes                                              |
| `S-MQTT-KEYS`          | 8      | medium            | yes                                              |
| `S-MQTT-MACRO`         | 8      | medium            | yes, if an activity has a macro                  |
| `S-MQTT-FAVORITE`      | 8      | low               | yes, if an activity has a favorite               |
| `S-MQTT-DEVICE`        | 8      | high              | yes                                              |
| `S-MQTT-LISTS`         | 6      | medium            | yes                                              |
| `S-MQTT-IDS`           | 6      | low               | yes (record the answer; nothing depends on it)  |
| `S-MQTT-SERIAL`        | 6      | medium            | partly: it shows serial requests work, not that parallel ones fail |
| `S-MQTT-REPLY-TIMEOUT` | 6      | low               | yes                                              |
| `S-MQTT-SETTLE`        | 7, 8   | low               | yes (and tune `SOFABATON_MQTT_SETTLE_S`)         |

## What to send back if something fails

- The full `doctor` output (it's redacted), and `doctor --json` if you can.
- `captures/sofabaton.json` from `--dump` (review it first; it has your names).
- For MQTT, the `--listen` output from step 7.
- The `call` command you ran and its full output.
