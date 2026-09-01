"""ISAPI data models and pure parsers.

Nothing in this module does I/O: functions take bytes/str and return
dataclasses. That makes them trivially testable against the real fixtures
captured from the device under tests/fixtures/.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from .const import CALL_IDLE, CALL_STATES


class ParseError(Exception):
    """The device returned something we cannot interpret."""


def _strip_ns(tag: str) -> str:
    """Strip the XML namespace from a tag, if present."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def xml_to_dict(raw: str | bytes) -> dict[str, Any]:
    """Convert an ISAPI XML response into a dict, ignoring namespaces.

    ISAPI uses namespaces inconsistently across endpoints (some ver10, some
    ver20, some repeating xmlns on children), so we drop them entirely.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise ParseError(f"Invalid XML: {err}") from err
    return {_strip_ns(root.tag): _node_to_value(root)}


def _node_to_value(node: ET.Element) -> Any:
    children = list(node)
    if not children:
        text = (node.text or "").strip()
        return text
    result: dict[str, Any] = {}
    for child in children:
        key = _strip_ns(child.tag)
        value = _node_to_value(child)
        # Attributes get an @ prefix (e.g. <cmd opt="open,alwaysOpen"/>)
        if child.attrib:
            attrs = {f"@{k}": v for k, v in child.attrib.items()}
            if isinstance(value, dict):
                value = {**value, **attrs}
            elif value:
                value = {"#text": value, **attrs}
            else:
                value = attrs
        if key in result:
            # A repeated tag becomes a list (e.g. several <StreamingChannel>)
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


@dataclass(frozen=True)
class DeviceInfo:
    """Response of /ISAPI/System/deviceInfo."""

    name: str
    model: str
    serial_number: str
    mac_address: str
    firmware_version: str
    firmware_date: str
    device_type: str
    sub_device_type: str

    @classmethod
    def from_xml(cls, raw: str | bytes) -> DeviceInfo:
        data = xml_to_dict(raw).get("DeviceInfo", {})
        if not isinstance(data, dict) or "model" not in data:
            raise ParseError("deviceInfo has no 'model' field")
        return cls(
            name=data.get("deviceName", ""),
            model=data.get("model", ""),
            serial_number=data.get("serialNumber", ""),
            mac_address=data.get("macAddress", ""),
            firmware_version=data.get("firmwareVersion", ""),
            firmware_date=data.get("firmwareReleasedDate", ""),
            device_type=data.get("deviceType", ""),
            sub_device_type=data.get("subDeviceType", ""),
        )

    @property
    def unique_id(self) -> str:
        """Stable identifier for the device.

        We prefer the serial number; the MAC is a fallback for firmwares that
        return an empty serial.
        """
        return self.serial_number or self.mac_address


def parse_call_status(payload: dict[str, Any]) -> str:
    """Extract the call state from /ISAPI/VideoIntercom/callStatus.

    Returns 'idle', 'ring' or 'onCall'. An unknown state degrades to 'idle'
    rather than raising, so a newer firmware cannot take the integration down.
    """
    status = payload.get("CallStatus", {}).get("status")
    if status in CALL_STATES:
        return status
    return CALL_IDLE


@dataclass(frozen=True)
class OperationTime:
    """Call durations configured on the device (`operationTime`).

    These map to the web UI's "Call Settings" tab:
      maxRingTime -> Max. Ring Duration
      talkTime    -> Max. Call Duration
      messageTime -> Max. Message Duration
    """

    max_ring_time: int = 0
    talk_time: int = 0
    message_time: int = 0

    @classmethod
    def from_xml(cls, raw: str | bytes) -> OperationTime:
        data = xml_to_dict(raw).get("OperationTime", {})
        if not isinstance(data, dict):
            return cls()

        def num(key: str) -> int:
            try:
                return int(data.get(key, 0))
            except (TypeError, ValueError):
                return 0

        return cls(
            max_ring_time=num("maxRingTime"),
            talk_time=num("talkTime"),
            message_time=num("messageTime"),
        )


@dataclass(frozen=True)
class Capabilities:
    """What this particular device actually supports.

    Built at startup by probing the device rather than inferred from the model
    number. This is what lets the integration adapt across firmwares instead
    of breaking on them.
    """

    supports_call_status: bool = False
    supports_remote_open_door: bool = False
    supports_alert_stream: bool = False
    supports_http_hosts: bool = False
    supports_two_way_audio: bool = False
    two_way_audio_enabled: bool = False
    two_way_audio_channel: int = 1
    door_numbers: tuple[int, ...] = ()
    door_commands: tuple[str, ...] = ()
    output_ports: int = 0
    stream_channels: tuple[int, ...] = ()
    call_states: tuple[str, ...] = CALL_STATES
    operation_time: OperationTime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def push_candidate(self) -> str | None:
        """Which push channel is worth TRYING, if any.

        Deliberately not "the channel we will use". An endpoint existing does
        not mean it delivers: on the DS-KB8113-IME1(B) with firmware V2.2.60,
        `httpHosts` accepts the configuration, confirms it on read-back, and
        still POSTs nothing when the doorbell rings (verified with a real ring
        on 2026-09-01, the full state machine coming through polling and zero
        requests reaching the webhook).

        So the integration always starts in polling and only promotes to push
        after a real event arrives. See the coordinator.
        """
        if self.supports_http_hosts:
            return "http_host"
        if self.supports_alert_stream:
            return "alert_stream"
        return None


def parse_door_capabilities(raw: str | bytes) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Read doorNo (min/max) and the supported commands from door capabilities."""
    data = xml_to_dict(raw).get("RemoteControlDoor", {})
    if not isinstance(data, dict):
        return (), ()

    door_no = data.get("doorNo", {})
    doors: tuple[int, ...] = ()
    if isinstance(door_no, dict):
        try:
            lo = int(door_no.get("@min", 1))
            hi = int(door_no.get("@max", 1))
            doors = tuple(range(lo, hi + 1))
        except (TypeError, ValueError):
            doors = (1,)

    cmd = data.get("cmd", {})
    commands: tuple[str, ...] = ()
    if isinstance(cmd, dict) and "@opt" in cmd:
        commands = tuple(c.strip() for c in cmd["@opt"].split(",") if c.strip())

    return doors, commands


def parse_io_capabilities(raw: str | bytes) -> int:
    """Return the number of output ports (relays) on the device.

    The SDK-based add-on assumes 2 by default; the DS-KB8113-IME1(B) has 1.
    Reading it from the device avoids creating a phantom switch.
    """
    data = xml_to_dict(raw).get("IOCap", {})
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("IOOutputPortNums", 0))
    except (TypeError, ValueError):
        return 0


def parse_stream_channels(raw: str | bytes) -> tuple[int, ...]:
    """List the IDs of the enabled streaming channels."""
    data = xml_to_dict(raw).get("StreamingChannelList", {})
    if not isinstance(data, dict):
        return ()
    channels = data.get("StreamingChannel", [])
    if isinstance(channels, dict):
        channels = [channels]
    result = []
    for ch in channels:
        if not isinstance(ch, dict) or ch.get("enabled") != "true":
            continue
        try:
            result.append(int(ch["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(result)


def parse_two_way_audio(raw: str | bytes) -> tuple[bool, int, bool]:
    """Return (supported, channel_id, enabled) for TwoWayAudio.

    On the DS-KB8113-IME1(B) the channel exists but ships `enabled=false`; it
    needs a PUT enabling it before audio works at all.
    """
    data = xml_to_dict(raw).get("TwoWayAudioChannelList", {})
    if not isinstance(data, dict):
        return False, 1, False
    channels = data.get("TwoWayAudioChannel", [])
    if isinstance(channels, dict):
        channels = [channels]
    if not channels:
        return False, 1, False
    first = channels[0]
    if not isinstance(first, dict):
        return False, 1, False
    try:
        channel_id = int(first.get("id", 1))
    except (TypeError, ValueError):
        channel_id = 1
    return True, channel_id, first.get("enabled") == "true"


def parse_intercom_capabilities(raw: str | bytes) -> dict[str, bool]:
    """Convert VideoIntercomCap into a dict of boolean flags."""
    data = xml_to_dict(raw).get("VideoIntercomCap", {})
    if not isinstance(data, dict):
        return {}
    return {k: v == "true" for k, v in data.items() if isinstance(v, str)}
