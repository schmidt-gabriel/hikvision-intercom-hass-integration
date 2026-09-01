"""Tests for the pure parsers, using real responses from the device.

The fixtures under tests/fixtures/ were captured live from a
DS-KB8113-IME1(B) on firmware V2.2.60, so these tests pin behaviour against
what the actual hardware returns -- not against what Hikvision's documentation
claims.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from isapi.models import (
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

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestDeviceInfo:
    def test_parses_real_response(self):
        info = DeviceInfo.from_xml(fixture("device_info.xml"))
        assert info.model == "DS-KB8113-IME1(B)"
        assert info.firmware_version == "V2.2.60"
        assert info.mac_address == "a4:d5:c2:00:00:01"
        assert info.device_type == "VIS"
        assert info.sub_device_type == "villaDoorStation"

    def test_unique_id_prefers_serial(self):
        info = DeviceInfo.from_xml(fixture("device_info.xml"))
        assert info.unique_id == info.serial_number

    def test_unique_id_falls_back_to_mac(self):
        """Some firmwares return an empty serial; the MAC covers for it."""
        xml = fixture("device_info.xml").replace(
            b"<serialNumber>DS-KB8113-IME1(B)0120240101EXAMPLE0000</serialNumber>",
            b"<serialNumber></serialNumber>",
        )
        assert DeviceInfo.from_xml(xml).unique_id == "a4:d5:c2:00:00:01"

    def test_rejects_garbage(self):
        with pytest.raises(ParseError):
            DeviceInfo.from_xml(b"not xml at all")

    def test_rejects_xml_without_model(self):
        with pytest.raises(ParseError):
            DeviceInfo.from_xml(b"<DeviceInfo><deviceName>x</deviceName></DeviceInfo>")


class TestCallStatus:
    def test_parses_real_response(self):
        assert parse_call_status(json.loads(fixture("call_status.json"))) == "idle"

    @pytest.mark.parametrize("state", ["idle", "ring", "onCall"])
    def test_accepts_all_documented_states(self, state):
        assert parse_call_status({"CallStatus": {"status": state}}) == state

    def test_unknown_state_degrades_to_idle(self):
        """A newer firmware with an unknown state must not take the integration down."""
        assert parse_call_status({"CallStatus": {"status": "espiando"}}) == "idle"
        assert parse_call_status({}) == "idle"


class TestDoorCapabilities:
    def test_parses_real_response(self):
        doors, commands = parse_door_capabilities(fixture("door_capabilities.xml"))
        assert doors == (1,)
        assert commands == ("open", "alwaysOpen", "resume")

    def test_expands_door_range(self):
        xml = b"""<RemoteControlDoor><doorNo min="1" max="4"/>
                  <cmd opt="open"/></RemoteControlDoor>"""
        doors, _ = parse_door_capabilities(xml)
        assert doors == (1, 2, 3, 4)


class TestIoCapabilities:
    def test_real_device_has_one_relay(self):
        """The SDK-based add-on assumes 2 relays; this device has 1.

        Reading it from the device is what avoids a phantom switch.
        """
        assert parse_io_capabilities(fixture("io_capabilities.xml")) == 1

    def test_missing_field_yields_zero(self):
        assert parse_io_capabilities(b"<IOCap></IOCap>") == 0


class TestStreamChannels:
    def test_parses_real_response(self):
        assert parse_stream_channels(fixture("streaming_channels.xml")) == (101, 102)

    def test_skips_disabled_channels(self):
        xml = b"""<StreamingChannelList>
          <StreamingChannel><id>101</id><enabled>true</enabled></StreamingChannel>
          <StreamingChannel><id>102</id><enabled>false</enabled></StreamingChannel>
        </StreamingChannelList>"""
        assert parse_stream_channels(xml) == (101,)

    def test_single_channel_is_not_treated_as_list(self):
        """A single tag becomes a dict, not a list -- the parser must accept both."""
        xml = b"<StreamingChannelList><StreamingChannel><id>101</id>"
        xml += b"<enabled>true</enabled></StreamingChannel></StreamingChannelList>"
        assert parse_stream_channels(xml) == (101,)


class TestTwoWayAudio:
    def test_real_device_supports_but_ships_disabled(self):
        """The DS-KB8113-IME1(B) ships with the channel disabled.

        Without enabling it first, go2rtc's ISAPI backchannel fails silently.
        """
        supported, channel, enabled = parse_two_way_audio(fixture("two_way_audio.xml"))
        assert supported is True
        assert channel == 1
        assert enabled is False


class TestIntercomCapabilities:
    def test_parses_real_flags(self):
        flags = parse_intercom_capabilities(fixture("intercom_capabilities.xml"))
        assert flags["isSupportRemoteOpenDoor"] is True
        assert flags["isSupportAlarmControlByPhone"] is False

    def test_capabilities_can_lie(self):
        """Records a fact discovered while probing.

        The device advertises isSupportWorkStatus=true and still returns 404 on
        /ISAPI/VideoIntercom/workStatus. That is why the integration probes
        behaviour instead of trusting these flags.
        """
        flags = parse_intercom_capabilities(fixture("intercom_capabilities.xml"))
        assert flags["isSupportWorkStatus"] is True  # and yet the endpoint 404s


class TestXmlToDict:
    def test_handles_repeated_tags_as_list(self):
        result = xml_to_dict(b"<r><i>a</i><i>b</i></r>")
        assert result["r"]["i"] == ["a", "b"]

    def test_captures_attributes_with_at_prefix(self):
        result = xml_to_dict(b'<r><cmd opt="open,resume"/></r>')
        assert result["r"]["cmd"]["@opt"] == "open,resume"

    def test_ignores_namespaces(self):
        """ISAPI mixes ver10 and ver20 across endpoints; we ignore both."""
        result = xml_to_dict(b'<r xmlns="http://www.isapi.org/ver20/XMLSchema"><a>1</a></r>')
        assert result == {"r": {"a": "1"}}


class TestPushCandidate:
    """Channel selection is the heart of the integration; it deserves explicit tests."""

    def test_prefers_http_hosts_over_alert_stream(self):
        caps = Capabilities(supports_http_hosts=True, supports_alert_stream=True)
        assert caps.push_candidate == "http_host"

    def test_falls_back_to_alert_stream(self):
        assert Capabilities(supports_alert_stream=True).push_candidate == "alert_stream"

    def test_no_push_channel_yields_none(self):
        """With no push channel, `None`. Polling is what guarantees the doorbell.

        Note this is only the candidate to TRY, not the channel in use: the
        coordinator always starts on polling and only promotes once a push
        event actually arrives. On the DS-KB8113-IME1(B), httpHosts accepts the
        configuration and never delivers, so trusting it up front would leave
        the doorbell silent -- exactly how the existing integrations fail.
        """
        assert Capabilities().push_candidate is None


class TestObservedStateMachine:
    """Pins the real behaviour observed on the device on 2026-09-01.

    Sequence captured with the doorbell rung and NOBODY answering (neither on
    the indoor station nor in the phone app):

        11:38:47  idle   -> ring
        11:39:18  ring   -> onCall
        11:39:49  onCall -> idle

    Both intervals came out at exactly 31s, pointing at firmware timers. The
    lesson this pins down: `onCall` is not evidence that anyone answered, so
    only the transition into `ring` may be treated as "the doorbell rang".
    """

    OBSERVED = ["idle", "ring", "onCall", "idle"]

    # Duration of each state, measured on two independent captures (11:38 and
    # 12:03 on 2026-09-01), matching to the tenth of a second. These are
    # firmware timers, not human action.
    RING_TIMEOUT_SECONDS = 31.2

    def test_every_observed_state_parses(self):
        for state in self.OBSERVED:
            assert parse_call_status({"CallStatus": {"status": state}}) == state

    def test_only_one_ring_transition_in_the_sequence(self):
        """One ring must produce exactly one doorbell event.

        If `onCall` also counted as a ring, a single visitor would generate two
        notifications.
        """
        # no strict: by construction the offset lists differ by one element
        transitions = list(zip(self.OBSERVED, self.OBSERVED[1:], strict=False))
        rings = [(a, b) for a, b in transitions if b == "ring" and a != "ring"]
        assert len(rings) == 1
        assert rings[0] == ("idle", "ring")

    def test_poll_interval_fits_comfortably_in_the_ring_window(self):
        """The poll must fit comfortably inside the ~31s `ring` window.

        Pins the relationship between poll interval and measured duration: if
        anyone raises the interval near 31s, catching the doorbell becomes a
        matter of luck.
        """
        from const import DEFAULT_POLL_INTERVAL

        assert DEFAULT_POLL_INTERVAL <= self.RING_TIMEOUT_SECONDS / 10


class TestOperationTime:
    """Call durations read from the device (the web UI's "Call Settings" tab).

    Endpoint found by inspecting the web UI's own requests: the Call Settings
    tab calls `/ISAPI/VideoIntercom/operationTime`, a name you would never
    guess from the label.
    """

    def test_parses_real_response(self):
        ot = OperationTime.from_xml(fixture("operation_time.xml"))
        assert ot.max_ring_time == 65
        assert ot.talk_time == 90
        assert ot.message_time == 30

    def test_measured_ring_is_not_the_configured_max_ring_time(self):
        """Records a misreading that nearly became a bug.

        We assumed the 31.2s measured in the `ring` state was the device's
        timeout. It is not: `maxRingTime` is 65s and cannot even be configured
        below that. The 31.2s comes from the indoor station giving up and
        falling back to message mode -- and the 30s `messageTime` matches the
        31.2s measured in the `onCall` state.

        Consequence: the "answered" threshold must NOT be derived from
        maxRingTime. Hence a configurable option with the empirical default.
        """
        ot = OperationTime.from_xml(fixture("operation_time.xml"))
        measured_ring_seconds = 31.2
        assert measured_ring_seconds < ot.max_ring_time
        # the message time, on the other hand, explains the observed `onCall`
        assert abs(ot.message_time - measured_ring_seconds) < 2.0

    def test_missing_fields_degrade_to_zero(self):
        assert OperationTime.from_xml(b"<OperationTime/>") == OperationTime()

    def test_survives_garbage_shape(self):
        assert OperationTime.from_xml(b"<Other><x>1</x></Other>") == OperationTime()


class TestRtspUrl:
    """Covers IsapiClient.rtsp_url.

    Regression test for a real crash: rtsp_url referenced DEFAULT_RTSP_PORT
    from isapi/const.py while the constant lived in the component's const.py.
    Nothing in the suite called rtsp_url, so it only surfaced when Home
    Assistant set up the camera platform and the entity failed to add.
    """

    def _client(self):
        from isapi.client import IsapiClient

        return IsapiClient("10.0.0.9", "admin", "secret")

    def test_builds_url_with_credentials(self):
        url = self._client().rtsp_url(101)
        assert url == "rtsp://admin:secret@10.0.0.9:554/Streaming/Channels/101"

    def test_can_omit_credentials(self):
        url = self._client().rtsp_url(101, with_credentials=False)
        assert url == "rtsp://10.0.0.9:554/Streaming/Channels/101"
        assert "secret" not in url

    def test_defaults_to_the_main_stream(self):
        from isapi.const import MAIN_STREAM_CHANNEL

        assert str(MAIN_STREAM_CHANNEL) in self._client().rtsp_url()
