"""HTTP Digest authentication (RFC 2617) for ISAPI.

Why hand-rolled instead of httpx.DigestAuth: there is a known httpx bug with
Hikvision intercoms (encode/httpx#2549), and Home Assistant standardises on
aiohttp, which ships no digest support. This is ~60 lines under our control and
zero new dependencies in the manifest.

Hikvision devices challenge with MD5 + qop="auth", for example:

    Digest qop="auth", realm="DS-0CED27ED", nonce="...", stale="false",
           opaque="", domain="::"
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^\s,]+))')


def _md5(data: str) -> str:
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def parse_challenge(header: str) -> dict[str, str]:
    """Extract the parameters of a WWW-Authenticate header."""
    if not header:
        return {}
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "digest":
        return {}
    return {
        m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
        for m in _TOKEN_RE.finditer(rest)
    }


@dataclass
class DigestAuth:
    """Holds the server challenge and builds the Authorization header.

    Note that the client deliberately does NOT reuse a challenge across
    requests on this hardware; see the comment in IsapiClient.request.
    """

    username: str
    password: str
    _challenge: dict[str, str] = field(default_factory=dict, repr=False)
    _nc: int = 0

    @property
    def has_challenge(self) -> bool:
        return bool(self._challenge.get("nonce"))

    @property
    def stale(self) -> bool:
        """The server says the nonce expired but the credentials are fine.

        This is what separates "renew and retry" from "your password is wrong".
        Counting attempts does not separate them: under fast polling the device
        rotates the nonce often and two consecutive 401s are normal.
        """
        return self._challenge.get("stale", "").lower() == "true"

    def set_challenge(self, header: str) -> bool:
        """Record a new challenge. Returns True if it could be parsed."""
        challenge = parse_challenge(header)
        if not challenge.get("nonce"):
            return False
        self._challenge = challenge
        self._nc = 0
        return True

    def reset(self) -> None:
        self._challenge = {}
        self._nc = 0

    def build_header(self, method: str, uri: str) -> str:
        """Build the Authorization header for this method/URI.

        `uri` must be exactly the path sent on the request line, query string
        included -- Hikvision devices reject a mismatch.
        """
        if not self.has_challenge:
            raise ValueError("No digest challenge yet; make an initial request first")

        realm = self._challenge.get("realm", "")
        nonce = self._challenge["nonce"]
        qop = self._challenge.get("qop")
        opaque = self._challenge.get("opaque")
        algorithm = self._challenge.get("algorithm", "MD5")

        ha1 = _md5(f"{self.username}:{realm}:{self.password}")
        ha2 = _md5(f"{method.upper()}:{uri}")

        parts = {
            "username": self.username,
            "realm": realm,
            "nonce": nonce,
            "uri": uri,
            "algorithm": algorithm,
        }

        if qop:
            # The header may list several options ("auth,auth-int"); we only do auth.
            chosen = "auth" if "auth" in [q.strip() for q in qop.split(",")] else qop
            self._nc += 1
            nc = f"{self._nc:08x}"
            cnonce = _md5(f"{time.time()}:{os.urandom(8).hex()}")[:16]
            response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{chosen}:{ha2}")
            parts.update({"qop": chosen, "nc": nc, "cnonce": cnonce, "response": response})
        else:
            parts["response"] = _md5(f"{ha1}:{nonce}:{ha2}")

        if opaque:
            parts["opaque"] = opaque

        # nc and qop go unquoted; everything else quoted, per the RFC.
        unquoted = {"qop", "nc", "algorithm"}
        rendered = ", ".join(
            f"{k}={v}" if k in unquoted else f'{k}="{v}"' for k, v in parts.items()
        )
        return f"Digest {rendered}"
