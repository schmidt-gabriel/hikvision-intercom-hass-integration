# ISAPI on the DS-KB8113-IME1(B) — what actually happens

Reverse-engineering notes taken against real hardware (`10.0.20.26`, firmware
**V2.2.60** build 231204, probed on 2026-09-01). Everything here was verified in
practice. Where the behaviour contradicts Hikvision's documentation or the
community's common wisdom, it is called out.

---

## Device identity

`GET /ISAPI/System/deviceInfo` → XML

| Field | Value |
|---|---|
| `model` | `DS-KB8113-IME1(B)` |
| `firmwareVersion` | `V2.2.60` (`build 231204`) |
| `deviceType` / `subDeviceType` | `VIS` / `villaDoorStation` |
| `alarmInNum` / `alarmOutNum` | `1` / `0` |

`serialNumber` is the stable identifier and becomes the config entry's
`unique_id`. The MAC is the fallback for firmwares returning an empty serial.

## Authentication

Digest MD5 with `qop="auth"`. A real challenge:

```
Digest qop="auth", realm="DS-0CED27ED", nonce="MGJm...NGI=",
       stale="false", opaque="", domain="::"
```

Details that matter in practice:

- The header's `uri` **must match** the request-line path exactly, query string
  included (`/ISAPI/VideoIntercom/callStatus?format=json`). A mismatch gives 401.
- `nc` and `qop` go **unquoted**; everything else quoted.
- `opaque` comes back empty; **do not echo it** when empty.
- **Never reuse the `nonce`.** The RFC allows reuse by incrementing `nc`, and
  this firmware does not tolerate it. Measured 2026-09-01, 12 reads per run:

  | Strategy | 0.5s | 1s | 2s |
  |---|---|---|---|
  | Reuse the nonce | 6 OK / 6 failures | 3 / 9 | 2 / 10 |
  | Fresh handshake per request | **12 / 0** | **12 / 0** | **12 / 0** |

  Note that reuse gets worse the **slower** you poll, which points at
  time-based expiry rather than a use count. And the failure mode is nasty:
  once in that state the device answers **401 with an empty `WWW-Authenticate`
  header**, refusing to issue a new challenge, with no way back short of
  starting over.

  So the client re-handshakes on every request, which is exactly what
  `curl --digest` does per invocation. That is why curl never failed during
  this investigation while the client did: the difference was not in the digest
  computation, it was in caching the challenge.

  **Diagnostic trap:** that challenge-less 401 looks exactly like invalid
  credentials. If the integration treats it as such, Home Assistant opens the
  reauth flow and prompts the user for their password for no reason. The
  symptom is indistinguishable from a wrong password, but the cause is nonce
  reuse.

## XML or JSON? Both, inconsistently

ISAPI mixes the two formats and **`?format=json` is not always honoured**. Door
control ignores the parameter and only accepts XML. So the client decides from
the first byte of the body, not from the URL.

Application errors arrive with **HTTP 200** and a `<ResponseStatus>` body —
checking only the status code lets failures slip through.

## Doorbell detection: the three channels

### Channel 1 — `alertStream`: BROKEN on this firmware

```
GET /ISAPI/Event/notification/alertStream
```

Observed response:

```
HTTP/1.1 200 OK
Content-Length: 40
Connection: close
Content-Type: application/xml

<?xml version="1.0" encoding="UTF-8"?>
```

Forty bytes, then it closes. It does **not** return `multipart/mixed`, does
**not** hold the connection open, and delivers **no** events.

**This is the root cause of every existing integration going silent on this
model.** `pergolafabio/Hikvision-Addons` uses the SDK's alarm channel
(`NET_DVR_SetupAlarmChan_V50`), which relies on the same mechanism; the result
is the well-known "11 entities and no doorbell event" report for the
DS-KB8113-IME1. It is not an integration bug — it is the device.

Because of this, the probe does **not** accept HTTP 200 as proof: it requires a
multipart `Content-Type`. See `IsapiClient._probe_alert_stream`.

### Channel 2 — `httpHosts` (reverse push)

```
GET|PUT|DELETE /ISAPI/Event/notification/httpHosts
```

The device POSTs events to a URL of ours. Capabilities:

| Field | Value |
|---|---|
| `hostNumber` | `1` (a single slot) |
| `protocolType` | `HTTP,HTTPS,EHome` |
| `parameterFormatType` | `XML,querystring,JSON` |
| `urlLen` | max **128** characters |

#### Verdict: accepts the configuration, but does NOT deliver call events

Tested with a real ring on 2026-09-01. The device was configured to point at a
capture server (confirmed by reading the configuration back), the doorbell was
rung, and polling recorded the entire state machine:

```
[11:38:47]  callStatus: idle   -> ring
[11:39:18]  callStatus: ring   -> onCall
[11:39:49]  callStatus: onCall -> idle
```

**Zero requests reached the webhook in that window.** The channel accepts the
configuration, confirms it on read-back, and pushes nothing. This is consistent
with `/ISAPI/Event/triggers`, which lists only `VMD`: intercom events do not go
through this firmware's event framework.

**Design consequence:** a push endpoint existing does not prove it delivers. The
integration **always** starts on polling and only promotes to push after a real
event arrives. Treating `supports_http_hosts` as "push works" would raise the
poll interval to 30s and leave the doorbell silent — exactly how the existing
integrations fail, by a different route.

Gotchas found the hard way:

- **An empty `<url></url>` is rejected** with `400 badXmlFormat`. The blank slot
  the device shows from the factory is generated by the firmware and **cannot be
  recreated through the API**.
- Consequently, **use `DELETE` to unconfigure**, not a PUT with zeroed values.
  DELETE answers `OK`.
- After the DELETE, `GET` starts answering `400 badParameters` (no entry
  exists). That is normal and equivalent to "no notification configured".
- There is only **1 slot**. If an NVR or HikConnect already uses it, the
  integration must warn rather than silently overwrite.

### Channel 3 — `callStatus` polling (always works)

```
GET /ISAPI/VideoIntercom/callStatus?format=json
→ {"CallStatus": {"status": "idle"}}
```

States, confirmed by the device's own capabilities:
`idle` → `ring` → `onCall` → `idle`.

**This is the only channel proven to work on this device**, so it is the
default, not the fallback. One light GET every ~1s.

The observed duration of a real ring (`ring` for 31s before timing out) leaves
enormous headroom for a 1s poll: no risk of missing the event.

## Call state semantics

**Reproduced.** A second capture, again with nobody answering:

```
[12:03:07.143]  idle   -> ring     (after 174.4s idle)
[12:03:38.376]  ring   -> onCall   (spent 31.2s in ring)
[12:04:09.573]  onCall -> idle     (spent 31.2s in onCall)
```

The durations match the first capture to the tenth of a second. These are
confirmed firmware timers:

| State | Duration |
|---|---|
| `ring` | **31.2s** |
| `onCall` | **31.2s** |

**`onCall` does NOT mean anyone answered.** In both captures nobody answered:
not on the indoor station, not in the phone app. The doorbell rang until it
stopped on its own. `deviceCommunication` confirms there is no external SIP
server configured (`0.0.0.0`), so the call goes straight to the indoor station.

### Answered or missed: it can be told, from the `ring` duration

The device exposes nowhere whether anyone answered. But the `ring` duration
gives it away. Four measurements:

| Scenario | `ring` | `onCall` |
|---|---|---|
| Nobody answered | 31.0s | 31.0s |
| Nobody answered (repeat) | 31.2s | 31.2s |
| Answered on the indoor station | **13.0s** | 15.7s |
| Answered in the phone app | **4.4s** | 7.0s |

`ring` only reaches 31.2s when the timeout runs out. Ending earlier means
someone answered. The separation between the groups is large (over 15s), so the
classification is robust.

**The app and the indoor station are indistinguishable from each other** via
`callStatus`: both simply end the `ring` early. You can tell *whether* it was
answered, not *where*.

Inherent limitation: answering at the very last second is indistinguishable
from not answering.

#### Where the 31.2s comes from (not what it looks like)

We initially assumed 31.2s was the door station's ring timeout. **It is not.**
The configured durations live at:

```
GET /ISAPI/VideoIntercom/operationTime
→ <maxRingTime>65</maxRingTime>   (min 65, max 255)
  <talkTime>90</talkTime>          (min 90, max 120)
  <messageTime>30</messageTime>    (min 30, max 60)
```

(Endpoint found by inspecting the web UI's own requests: the "Call Settings"
tab calls `operationTime`, a name you would never guess from the label.)

`maxRingTime` is 65s and **cannot even be configured below that**, so the
measured 31.2s comes from elsewhere: the indoor station giving up and falling
back to message mode. The 30s `messageTime` matches the 31.2s the `onCall`
state lasted on unanswered calls — meaning that `onCall` was the visitor
recording a message, not a conversation.

**Design consequence:** the "answered" threshold cannot be derived from
`maxRingTime`, because it depends on indoor-station behaviour the door station
does not expose. So it is a **configurable option** with the empirical value as
its default, and the real duration of every ring is written to the debug log to
make calibration possible.

## Door control

```
PUT /ISAPI/AccessControl/RemoteControl/door/1
Content-Type: application/xml

<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>
```

`GET .../door/capabilities` reports `doorNo min=1 max=1` and
`cmd opt="open,alwaysOpen,resume"`.

**XML only.** Sending JSON returns 400, even with `?format=json`.

## Relays

`GET /ISAPI/System/IO/capabilities` → `IOOutputPortNums = 1`.

This device has **one** relay. The SDK-based add-on assumes 2 by default
(`output_relays: 2`), which creates a phantom switch that does nothing. Reading
it from the device avoids that.

## Video and audio

`GET /ISAPI/Streaming/channels`:

| Channel | Resolution | Codec | Audio |
|---|---|---|---|
| `101` | 1920×1080 | H.264 | G.711ulaw |
| `102` | 1280×720 | H.264 | G.711ulaw |

- RTSP: `rtsp://user:password@IP:554/Streaming/Channels/101`
- Snapshot: `GET /ISAPI/Streaming/channels/101/picture`

Two-way audio:

```
GET /ISAPI/System/TwoWayAudio/channels
→ id=1, audioCompressionType=G.711ulaw, enabled=false
```

**It ships disabled.** go2rtc's `isapi://` backchannel fails silently unless it
is enabled first with a PUT on the channel.

## The capabilities lie

`GET /ISAPI/VideoIntercom/capabilities` advertises `isSupportWorkStatus=true`,
and yet:

```
GET /ISAPI/VideoIntercom/workStatus?format=json
→ HTTP 404, statusCode 4, subStatusCode invalidOperation
```

This is why the integration **probes behaviour** instead of trusting the flags
or the model number. It is the same principle that exposed the broken
`alertStream`.

## `/ISAPI/Event/triggers`

Lists **only** `VMD` (motion detection), with `notificationMethod: center`.
Intercom events do not appear in the standard event framework — one more reason
not to rely on the generic event path.

## SIP

`GET /ISAPI/VideoIntercom/deviceCommunication` shows no external SIP server
(`0.0.0.0` for both the `manage` and `sip` entries), and the web UI's "Enable
VOIP Gateway" is off. Enabling it is **not** needed for this integration:
doorbell detection uses `callStatus`, the door uses ISAPI, video uses RTSP, and
two-way audio uses the ISAPI backchannel. Pointing the station at a SIP gateway
risks diverting calls away from the indoor station and the phone app.
