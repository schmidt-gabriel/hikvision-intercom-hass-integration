"""Intercom camera: ISAPI snapshot and RTSP stream."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HikvisionIntercomCoordinator
from .entity import build_device_info
from .isapi.client import IsapiError
from .isapi.const import MAIN_STREAM_CHANNEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HikvisionIntercomCoordinator = entry.runtime_data
    if not coordinator.capabilities.stream_channels:
        return
    async_add_entities([IntercomCamera(coordinator)])


class IntercomCamera(Camera):
    """Video from the door station.

    Snapshots come from ISAPI and the stream from RTSP. Two-way audio is
    delivered by the go2rtc bundled with Home Assistant through the `isapi://`
    backchannel -- no companion server needed.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "camera"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: HikvisionIntercomCoordinator) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.device_info.unique_id}_camera"
        self._attr_device_info = build_device_info(coordinator)
        channels = coordinator.capabilities.stream_channels
        self._channel = MAIN_STREAM_CHANNEL if MAIN_STREAM_CHANNEL in channels else channels[0]

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        try:
            return await self.coordinator.client.get_snapshot(self._channel)
        except IsapiError as err:
            _LOGGER.debug("Failed to fetch snapshot: %s", err)
            return None

    async def stream_source(self) -> str | None:
        return self.coordinator.client.rtsp_url(self._channel)
