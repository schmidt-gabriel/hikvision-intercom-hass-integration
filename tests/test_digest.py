"""Digest authentication tests, against a real challenge from the device."""

from __future__ import annotations

import hashlib
import re

import pytest
from isapi.digest import (
    DigestAuth,
    parse_challenge,
)

# Real challenge issued by the DS-KB8113-IME1(B) on 2026-09-01.
REAL_CHALLENGE = (
    'Digest qop="auth", realm="DS-0CED27ED", '
    'nonce="MGJmMjYxMzJlZDRlMzI3YzIyMWIyMzU2ZDVjM2E3NGI=", '
    'stale="false", opaque="", domain="::"'
)


def parse_header(header: str) -> dict[str, str]:
    assert header.startswith("Digest ")
    return {
        m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
        for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', header[7:])
    }


class TestParseChallenge:
    def test_parses_real_challenge(self):
        c = parse_challenge(REAL_CHALLENGE)
        assert c["realm"] == "DS-0CED27ED"
        assert c["qop"] == "auth"
        assert c["nonce"].endswith("=")

    def test_ignores_non_digest_scheme(self):
        assert parse_challenge("Basic realm=x") == {}
        assert parse_challenge("") == {}


class TestDigestAuth:
    def test_response_matches_rfc2617(self):
        """Check the computation against the RFC's MD5 done by hand."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        parts = parse_header(auth.build_header("GET", "/ISAPI/System/deviceInfo"))

        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
        ha1 = md5("admin:DS-0CED27ED:secret")
        ha2 = md5("GET:/ISAPI/System/deviceInfo")
        expected = md5(f"{ha1}:{parts['nonce']}:{parts['nc']}:{parts['cnonce']}:auth:{ha2}")
        assert parts["response"] == expected

    def test_nonce_count_increments(self):
        """nc must increment per request on the same nonce, or the device rejects it."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        ncs = [parse_header(auth.build_header("GET", "/x"))["nc"] for _ in range(3)]
        assert ncs == ["00000001", "00000002", "00000003"]

    def test_cnonce_differs_between_requests(self):
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        a = parse_header(auth.build_header("GET", "/x"))["cnonce"]
        b = parse_header(auth.build_header("GET", "/x"))["cnonce"]
        assert a != b

    def test_uri_must_match_request_line_exactly(self):
        """Query string included -- Hikvision devices reject a mismatch."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        path = "/ISAPI/VideoIntercom/callStatus?format=json"
        assert parse_header(auth.build_header("GET", path))["uri"] == path

    def test_nc_and_qop_are_unquoted(self):
        """The RFC requires nc/qop unquoted; some firmwares reject them quoted."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        header = auth.build_header("GET", "/x")
        assert "nc=00000001," in header
        assert "qop=auth," in header

    def test_empty_opaque_is_omitted(self):
        """The device sends opaque=""; echoing it back empty confuses some firmwares."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        assert "opaque" not in auth.build_header("GET", "/x")

    def test_reset_clears_challenge(self):
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        auth.reset()
        assert not auth.has_challenge
        with pytest.raises(ValueError):
            auth.build_header("GET", "/x")

    def test_rejects_challenge_without_nonce(self):
        auth = DigestAuth("admin", "secret")
        assert auth.set_challenge('Digest realm="x"') is False
        assert not auth.has_challenge


class TestStaleFlag:
    """`stale` separates "the nonce expired" from "wrong password".

    Regression test for a real bug: the client assumed a 401 on the second
    attempt meant bad credentials. It does not. Under 1s polling the device
    rotates the nonce often and answers 401 with stale="true", which means
    "renew and retry". Treating that as a credential failure made Home
    Assistant open the reauth flow and prompt the user for their password for
    no reason at all.
    """

    def test_real_challenge_is_not_stale(self):
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        assert auth.stale is False

    def test_detects_stale_challenge(self):
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE.replace('stale="false"', 'stale="true"'))
        assert auth.stale is True

    def test_stale_is_case_insensitive(self):
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE.replace('stale="false"', 'stale="TRUE"'))
        assert auth.stale is True

    def test_absent_stale_is_not_stale(self):
        """Without the flag we treat it as non-stale: failing beats retrying blindly."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge('Digest qop="auth", realm="R", nonce="abc"')
        assert auth.stale is False

    def test_renewed_challenge_restarts_nonce_count(self):
        """A new nonce requires nc restarting at 1, or the device rejects again."""
        auth = DigestAuth("admin", "secret")
        auth.set_challenge(REAL_CHALLENGE)
        auth.build_header("GET", "/x")
        auth.build_header("GET", "/x")
        auth.set_challenge(REAL_CHALLENGE.replace("MGJm", "OUTR"))
        assert parse_header(auth.build_header("GET", "/x"))["nc"] == "00000001"
