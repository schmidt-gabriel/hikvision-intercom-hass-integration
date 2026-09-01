"""ISAPI client for Hikvision intercoms.

This module is deliberately free of Home Assistant: it only depends on aiohttp,
which makes it testable straight from the command line (see the __main__ block
at the bottom) without booting HA.

Verified against a DS-KB8113-IME1(B) running firmware V2.2.60.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import sys
import time
from typing import Any

import aiohttp

from . import const as c
from .digest import DigestAuth
from .models import (
    Capabilities,
    DeviceInfo,
    OperationTime,
    ParseError,
    parse_call_status,
    parse_door_capabilities,
    parse_intercom_capabilities,
    parse_io_capabilities,
    parse_stream_channels,
    parse_two_way_audio,
    xml_to_dict,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0

# Digest attempts before giving up. More than two because an expired nonce is
# not a credential failure and can happen back to back under fast polling.
MAX_AUTH_ATTEMPTS = 4


class IsapiError(Exception):
    """Generic failure talking to the device."""


class CannotConnect(IsapiError):
    """The device could not be reached."""


class AuthError(IsapiError):
    """Username or password rejected."""


class UnsupportedEndpoint(IsapiError):
    """The device does not implement this endpoint (404 / invalidOperation)."""


class IsapiClient:
    """Speaks ISAPI over HTTP to a Hikvision intercom."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        session: aiohttp.ClientSession | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self._auth = DigestAuth(username, password)
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def rtsp_url(
        self, channel: int = c.MAIN_STREAM_CHANNEL, *, with_credentials: bool = True
    ) -> str:
        """RTSP URL for a channel. Only embed credentials for local destinations."""
        creds = f"{self.username}:{self._password}@" if with_credentials else ""
        return (
            f"rtsp://{creds}{self.host}:{c.DEFAULT_RTSP_PORT}{c.RTSP_PATH.format(channel=channel)}"
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we own it. Idempotent and never raises."""
        if self._owns_session and self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                _LOGGER.debug("Ignored error while closing the session", exc_info=True)
        self._session = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: str | bytes | None = None,
        content_type: str = "application/xml",
        raw: bool = False,
    ) -> Any:
        """Make an authenticated request and return a dict (or bytes if raw).

        Digest is done by hand: send without a header, read the challenge from
        the 401, retry. See the comment below on why the challenge is never
        reused across requests.
        """
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}
        if data is not None:
            headers["Content-Type"] = content_type

        # A fresh handshake on every request, on purpose.
        #
        # This firmware's nonce does not survive reuse. Measured 2026-09-01,
        # reusing the challenge across requests gives 6 OK / 6 failures at
        # 0.5s, and gets WORSE the slower you poll (2 OK / 10 failures at 2s),
        # which points at time-based expiry. Worse still, once it enters that
        # state the device starts answering 401 with an EMPTY WWW-Authenticate
        # header -- it refuses to issue a new challenge, and there is no way
        # back without starting over.
        #
        # Re-handshaking every time: 12/12 at 0.5s, 1s and 2s. That is exactly
        # what `curl --digest` does per invocation, and it is why curl never
        # failed while this client did. It costs one extra round-trip per read;
        # a callStatus GET is cheap and the reliability is worth far more.
        self._auth.reset()
        used_fresh_challenge = False

        for _attempt in range(MAX_AUTH_ATTEMPTS):
            if self._auth.has_challenge:
                headers["Authorization"] = self._auth.build_header(method, path)

            try:
                async with session.request(
                    method, url, data=data, headers=headers, timeout=self._timeout
                ) as resp:
                    body = await resp.read()

                    if resp.status == 401:
                        challenge = resp.headers.get("WWW-Authenticate", "")
                        if not self._auth.set_challenge(challenge):
                            self._auth.reset()
                            raise AuthError(f"Authentication rejected by {self.host}")

                        # stale=true means "nonce expired, credentials fine".
                        # Under 1s polling the device rotates the nonce often,
                        # so this is routine, not failure -- treating it as a
                        # bad password would make HA prompt for reauth for no
                        # reason at all.
                        if self._auth.stale or not used_fresh_challenge:
                            used_fresh_challenge = True
                            continue

                        self._auth.reset()
                        raise AuthError(f"Authentication rejected by {self.host}")

                    if resp.status == 404:
                        raise UnsupportedEndpoint(f"{path} does not exist on this device")

                    if resp.status >= 400:
                        raise IsapiError(f"{method} {path} returned HTTP {resp.status}")

                    if raw:
                        return body
                    return self._decode(path, body)

            except aiohttp.ClientError as err:
                raise CannotConnect(f"Could not reach {self.host}: {err}") from err
            except TimeoutError as err:
                raise CannotConnect(f"Timed out talking to {self.host}") from err

        raise AuthError(f"Authentication rejected by {self.host}")

    @staticmethod
    def _decode(path: str, body: bytes) -> Any:
        """Decode the body as JSON or XML.

        ISAPI mixes both: some endpoints ignore ?format=json and return XML
        anyway (door control is one), so we decide from the content rather
        than from the URL.
        """
        stripped = body.lstrip()
        if not stripped:
            return {}
        if stripped[:1] in (b"{", b"["):
            try:
                return json.loads(body)
            except json.JSONDecodeError as err:
                raise ParseError(f"Invalid JSON from {path}: {err}") from err
        parsed = xml_to_dict(body)
        # ISAPI signals application errors with HTTP 200 + ResponseStatus.
        status = parsed.get("ResponseStatus")
        if isinstance(status, dict) and status.get("statusString") not in (None, "OK"):
            sub = status.get("subStatusCode", "")
            if sub in ("invalidOperation", "notSupport"):
                raise UnsupportedEndpoint(f"{path}: {sub}")
            raise IsapiError(f"{path}: {status.get('statusString')} ({sub})")
        return parsed

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_device_info(self) -> DeviceInfo:
        return DeviceInfo.from_xml(await self.request("GET", c.DEVICE_INFO, raw=True))

    async def get_call_status(self) -> str:
        """Return 'idle', 'ring' or 'onCall'."""
        return parse_call_status(await self.request("GET", c.CALL_STATUS))

    async def get_snapshot(self, channel: int = c.MAIN_STREAM_CHANNEL) -> bytes:
        return await self.request("GET", c.SNAPSHOT.format(channel=channel), raw=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def open_door(self, door_no: int = 1, cmd: str = c.DOOR_OPEN) -> None:
        """Trigger the door lock.

        This endpoint only accepts XML -- sending JSON returns 400, even with
        ?format=json in the URL.
        """
        if cmd not in (c.DOOR_OPEN, c.DOOR_ALWAYS_OPEN, c.DOOR_RESUME):
            raise ValueError(f"Invalid door command: {cmd}")
        body = f"<RemoteControlDoor><cmd>{cmd}</cmd></RemoteControlDoor>"
        await self.request("PUT", c.DOOR_CONTROL.format(door_no=door_no), data=body)

    async def _call_signal(self, cmd_type: str) -> None:
        body = json.dumps({"CallSignal": {"cmdType": cmd_type}})
        await self.request("PUT", c.CALL_SIGNAL, data=body, content_type="application/json")

    async def answer_call(self) -> None:
        await self._call_signal(c.CMD_ANSWER)

    async def reject_call(self) -> None:
        await self._call_signal(c.CMD_REJECT)

    async def hangup_call(self) -> None:
        await self._call_signal(c.CMD_HANGUP)

    async def trigger_output(self, output_no: int = 1, state: str = "high") -> None:
        body = f"<IOPortData><outputState>{state}</outputState></IOPortData>"
        await self.request("PUT", c.OUTPUT_TRIGGER.format(output_no=output_no), data=body)

    async def set_two_way_audio_enabled(self, enabled: bool, channel: int = 1) -> None:
        """Enable or disable the two-way audio channel.

        Needed because the DS-KB8113-IME1(B) ships with enabled=false, and
        go2rtc's ISAPI backchannel fails silently when it is off.
        """
        body = (
            f'<TwoWayAudioChannel version="2.0">'
            f"<id>{channel}</id>"
            f"<enabled>{'true' if enabled else 'false'}</enabled>"
            f"<audioCompressionType>G.711ulaw</audioCompressionType>"
            f"</TwoWayAudioChannel>"
        )
        await self.request("PUT", c.TWO_WAY_AUDIO_CHANNEL.format(channel=channel), data=body)

    async def reboot(self) -> None:
        await self.request("PUT", c.REBOOT)

    # ------------------------------------------------------------------
    # Capability probing
    # ------------------------------------------------------------------

    async def probe_capabilities(self) -> Capabilities:
        """Discover what this device actually does.

        We probe behaviour rather than trusting the model number or the
        capability flags -- which lie: this firmware advertises
        isSupportWorkStatus=true and still returns 404 on that endpoint.
        """
        notes: list[str] = []

        async def probe(coro, label: str, default):
            try:
                return await coro
            except UnsupportedEndpoint:
                notes.append(f"{label}: not supported")
            except IsapiError as err:
                notes.append(f"{label}: failed ({err})")
            except ParseError as err:
                notes.append(f"{label}: unreadable response ({err})")
            return default

        call_status = await probe(self.get_call_status(), "callStatus", None)

        intercom = await probe(
            self.request("GET", c.INTERCOM_CAPABILITIES, raw=True),
            "VideoIntercom/capabilities",
            None,
        )
        flags = parse_intercom_capabilities(intercom) if intercom else {}

        door_raw = await probe(
            self.request("GET", c.DOOR_CAPABILITIES, raw=True), "door/capabilities", None
        )
        doors, commands = parse_door_capabilities(door_raw) if door_raw else ((), ())

        io_raw = await probe(
            self.request("GET", c.IO_CAPABILITIES, raw=True), "IO/capabilities", None
        )
        outputs = parse_io_capabilities(io_raw) if io_raw else 0

        streams_raw = await probe(
            self.request("GET", c.STREAMING_CHANNELS, raw=True), "Streaming/channels", None
        )
        streams = parse_stream_channels(streams_raw) if streams_raw else ()

        audio_raw = await probe(
            self.request("GET", c.TWO_WAY_AUDIO_CHANNELS, raw=True), "TwoWayAudio", None
        )
        has_audio, audio_ch, audio_on = (
            parse_two_way_audio(audio_raw) if audio_raw else (False, 1, False)
        )

        # Probe /capabilities rather than the config itself: with no entry
        # registered (after a DELETE, say) the config GET answers 400
        # badParameters, which would make a perfectly usable slot look
        # unsupported.
        http_hosts = await probe(
            self.request("GET", c.HTTP_HOSTS_CAPABILITIES, raw=True), "httpHosts", None
        )

        op_raw = await probe(self.request("GET", c.OPERATION_TIME, raw=True), "operationTime", None)
        operation_time = OperationTime.from_xml(op_raw) if op_raw else None

        alert_stream_ok = await self._probe_alert_stream(notes)

        return Capabilities(
            supports_call_status=call_status is not None,
            supports_remote_open_door=flags.get("isSupportRemoteOpenDoor", bool(commands)),
            supports_alert_stream=alert_stream_ok,
            supports_http_hosts=http_hosts is not None,
            supports_two_way_audio=has_audio,
            two_way_audio_enabled=audio_on,
            two_way_audio_channel=audio_ch,
            door_numbers=doors,
            door_commands=commands,
            output_ports=outputs,
            stream_channels=streams,
            operation_time=operation_time,
            notes=notes,
        )

    async def _probe_alert_stream(self, notes: list[str]) -> bool:
        """Check whether alertStream actually streams.

        Checking the status code is not enough: on the DS-KB8113-IME1(B) with
        firmware V2.2.60 this endpoint answers HTTP 200 with a 40-byte body
        (just the XML declaration) and closes immediately, instead of holding
        a multipart stream open. That is precisely why alertStream/SDK-based
        integrations go silent on this model. We only accept the channel when
        a multipart Content-Type comes back.
        """
        session = await self._ensure_session()
        url = f"{self.base_url}{c.ALERT_STREAM}"
        headers = {"Accept": "multipart/mixed"}
        try:
            for attempt in (1, 2):
                if self._auth.has_challenge:
                    headers["Authorization"] = self._auth.build_header("GET", c.ALERT_STREAM)
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if (
                        resp.status == 401
                        and attempt == 1
                        and self._auth.set_challenge(resp.headers.get("WWW-Authenticate", ""))
                    ):
                        continue
                    if resp.status != 200:
                        notes.append(f"alertStream: HTTP {resp.status}")
                        return False
                    ctype = resp.headers.get("Content-Type", "")
                    if "multipart" not in ctype.lower():
                        notes.append(
                            f"alertStream: broken on this firmware (Content-Type {ctype!r}, "
                            "no multipart stream) -- falling back to push/polling"
                        )
                        return False
                    return True
        except (TimeoutError, aiohttp.ClientError) as err:
            notes.append(f"alertStream: unavailable ({err})")
        return False


# ----------------------------------------------------------------------
# Manual test, without Home Assistant. From custom_components/hikvision_intercom/:
#
#   HIK_PASSWORD=... python -m isapi.client 10.0.20.26
#   HIK_PASSWORD=... python -m isapi.client 10.0.20.26 --watch 60
#   HIK_PASSWORD=... python -m isapi.client 10.0.20.26 --open-door
#
# Running it this way (rather than -m custom_components...) is deliberate: the
# parent package imports Home Assistant, and the whole point of this block is
# to exercise the client without it.
# ----------------------------------------------------------------------


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Exercise the ISAPI client against a device.")
    parser.add_argument("host")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--open-door", action="store_true", help="ACTUALLY TRIGGERS THE LOCK")
    parser.add_argument(
        "--watch", type=int, metavar="SECONDS", help="Follow callStatus for N seconds"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    password = os.environ.get("HIK_PASSWORD")
    if not password:
        print(
            "Set HIK_PASSWORD in the environment (keeps it out of shell history).", file=sys.stderr
        )
        return 2

    client = IsapiClient(args.host, args.username, password, port=args.port)
    try:
        info = await client.get_device_info()
        print(f"Device   : {info.model}  fw {info.firmware_version} ({info.firmware_date})")
        print(f"Serial   : {info.serial_number}")
        print(f"Type     : {info.device_type}/{info.sub_device_type}")

        caps = await client.probe_capabilities()
        print("\nProbed capabilities:")
        print(f"  callStatus       : {caps.supports_call_status}")
        print(
            f"  open door        : {caps.supports_remote_open_door}  "
            f"doors={caps.door_numbers} cmds={caps.door_commands}"
        )
        print(f"  output relays    : {caps.output_ports}")
        print(f"  stream channels  : {caps.stream_channels}")
        print(
            f"  two-way audio    : {caps.supports_two_way_audio} "
            f"(channel {caps.two_way_audio_channel}, enabled={caps.two_way_audio_enabled})"
        )
        print(f"  alertStream      : {caps.supports_alert_stream}")
        print(f"  httpHosts        : {caps.supports_http_hosts}")
        print(
            f"  >> push candidate: {caps.push_candidate or 'none'} "
            "(the doorbell always starts on polling)"
        )
        if caps.operation_time:
            ot = caps.operation_time
            print(
                f"  device durations : max ring {ot.max_ring_time}s, "
                f"max talk {ot.talk_time}s, message {ot.message_time}s"
            )
        if caps.notes:
            print("\nNotes:")
            for note in caps.notes:
                print(f"  - {note}")

        print(f"\nCurrent call status: {await client.get_call_status()}")

        if args.watch:
            print(f"\nFollowing callStatus for {args.watch}s. Ring the doorbell.")
            print("We record how long the device spends in each state: that is how you")
            print("tell a firmware timer apart from a human answering.\n")
            last: str | None = None
            entered = time.monotonic()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + args.watch
            while loop.time() < deadline:
                status = await client.get_call_status()
                if status != last:
                    now = time.monotonic()
                    stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    held = "" if last is None else f"  (spent {now - entered:5.1f}s in {last})"
                    print(f"  [{stamp}]  {last} -> {status}{held}")
                    last, entered = status, now
                await asyncio.sleep(0.5)
            if last is not None:
                print(f"\n  window over; current state: {last}")

        if args.open_door:
            print("\nOpening the door...")
            await client.open_door()
            print("Open command sent.")
    except IsapiError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
