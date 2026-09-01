# Test fixtures

Real ISAPI responses captured from a DS-KB8113-IME1(B) running firmware
V2.2.60 (build 231204) on 2026-09-01.

These pin the parsers against what the hardware actually returns rather than
what Hikvision's documentation claims — several endpoints on this firmware
disagree with the docs (see `docs/protocol.md`).

Device identifiers (serial number, MAC address, device UUID) have been replaced
with example values of the same shape. Everything else is verbatim.
