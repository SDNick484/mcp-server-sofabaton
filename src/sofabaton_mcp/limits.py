"""This server's rate limits and per-call caps: the numbers, per hub.

A model in a loop ("volume up... still quiet... volume up...") can send a lot
of IR in a few seconds, and an activity start/stop loop can wear out a TV's
power relay. The schema caps one call (repeat <= 10, hold <= 3 s); token
buckets cap the *rate* across calls, per hub. Each press (or activity change)
takes one token, and a call that needs more than are there is refused whole,
with how long to wait, rather than half-sent: half a "volume up x5" is worse
than none.

The mechanism (TokenBucket, refusal) is shared with the siblings in
ratelimit.py; these numbers are about a remote hub.
"""

from __future__ import annotations

# Presses per hub: a burst of 20, then 4 per second sustained.
PRESS_CAPACITY, PRESS_RATE = 20.0, 4.0
# Activity changes (start or power off) per hub: 4 in a burst, then one per 15 s.
ACTIVITY_CAPACITY, ACTIVITY_RATE = 4.0, 1 / 15
# Volume-up presses: at most VOLUME_UP_LIMIT in any VOLUME_UP_WINDOW seconds, counted across calls. A hub
# sends blind IR and can't read the receiver's level, so each press is one step louder whatever the level
# already is; the receiver's own server caps a level it is *set* to (max_volume), but not presses. A sliding
# window (WindowLimit), not a bucket, because the count itself is the safety limit: a bucket of 20 refilling
# over five minutes would allow 40 in a five-minute stretch. SOFABATON_VOLUME_UP_LIMIT can lower it, not raise it.
VOLUME_UP_LIMIT = 20
VOLUME_UP_WINDOW = 300.0
# Command labels (after normalising case, spaces and punctuation) that turn the volume up, plus the VOL_UP
# button. Volume down and mute are never limited.
VOLUME_UP_NAMES = frozenset({"volup", "volumeup", "volumeplus", "volplus"})
# Per call.
MAX_REPEAT = 10
MAX_HOLD_MS = 3000
MIN_DELAY_MS, MAX_DELAY_MS = 100, 2000
MAX_CALL_SECONDS = 15.0  # repeat * (hold + delay) for one send_command
