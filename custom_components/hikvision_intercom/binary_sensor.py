"""Binary sensors for the intercom."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HikvisionIntercomCoordinator, IntercomState
from .entity import HikvisionIntercomEntity


@dataclass(frozen=True, kw_only=True)
class IntercomBinarySensorDescription(BinarySensorEntityDescription):
    """Description carrying the function that extracts the value from state."""

    value_fn: Callable[[IntercomState], bool]


SENSORS: tuple[IntercomBinarySensorDescription, ...] = (
    IntercomBinarySensorDescription(
        key="ringing",
        translation_key="ringing",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        value_fn=lambda state: state.is_ringing,
    ),
    # MIND THE NAME: this sensor reflects the device's `onCall` state, which
    # does NOT mean anyone answered. In a real capture with nobody answering
    # (neither on the indoor station nor in the app), the device went from
    # `ring` to `onCall` and back to `idle` on its own, 31s in each state -- a
    # firmware timeout. Hence "call session", and hence diagnostic: anyone
    # asking "did the doorbell ring" wants event.doorbell instead.
    IntercomBinarySensorDescription(
        key="call_session",
        translation_key="call_session",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.is_on_call,
    ),
    IntercomBinarySensorDescription(
        key="push_degraded",
        translation_key="push_degraded",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.push_degraded,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(IntercomBinarySensor(coordinator, desc) for desc in SENSORS)


class IntercomBinarySensor(HikvisionIntercomEntity, BinarySensorEntity):
    """Binary sensor derived from coordinator state."""

    entity_description: IntercomBinarySensorDescription

    def __init__(
        self,
        coordinator: HikvisionIntercomCoordinator,
        description: IntercomBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)
