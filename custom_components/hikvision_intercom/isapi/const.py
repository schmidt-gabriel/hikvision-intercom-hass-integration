"""ISAPI protocol endpoints and constants.

Every path below was verified live against a DS-KB8113-IME1(B) running
firmware V2.2.60 (build 231204) on 2026-09-01. Endpoints that return 404 or
misbehave on that firmware are marked as such.
"""

# --- System / discovery --------------------------------------------------
DEVICE_INFO = "/ISAPI/System/deviceInfo"
SYSTEM_CAPABILITIES = "/ISAPI/System/capabilities"
IO_CAPABILITIES = "/ISAPI/System/IO/capabilities"
REBOOT = "/ISAPI/System/reboot"

# --- Video intercom ------------------------------------------------------
INTERCOM_CAPABILITIES = "/ISAPI/VideoIntercom/capabilities"
CALL_STATUS = "/ISAPI/VideoIntercom/callStatus?format=json"
CALL_STATUS_CAPABILITIES = "/ISAPI/VideoIntercom/callStatus/capabilities?format=json"
CALL_SIGNAL = "/ISAPI/VideoIntercom/callSignal?format=json"

# Call durations (the web UI's "Call Settings" tab). The name does not help:
# these are maxRingTime, talkTime and messageTime.
OPERATION_TIME = "/ISAPI/VideoIntercom/operationTime"
OPERATION_TIME_CAPABILITIES = "/ISAPI/VideoIntercom/operationTime/capabilities"

# --- Door control --------------------------------------------------------
# Careful: this endpoint speaks XML, not JSON, even with ?format=json.
DOOR_CONTROL = "/ISAPI/AccessControl/RemoteControl/door/{door_no}"
DOOR_CAPABILITIES = "/ISAPI/AccessControl/RemoteControl/door/capabilities"

# --- IO outputs ----------------------------------------------------------
OUTPUT_TRIGGER = "/ISAPI/System/IO/outputs/{output_no}/trigger"

# --- Events --------------------------------------------------------------
# CHANNEL 1 -- BROKEN on firmware V2.2.60: returns HTTP 200 with a 40-byte body
# (just the XML declaration) and closes immediately, instead of holding open a
# multipart stream. This is why every existing integration goes silent on this
# model. Kept here only for the capability probe, in case a future firmware
# fixes it.
ALERT_STREAM = "/ISAPI/Event/notification/alertStream"

# CHANNEL 2 -- reverse push: the device POSTs events to a URL of ours.
HTTP_HOSTS = "/ISAPI/Event/notification/httpHosts"
HTTP_HOSTS_CAPABILITIES = "/ISAPI/Event/notification/httpHosts/capabilities"
EVENT_TRIGGERS = "/ISAPI/Event/triggers"

# --- Streaming and audio -------------------------------------------------
STREAMING_CHANNELS = "/ISAPI/Streaming/channels"
SNAPSHOT = "/ISAPI/Streaming/channels/{channel}/picture"
TWO_WAY_AUDIO_CHANNELS = "/ISAPI/System/TwoWayAudio/channels"
TWO_WAY_AUDIO_CHANNEL = "/ISAPI/System/TwoWayAudio/channels/{channel}"

# The client must stay Home Assistant free and self-contained, so the RTSP port
# lives here rather than in the component's const.py.
DEFAULT_RTSP_PORT = 554
RTSP_PATH = "/Streaming/Channels/{channel}"

MAIN_STREAM_CHANNEL = 101
SUB_STREAM_CHANNEL = 102

# --- Call states ---------------------------------------------------------
CALL_IDLE = "idle"
CALL_RING = "ring"
CALL_ON_CALL = "onCall"
CALL_STATES = (CALL_IDLE, CALL_RING, CALL_ON_CALL)

# --- Call signal commands ------------------------------------------------
CMD_ANSWER = "answer"
CMD_REJECT = "reject"
CMD_HANGUP = "hangUp"

# --- Door commands -------------------------------------------------------
DOOR_OPEN = "open"
DOOR_ALWAYS_OPEN = "alwaysOpen"
DOOR_RESUME = "resume"

# Namespace used in ISAPI XML responses.
XML_NS = "{http://www.isapi.org/ver20/XMLSchema}"

# httpHosts URL length limit, read from the device capabilities.
HTTP_HOST_URL_MAX_LEN = 128
