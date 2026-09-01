"""Pytest configuration.

The `isapi` subpackage is deliberately free of Home Assistant, so the tests
import it directly rather than through the component's __init__.py, which pulls
HA in. That keeps the parser suite fast and dependency-free, and doubles as a
guard: if anyone introduces a homeassistant import inside isapi/, these tests
break immediately.
"""

from __future__ import annotations

import pathlib
import sys

COMPONENT_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "hikvision_intercom"
)
sys.path.insert(0, str(COMPONENT_DIR))
