"""Constants for the Hikvision Video Intercom integration."""

DOMAIN = "hikvision_intercom"
MANUFACTURER = "Hikvision"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

DEFAULT_PORT = 80

# Options
CONF_POLL_INTERVAL = "poll_interval"
CONF_USE_HTTP_HOST = "use_http_host"
CONF_ENABLE_TWO_WAY_AUDIO = "enable_two_way_audio"
CONF_ANSWERED_RING_THRESHOLD = "answered_ring_threshold"

DEFAULT_POLL_INTERVAL = 1.0  # seconds, while polling
HEALTHCHECK_INTERVAL = 30.0  # seconds, while in push mode

# How long without a push event before the watchdog considers the channel degraded.
PUSH_WATCHDOG_TIMEOUT = 300.0  # 5 minutes

# Duration of the `ring` state when NOBODY answers: the device gives up on its
# own. Measured on two independent captures (31.0s and 31.2s), matching to the
# tenth of a second.
#
# CAREFUL: this value does NOT correspond to the device's "Max. Ring Duration"
# setting, which read 65s during the measurements. So the 31.2s comes from a
# different timer -- most likely the indoor station, which ends the `ring` when
# it falls back to message mode (the UI's "Max. Message Duration" was 30s, and
# the observed `onCall` lasted 31.2s). No ISAPI endpoint on this model exposes
# that indoor-station timer, so the value is empirical rather than read from the
# device.
#
# That is why the threshold is a configurable OPTION: anyone changing the call
# durations on the device must be able to adjust it without editing code. Debug
# logs record the real duration of every ring, which makes calibration easy.
RING_TIMEOUT_SECONDS = 31.2

# Below this much time in `ring`, someone answered (on the indoor station or in
# the phone app -- both look identical in callStatus). Real answered calls
# measured 13.0s (indoor station) and 4.4s (app), far from the timeout.
#
# The 2s margin covers polling granularity. The ambiguous case is answering at
# the very last second, which is rare and inherently indistinguishable here.
DEFAULT_ANSWERED_RING_THRESHOLD = RING_TIMEOUT_SECONDS - 2.0

# Kept for the tests, which assert against the default.
ANSWERED_RING_THRESHOLD = DEFAULT_ANSWERED_RING_THRESHOLD
