"""Base class for the integration's entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import HikvisionIntercomCoordinator


def build_device_info(coordinator: HikvisionIntercomCoordinator) -> DeviceInfo:
    """Build DeviceInfo from what the device reported about itself."""
    info = coordinator.device_info
    connections = set()
    if info.mac_address:
        connections.add((CONNECTION_NETWORK_MAC, info.mac_address))

    return DeviceInfo(
        identifiers={(DOMAIN, info.unique_id)},
        connections=connections,
        manufacturer=MANUFACTURER,
        model=info.model,
        name=info.name or info.model,
        sw_version=f"{info.firmware_version} ({info.firmware_date})".strip(),
        serial_number=info.serial_number or None,
        configuration_url=f"http://{coordinator.client.host}",
    )


class HikvisionIntercomEntity(CoordinatorEntity[HikvisionIntercomCoordinator]):
    """Base for coordinator-backed entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HikvisionIntercomCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.device_info.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = build_device_info(coordinator)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
