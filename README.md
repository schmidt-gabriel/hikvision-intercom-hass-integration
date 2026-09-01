# Hikvision Video Intercom — Home Assistant integration

<img src="custom_components/hikvision_intercom/brand/icon.png" alt="Hikvision Video Intercom" width="128" align="right">

[![Validate](https://github.com/schmidt-gabriel/hikvision-intercom-hass-integration/actions/workflows/validate.yml/badge.svg)](https://github.com/schmidt-gabriel/hikvision-intercom-hass-integration/actions/workflows/validate.yml)

A **local** integration for Hikvision door stations, speaking **pure ISAPI over
HTTP**. No cloud, no account, no Hikvision SDK binary, and no companion server.

## Why another integration

Existing solutions fail on DS-KB series door stations for a concrete reason:
**Hikvision's standard event channel is broken on several firmwares**. On the
DS-KB8113-IME1(B) with firmware V2.2.60:

```
GET /ISAPI/Event/notification/alertStream
→ HTTP 200, Content-Length: 40, Connection: close
   <?xml version="1.0" encoding="UTF-8"?>
```

Forty bytes, then it closes. It does not return `multipart/mixed`, does not
hold the connection open, and delivers no events at all. Any integration that
depends on that channel — or on the SDK's alarm channel, which uses the same
mechanism — goes permanently silent. That is the source of the well-known
*"11 entities and no doorbell event"* report for this model. **It is not a bug
in those integrations; it is the device.**

This integration solves it by not betting on a single channel.

## Tested hardware

**Only one device has ever been tested with this integration.** Everything
below was verified against real hardware; anything not listed is unverified.

| | |
|---|---|
| Door station | **DS-KB8113-IME1(B)**, firmware V2.2.60 (build 231204) |
| Home Assistant | **2026.8.1** |

Verified by exercising the real device:

- Doorbell detection, with actual button presses
- Answered vs missed classification, across four real calls (two unanswered,
  one answered on the indoor station, one answered in the phone app)
- Door unlock, confirmed by the lock physically opening
- Camera snapshot, returning a valid JPEG
- Capability probing, digest authentication, and reconnection

Present in the code but **not** verified on hardware:

- The RTSP live stream (the snapshot path is verified, live view is not)
- The output relay switch
- The `answer`, `reject` and `hangup` call signals during an active call. The
  device returns `200 OK` to all three even while idle, so a successful
  response proves nothing. This is part of why `answer` and `hangup` ship
  disabled.
- The reboot button

### Other models

**No other model is covered.** Other Hikvision door stations (DS-KV, DS-KD, and
other DS-KB variants) may work, since the integration probes capabilities at
startup instead of assuming them, but none has been tried.

Indoor stations (DS-KH*) are **not supported**. A DS-KH6350-WTE1 sits on the
same installation and was never probed, so nothing is known about it here.

Firmware matters at least as much as the model: the findings in
[`docs/protocol.md`](docs/protocol.md), including the broken `alertStream` and
the digest nonce that cannot be reused, are specific to V2.2.60 and may differ
on other firmware.

If you try another model, the probe output is the useful thing to report:

```bash
cd custom_components/hikvision_intercom
HIK_PASSWORD=your-password python -m isapi.client <device-ip>
```

## Layered doorbell detection

At startup the device is **probed** — we test behaviour rather than trusting
the model number or the capability flags, which lie: this firmware advertises
`isSupportWorkStatus=true` and returns 404 on that endpoint.

| Channel | Mechanism | Status on DS-KB8113-IME1(B) fw V2.2.60 |
|---|---|---|
| `callStatus` | Light polling of `idle`/`ring`/`onCall` | **Works. This is the default.** |
| `httpHosts` | Device POSTs to an HA webhook | Accepts the config, delivers nothing |
| `alertStream` | HTTP `multipart/mixed` stream | Broken (200 with an empty body) |

The integration **always starts on polling** and only promotes to push after a
real event arrives. A push endpoint existing does not prove it delivers: on
this device `httpHosts` accepts the configuration, confirms it on read-back,
and still never POSTs when the doorbell rings (verified with a real ring, the
entire state machine coming through polling).

A **watchdog** guards the push channel: if it misses a ring that polling
caught, polling takes over automatically and a diagnostic sensor records the
degradation. That is what prevents the silent failure mode — the one where the
doorbell simply stops working and nobody notices.

## Requirements

- Home Assistant 2024.11 or newer (the bundled go2rtc is used for audio)
- ISAPI enabled on the door station
- A device user with live view and remote control permissions

## Installation

### HACS

1. HACS → Integrations → ⋮ → Custom repositories
2. Add this repository with category **Integration**
3. Install and restart Home Assistant
4. Settings → Devices & Services → Add Integration → *Hikvision Video Intercom*

### Manual

Copy `custom_components/hikvision_intercom` into your installation's
`custom_components/` directory and restart.

## Configuration

| Field | Value |
|---|---|
| IP address | The door station's address on your LAN |
| Username | usually `admin` |
| Password | the device password |
| HTTP port | `80` by default |

## Entities

| Platform | Entities |
|---|---|
| `event` | Doorbell: `ring` when pressed, then `answered` or `missed` |
| `binary_sensor` | Ringing, Call session (diagnostic), Push degraded (diagnostic) |
| `lock` | Door (momentary unlock) |
| `switch` | Output relay, Always unlocked |
| `button` | Reject, Reboot (Answer and Hang up ship disabled, see below) |
| `camera` | ISAPI snapshot + RTSP stream |

> **About the `onCall` state:** it does **not** mean anyone answered. In a
> capture with nobody answering (neither on the indoor station nor in the app),
> the device moved from `ring` to `onCall` and back to `idle` on its own, 31s in
> each state (measured on two independent captures, matching to the tenth of a
> second). Hence the entity is called "Call session" and is diagnostic. For
> doorbell automations use `event.doorbell`, which fires on the `idle -> ring`
> transition.
>
> **The window to answer is ~31 seconds** from the ring. Worth keeping in mind
> when building notifications: after that the device gives up on its own.

### Answer and Hang up are disabled by default

The `answer` and `hangup` buttons are created disabled. Answering from Home
Assistant today would pick the call up on the door station — the visitor sees
that someone answered — while no audio path exists on this side. They talk and
nobody hears them, which is worse than not answering at all. `hangup` only
exists to undo that.

Enable them in the entity settings once two-way audio is configured.

Note that the device answers `200 OK` to all three call signals (`answer`,
`reject`, `hangUp`) even when idle, so a successful response is not evidence
that the command did anything.

### Missed calls

`event.doorbell` fires `ring` when the button is pressed and, when the call
ends, either `answered` or `missed`, carrying a `ring_duration` attribute. This
enables the "someone rang and nobody answered" automation, which the device
offers no direct way to build: the distinction is inferred from the ring
duration (~31s means the timeout ran out; less than that means someone picked
up).

There is no way to tell **where** the call was answered: the indoor station and
the phone app behave identically.

The relay and door counts are **read from the device**, not assumed — this
model has one relay, while the SDK-based add-on assumes two and creates a
phantom switch.

## Diagnostic tooling

Full probe of a device, without starting Home Assistant:

```bash
cd custom_components/hikvision_intercom
HIK_PASSWORD=your-password python -m isapi.client 10.0.20.26
```

Follow the call state live (ring the doorbell and watch):

```bash
HIK_PASSWORD=your-password python -m isapi.client 10.0.20.26 --watch 60
```

The password is read from the environment on purpose — passing it as an
argument would leave it in shell history and in the process list.

## Two-way audio (not implemented)

Two-way audio is **not supported yet**, and the reason is worth recording
because it is not obvious.

The device side is ready: `/ISAPI/System/TwoWayAudio/channels` exposes a
G.711 µ-law channel, and go2rtc speaks that backchannel through its `isapi://`
source. The blocker is on the Home Assistant side. The bundled go2rtc writes
its own configuration from a hardcoded template on every start:

```
# This file is managed by Home Assistant
# Do not edit it manually
```

It ignores any user `go2rtc.yaml`, so there is no way to give a stream the
second `isapi://` source that a backchannel requires.

Two viable routes remain, neither implemented here:

1. **Self-hosted go2rtc.** Run the go2rtc add-on, define the stream with both
   sources, and point Home Assistant at it with `go2rtc: url: ...`. This works
   because the go2rtc integration leaves an existing stream untouched when it
   already lists the camera's `stream_source` as a producer, so the extra
   backchannel producer survives. The cost is that the self-hosted instance
   then serves every camera, not just the intercom.

2. **One-way audio over ISAPI.** `PUT /ISAPI/System/TwoWayAudio/channels/1/audioData`
   accepts G.711 µ-law, which is enough to play TTS or a recorded message
   through the door station speaker. Not a conversation, but it needs no extra
   infrastructure and stays inside the integration.

Note that the device's audio channel ships with `enabled=false`, so either
route has to enable it first.

Community guidance, for whoever picks this up: leave two-way disabled on RTSP
and use ISAPI only — the RTSP backchannel produces periodic static.

## Limitations

- The device reports no latch state, so `lock` and the relays are
  `assumed_state`.
- There is only **one** HTTP notification slot. If an NVR or HikConnect already
  uses it, the integration warns instead of silently overwriting.
- Indoor stations (DS-KH*) are not supported yet; the architecture is already
  multi-device to accommodate them. See [Tested hardware](#tested-hardware) for
  what has and has not been verified.

## Brand images

`custom_components/hikvision_intercom/brand/` holds the icon Home Assistant
shows for this integration. Since Home Assistant 2026.3.0, custom integrations
serve their own brand images from that folder through the brands proxy API, so
no submission to the `home-assistant/brands` repository is needed (and that
repository no longer accepts custom integration icons).

The folder is read at startup, so a newly added or replaced icon only shows
after a restart.

## Technical documentation

[`docs/protocol.md`](docs/protocol.md) records the real behaviour of ISAPI on
this hardware, including gotchas documented nowhere else: the broken
`alertStream`, digest nonces that cannot be reused, the empty `<url></url>`
rejected with `badXmlFormat`, `DELETE` as the only way to unconfigure the
webhook, and door control that only accepts XML.

## Acknowledgements

- [pergolafabio/Hikvision-Addons](https://github.com/pergolafabio/Hikvision-Addons)
  — ISAPI endpoint reference and the community's field work
- [maciej-or/hikvision_next](https://github.com/maciej-or/hikvision_next)
  — reference for a well-structured native ISAPI integration
- [AlexxIT/go2rtc](https://github.com/AlexxIT/go2rtc) — ISAPI backchannel

## License

MIT — see [LICENSE.md](LICENSE.md).
