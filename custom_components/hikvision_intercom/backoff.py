"""Failure tolerance and backoff for the callStatus poll.

Kept free of Home Assistant so the tests can exercise the real policy.

Two problems, one counter:

    1. A single failed read used to mark every entity unavailable. On
       2026-10-12 the device rejected one digest handshake at 02:00 and again
       at 16:00, recovering on the very next poll -- a one-second blip that
       still showed up as an outage on every entity.

    2. When the device stops answering for real (2026-10-13 its HTTP server
       hung on port 80 while 443/554/8000 stayed up), we kept hitting it every
       second, two requests per read, indefinitely. That buys nothing and may
       be part of what wedges this firmware.

So the first few failures are absorbed (entities keep the last known state),
and past that the interval doubles per failure up to a cap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FailureBackoff:
    """Counts consecutive read failures and derives the next poll interval."""

    tolerance: int
    max_interval: float
    failures: int = 0

    def record_failure(self) -> bool:
        """Count a failure. Returns True while it is still within tolerance."""
        self.failures += 1
        return self.failures <= self.tolerance

    def record_success(self) -> bool:
        """Reset the counter. Returns True if there was anything to reset."""
        was_failing = self.failures > 0
        self.failures = 0
        return was_failing

    def interval(self, base: float) -> float:
        """Poll interval in seconds given the mode's normal interval.

        Tolerated failures keep the base interval -- the point is to retry
        quickly and hide the blip. Each failure beyond that doubles it. The cap
        never drops below the base, so push mode's 30s health check is never
        shortened.
        """
        excess = self.failures - self.tolerance
        if excess <= 0:
            return base
        return min(base * 2**excess, max(self.max_interval, base))
