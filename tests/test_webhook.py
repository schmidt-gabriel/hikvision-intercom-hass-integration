"""Channel 2 (httpHosts) tests: building the config and parsing the push."""

from __future__ import annotations

import json
import pathlib

import pytest
from isapi.webhook import (
    build_host_config,
    describe_existing_host,
    parse_push_event,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class TestBuildHostConfig:
    def test_produces_config_the_device_accepts(self):
        """This exact shape was accepted by the device (HTTP 200 + read-back)."""
        xml = build_host_config("/api/webhook/abc", "10.0.10.12", 8123)
        assert "<url>/api/webhook/abc</url>" in xml
        assert "<ipAddress>10.0.10.12</ipAddress>" in xml
        assert "<portNo>8123</portNo>" in xml
        # The device rejects it unless xmlns is on the root element.
        assert 'xmlns="http://www.isapi.org/ver20/XMLSchema"' in xml

    def test_rejects_url_over_device_limit(self):
        """The device declares urlLen max=128; failing early gives a readable error."""
        with pytest.raises(ValueError, match="128"):
            build_host_config("/" + "x" * 200, "10.0.0.1", 8123)


class TestDescribeExistingHost:
    def test_factory_blank_slot_counts_as_free(self):
        """The factory state (0.0.0.0:0, empty url) counts as a free slot."""
        assert describe_existing_host(fixture_bytes("http_hosts.xml")) is None

    def test_reports_a_configured_host(self):
        """An occupied slot must be reported, not silently overwritten.

        There is only one slot; overwriting an NVR's configuration would break
        its recording without the user ever noticing.
        """
        xml = build_host_config("/hik", "192.168.1.50", 9000)
        assert describe_existing_host(xml) == "192.168.1.50:9000/hik"

    def test_survives_error_response(self):
        """After a DELETE the GET answers ResponseStatus, not the list."""
        err = b"<ResponseStatus><statusString>Invalid Content</statusString></ResponseStatus>"
        assert describe_existing_host(err) is None

    def test_survives_garbage(self):
        assert describe_existing_host(b"not xml at all") is None


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParsePushEvent:
    def test_extracts_call_status_from_json(self):
        body = json.dumps({"CallStatus": {"status": "ring"}}).encode()
        assert parse_push_event(body)["call_status"] == "ring"

    def test_extracts_call_status_from_nested_json(self):
        """Firmwares nest at different depths; we flatten and search."""
        body = json.dumps({"Event": {"Detail": {"status": "onCall"}}}).encode()
        assert parse_push_event(body)["call_status"] == "onCall"

    def test_extracts_event_type(self):
        body = json.dumps({"eventType": "VMD", "ipAddress": "10.0.20.26"}).encode()
        assert parse_push_event(body)["event_type"] == "VMD"

    def test_maps_doorbell_event_type_to_ring(self):
        body = json.dumps({"eventType": "doorbell"}).encode()
        assert parse_push_event(body)["call_status"] == "ring"

    def test_parses_xml_payload(self):
        """parameterFormatType may be XML instead of JSON."""
        body = b"<EventNotificationAlert><eventType>VMD</eventType></EventNotificationAlert>"
        assert parse_push_event(body)["event_type"] == "VMD"

    def test_never_raises_on_garbage(self):
        """An odd payload must not take the integration down."""
        for body in (b"", b"\x00\x01\x02", b"{broken", b"<unclosed"):
            result = parse_push_event(body)
            assert isinstance(result, dict)

    def test_unknown_status_is_not_reported(self):
        """We do not invent a call_status when it cannot be determined."""
        body = json.dumps({"eventType": "somethingElse"}).encode()
        assert "call_status" not in parse_push_event(body)
