"""Failure tolerance and backoff for the callStatus poll."""

from __future__ import annotations

from backoff import FailureBackoff
from const import (
    DEFAULT_POLL_INTERVAL,
    FAILURE_TOLERANCE,
    HEALTHCHECK_INTERVAL,
    MAX_BACKOFF_INTERVAL,
)


def make() -> FailureBackoff:
    return FailureBackoff(tolerance=FAILURE_TOLERANCE, max_interval=MAX_BACKOFF_INTERVAL)


class TestTolerance:
    def test_single_blip_is_absorbed(self):
        """The 2026-10-12 02:00/16:00 case: one rejected read, then fine."""
        backoff = make()
        assert backoff.record_failure() is True
        assert backoff.record_success() is True
        assert backoff.failures == 0

    def test_failures_up_to_tolerance_are_absorbed(self):
        backoff = make()
        assert all(backoff.record_failure() for _ in range(FAILURE_TOLERANCE))

    def test_failure_past_tolerance_is_reported(self):
        backoff = make()
        for _ in range(FAILURE_TOLERANCE):
            backoff.record_failure()
        assert backoff.record_failure() is False

    def test_success_without_failures_reports_nothing_to_reset(self):
        assert make().record_success() is False


class TestInterval:
    def test_base_interval_while_healthy(self):
        assert make().interval(DEFAULT_POLL_INTERVAL) == DEFAULT_POLL_INTERVAL

    def test_tolerated_failures_retry_at_base_interval(self):
        backoff = make()
        for _ in range(FAILURE_TOLERANCE):
            backoff.record_failure()
        assert backoff.interval(DEFAULT_POLL_INTERVAL) == DEFAULT_POLL_INTERVAL

    def test_doubles_per_failure_past_tolerance(self):
        backoff = make()
        for _ in range(FAILURE_TOLERANCE):
            backoff.record_failure()
        seen = []
        for _ in range(4):
            backoff.record_failure()
            seen.append(backoff.interval(1.0))
        assert seen == [2.0, 4.0, 8.0, 16.0]

    def test_capped_on_a_long_outage(self):
        """The 2026-10-13 case: the device stays unreachable for hours."""
        backoff = make()
        for _ in range(1000):
            backoff.record_failure()
        assert backoff.interval(DEFAULT_POLL_INTERVAL) == MAX_BACKOFF_INTERVAL

    def test_push_health_check_is_never_shortened(self):
        backoff = FailureBackoff(tolerance=0, max_interval=HEALTHCHECK_INTERVAL / 2)
        backoff.record_failure()
        assert backoff.interval(HEALTHCHECK_INTERVAL) == HEALTHCHECK_INTERVAL

    def test_recovery_restores_base_interval(self):
        backoff = make()
        for _ in range(10):
            backoff.record_failure()
        backoff.record_success()
        assert backoff.interval(DEFAULT_POLL_INTERVAL) == DEFAULT_POLL_INTERVAL
