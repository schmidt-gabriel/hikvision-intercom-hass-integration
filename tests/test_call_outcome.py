"""Answered vs missed classification, from the `ring` duration.

The device reports nowhere whether anyone answered. The inference comes from
the duration of the `ring` state, and these tests pin the thresholds against
the four real measurements taken on 2026-09-01 on a DS-KB8113-IME1(B) running
firmware V2.2.60.
"""

from __future__ import annotations

import pytest
from const import ANSWERED_RING_THRESHOLD, RING_TIMEOUT_SECONDS


def classify(ring_duration: float) -> str:
    """Same rule as the coordinator, isolated for testing."""
    return "answered" if ring_duration < ANSWERED_RING_THRESHOLD else "missed"


# (ring duration, expected outcome, how it was obtained)
MEASUREMENTS = [
    (31.0, "missed", "first capture, nobody answered"),
    (31.2, "missed", "second capture, nobody answered"),
    (13.0, "answered", "answered on the indoor station"),
    (4.4, "answered", "answered in the phone app"),
]


class TestAgainstRealMeasurements:
    @pytest.mark.parametrize(("duration", "expected", "label"), MEASUREMENTS)
    def test_classifies_real_calls(self, duration, expected, label):
        assert classify(duration) == expected, label

    def test_answered_and_missed_are_well_separated(self):
        """The gap between the two groups must stay comfortable.

        If they ever converge, duration-based classification stops being
        reliable and the method needs rethinking, not the threshold nudging.
        """
        answered = [d for d, e, _ in MEASUREMENTS if e == "answered"]
        missed = [d for d, e, _ in MEASUREMENTS if e == "missed"]
        assert min(missed) - max(answered) > 15.0

    def test_app_and_screen_are_indistinguishable(self):
        """App and indoor station behave the same: both just end the ring early.

        Documents an answer that cost a measurement: you cannot tell WHERE the
        call was answered by looking at callStatus.
        """
        by_label = {label: duration for duration, _, label in MEASUREMENTS}
        screen = by_label["answered on the indoor station"]
        app = by_label["answered in the phone app"]
        assert classify(screen) == classify(app) == "answered"


class TestThresholds:
    def test_threshold_sits_below_the_timeout(self):
        assert ANSWERED_RING_THRESHOLD < RING_TIMEOUT_SECONDS

    def test_threshold_leaves_room_for_poll_granularity(self):
        """With 1s polling the measured duration is ~1s off; the margin covers it."""
        assert RING_TIMEOUT_SECONDS - ANSWERED_RING_THRESHOLD >= 2.0

    def test_timeout_itself_classifies_as_missed(self):
        assert classify(RING_TIMEOUT_SECONDS) == "missed"

    def test_answering_at_the_last_moment_is_ambiguous(self):
        """A known limitation, recorded on purpose.

        Answering at the very last second is indistinguishable from not
        answering by this method. That is inherent to the approach, not a
        badly chosen threshold.
        """
        assert classify(RING_TIMEOUT_SECONDS - 0.5) == "missed"
