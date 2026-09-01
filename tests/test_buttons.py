"""Which command buttons ship enabled.

Pins a deliberate product decision rather than an implementation detail, so it
does not get reverted by accident.
"""

from __future__ import annotations

import pathlib
import re

BUTTON_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hikvision_intercom"
    / "button.py"
).read_text()


def _block(key: str) -> str:
    """Return the IntercomButtonDescription block for a button key."""
    match = re.search(
        rf'IntercomButtonDescription\((?:(?!IntercomButtonDescription).)*?key="{key}".*?\),\n',
        BUTTON_SOURCE,
        re.DOTALL,
    )
    assert match, f"no description found for {key}"
    return match.group(0)


class TestDefaultEnabledButtons:
    def test_reject_is_enabled(self):
        """Dismissing a ring is useful on its own and needs no audio."""
        assert "entity_registry_enabled_default=False" not in _block("reject")

    def test_reboot_is_enabled(self):
        assert "entity_registry_enabled_default=False" not in _block("reboot")

    def test_answer_is_disabled_until_two_way_audio_exists(self):
        """Answering with no audio path is worse than not answering.

        The door station picks up, the visitor sees the call was answered, and
        nobody can hear them. Re-enable this only once two-way audio works.
        """
        assert "entity_registry_enabled_default=False" in _block("answer")

    def test_hangup_is_disabled_until_two_way_audio_exists(self):
        """`hangup` only undoes `answer`, so it is premature for the same reason."""
        assert "entity_registry_enabled_default=False" in _block("hangup")
