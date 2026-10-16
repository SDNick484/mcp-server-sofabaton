"""Every protocol detail this server relies on that hasn't been checked on a real hub.

Same mechanism as mcp-server-harmony: each assumption has an id that code
cites next to where it depends on it (``# ASSUMPTION S-MQTT-STATE``), tests
cite to document it, HARDWARE_VALIDATION.md cites in the step that confirms
it, and `doctor` prints. tests/test_assumptions.py keeps the README and
HARDWARE_VALIDATION.md in step with this list.

Sources, shortened in ``source``:
  [Y]  yomonpet/ha-sofabaton-hub (control + list topics; called "the official
       Sofabaton Hub integration" by m3tac0de's README, unconfirmed on sofabaton.com)
  [H]  RepairGuyDK/com.repairguydk.sofabatonx2 (Homey app: device topics)
  [M]  m3tac0de/home-assistant-sofabaton-x1s docs/protocol/live-hub-testing.md
       (benches against real X1S/X2 hubs)
  [S]  sofabaton-x-server 0.2.4, run here with no hub attached; its OpenAPI
       document is in tests/fixtures/

Confidence:
  high   - measured on a real hub by [M], or recorded from the real server [S]
  medium - one or two independent client implementations agree, no measurement
  low    - our own choice or guess; the simulator implements it, nothing confirms it

To record a hardware result, set ``status`` to "hardware-verified" (or
"hardware-contradicted" with what you saw in ``note``) and commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "medium", "low"]
Status = Literal["simulator-only", "hardware-verified", "hardware-contradicted"]


@dataclass(frozen=True)
class Assumption:
    id: str
    claim: str
    source: str
    confidence: Confidence
    status: Status = "simulator-only"
    note: str = ""


# Hardware runs, cited by the notes below.
_HW_2026_10_16 = "2026-10-16, the owner's X2 over MQTT (Home Assistant's Mosquitto) from the service on LXC210"

ASSUMPTIONS: tuple[Assumption, ...] = (
    # --- sofabaton-x-server (any model) -----------------------------------------------------
    Assumption(
        "S-REST-API",
        "sofabaton-x-server 0.2.x serves the routes and response fields we use under /api/v1.",
        "[S] OpenAPI document recorded from 0.2.4; test_openapi_contract.py checks every field we read",
        "high",
    ),
    Assumption(
        "S-REST-MODES",
        "mode 'observe' (the Sofabaton app holds the proxy) refuses commands with 409; with no hub session, "
        "catalog reads and send/start/stop get 503 (they look the id up first) and find-remote 409; all are "
        "RFC 9457 problem bodies carrying 'mode'.",
        "[S] 503 observed on 0.2.4 with no hub attached; the per-route mapping read from its routes_hub_data.py; "
        "observe mode itself not observed",
        "medium",
    ),
    Assumption(
        "S-REST-START",
        "A start/stop is confirmed when GET /activity shows the change; the power macro finishes within 30 s.",
        "[S] route semantics; 30 s is our timeout",
        "low",
    ),
    Assumption(
        "S-X1-NO-MQTT",
        "Only the X2 has MQTT; the X1 and X1S are reached through sofabaton-x-server only.",
        "[M] README (MQTT is X2-only); [Y] is X2-only",
        "high",
    ),
    # --- X2 MQTT --------------------------------------------------------------------------------
    Assumption(
        "S-MQTT-UP",
        'A key bound to an MQTT Wifi Device publishes {"device_id", "key_id"} to <MAC>/up, QoS 0, never retained.',
        "[M] measured; openHAB forum user report agrees",
        "high",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": a button bound to an MQTT Wifi Device was pressed on the physical remote, and the "
        "owner saw the <MAC>/up message on the broker with mosquitto_sub.",
    ),
    Assumption(
        "S-MQTT-STATE",
        'Every activity change is published to activity/<MAC>/activity_control_up as {"activity_id", "state"} '
        "early in the power macro; activity_id 255 means everything off. This includes changes we request.",
        "[M] measured (incl. 'every transition'); [Y] parses the same shape",
        "high",
        status="hardware-verified",
        note=_HW_2026_10_16 + ': start_activity "Watch TV" got {"activity_id":101,"state":"on"}; power_off (which '
        'asked for 101 off) got {"activity_id":255,"state":"off"}, not 101 off. The X2 also pushed one when Watch TV '
        "was started from the physical remote. When in the macro it arrives wasn't timed.",
    ),
    Assumption(
        "S-MQTT-MAC-CASE",
        "Topics use the hub's MAC as UPPERCASE bare hex (02AB34CD56EF), for activity/ and device/ topics too.",
        "[M] measured for <MAC>/up and device/<MAC>/keys_control; activity/ topics inferred",
        "medium",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": activity/<MAC>/ and device/<MAC>/ list requests were answered on the uppercase "
        "topics (doctor never needed its lowercase probe).",
    ),
    Assumption(
        "S-MQTT-CONTROL",
        'Publishing {"data": {"activity_id", "state": "on"|"off"}} to activity/<MAC>/activity_control_down '
        "starts or stops an activity.",
        "[Y]",
        "medium",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": through the MCP connector, start_activity and power_off on Watch TV (101) each "
        "published this and the family room's devices turned on, then off.",
    ),
    Assumption(
        "S-MQTT-KEYS",
        '{"data": {"activity_id", "key_id"}} to activity/<MAC>/keys_control presses a hard button, with key_id = '
        "the ButtonName code (VOL_UP = 182).",
        "[Y] (its key table matches sofabaton-x's ButtonName codes)",
        "medium",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": press_button VOL_UP repeat=2 on Watch TV published key_id 182 twice and the "
        "receiver's volume went up, twice in a row.",
    ),
    Assumption(
        "S-MQTT-MACRO",
        '{"data": {"activity_id", "key_id"}} to activity/<MAC>/macro_keys_control runs a macro.',
        "[Y]",
        "medium",
        status="hardware-verified",
        note=_HW_2026_10_16 + ': send_command "Big Volume Up" on Watch TV published macro_keys_control with key_id 8, '
        "and the receiver's volume went up by the macro's full amount.",
    ),
    Assumption(
        "S-MQTT-FAVORITE",
        "Favorites go to activity/<MAC>/favorites_keys_control with the *device* id in the activity_id field.",
        "[Y] only ('Due to firmware design issue')",
        "low",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": Watch TV's favorites Netflix (device 1, on the TV) and Disney (device 6, on Home "
        "Assistant) both opened from Claude, with the device id in activity_id; each sent twice, both worked.",
    ),
    Assumption(
        "S-MQTT-DEVICE",
        '{"data": {"device_id", "key_id"}} to device/<MAC>/keys_control sends a device command.',
        "[M] measured ('the hub DOES honor device/<MAC>/keys_control'); [H] uses it",
        "high",
        note="Unused while S-MQTT-DEVICE-KEYS holds: without a device's key list there are no key ids to send.",
    ),
    Assumption(
        "S-MQTT-DEVICE-KEYS",
        "The X2 does NOT answer device/<MAC>/keys_request, so a device's own commands can't be listed or sent "
        "over MQTT (protocol.DEVICE_KEYS_OVER_MQTT = False).",
        "[H] requests them but tolerates 'the hub's occasional non-responses'; [Y] disables its device features",
        "high",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": no reply at all for any of the six devices, on any topic under the MAC "
        "(mosquitto_sub on device/<MAC>/#, activity/<MAC>/# and <MAC>/#, 4 s per request). This disproved what "
        "the code first assumed from [H] (that it answers, then part of S-MQTT-LISTS). A burst of these wedged the "
        "X2's request handling until a power cycle (see S-MQTT-SERIAL), so nothing but doctor's one probe sends it.",
    ),
    Assumption(
        "S-MQTT-LISTS",
        'List requests ({"data": "activity_list"} etc.) are answered on activity/<MAC>/list, keys_list, '
        "macro_keys_list, favorites_keys_list and device/<MAC>/list with the shapes in protocol.py. (A device's "
        "own key list is S-MQTT-DEVICE-KEYS.)",
        "[Y] and [H] agree",
        "medium",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": the activity list (5), the device list (6), and the first activity's keys, macros "
        "and favorites all answered and parsed (doctor --dump).",
    ),
    Assumption(
        "S-MQTT-IDS",
        "Nothing says the X2's MQTT ids equal sofabaton-x-server's; we never carry ids between the two.",
        "none: a design choice that avoids depending on it (hybrid tests use different id spaces)",
        "low",
    ),
    Assumption(
        "S-MQTT-SERIAL",
        "The hub handles one MQTT request at a time; requests are sent one by one, 200 ms apart.",
        "[Y] and [H] both serialize with a small gap ('the hub is single-threaded')",
        "medium",
        note=_HW_2026_10_16 + ": after a burst of a dozen-plus unanswered device/<MAC>/keys_request (a probe script "
        "and an older doctor), the X2 stopped answering every request while still pushing activity_control_up; a "
        "power cycle fixed it. A single unanswered request (doctor's probe) left it answering. Consistent with a "
        "one-at-a-time handler, not proof of it.",
    ),
    Assumption(
        "S-MQTT-REPLY-TIMEOUT",
        "The X2 answers a list request within 5 s.",
        "none: our timeout",
        "low",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": every list request that is answered at all was answered within doctor's 5 s.",
    ),
    Assumption(
        "S-MQTT-SETTLE",
        "After an MQTT-announced activity change, the power macro finishes within SOFABATON_MQTT_SETTLE_S (6 s); "
        "presses over MQTT are held off until then.",
        "none: [M] notes commands sent mid-macro can interrupt it, and has no completion signal over MQTT",
        "low",
        status="hardware-verified",
        note=_HW_2026_10_16 + ": Watch TV's devices were fully on (picture, sound, input) about 5 s after the start, "
        "inside the 6 s window; a VOL_UP sent ~4 s in was refused with 'Try again in 2s', and one after it went "
        "through. Other activities' macros weren't timed.",
    ),
)

BY_ID = {a.id: a for a in ASSUMPTIONS}


def unverified() -> list[Assumption]:
    return [a for a in ASSUMPTIONS if a.status != "hardware-verified"]
