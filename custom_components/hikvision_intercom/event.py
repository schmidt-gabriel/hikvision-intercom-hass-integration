"""Doorbell event entity.

The `event` platform is the modern way to model a doorbell in Home Assistant:
it fires a point-in-time event rather than holding a state with a duration,
which is what doorbell automations actually want.
"""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HikvisionIntercomCoordinator
from .entity import HikvisionIntercomEntity

EVENT_RING = "ring"
EVENT_ANSWERED = "answered"
EVENT_MISSED = "missed"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DoorbellEvent(entry.runtime_data)])


class DoorbellEvent(HikvisionIntercomEntity, EventEntity):
    """Fires whenever someone rings the doorbell."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING, EVENT_ANSWERED, EVENT_MISSED]

    def __init__(self, coordinator: HikvisionIntercomCoordinator) -> None:
        super().__init__(coordinator, "doorbell")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_ring_listener(self._handle_ring))
        self.async_on_remove(
            self.coordinator.async_add_call_result_listener(self._handle_call_result)
        )

    @callback
    def _handle_ring(self, source: str) -> None:
        """Record the ring, noting which channel delivered it.

        The `source` attribute is diagnostic: it makes visible whether push is
        working or the watchdog has already demoted us to polling.
        """
        self._trigger_event(EVENT_RING, {"source": source})
        self.async_write_ha_state()

    @callback
    def _handle_call_result(self, answered: bool, duration: float) -> None:
        """Record whether the call was answered or missed.

        Inferred from the `ring` duration: the device does not expose this
        directly. See the coordinator for the reasoning and the measurements.
        """
        self._trigger_event(
            EVENT_ANSWERED if answered else EVENT_MISSED,
            {"ring_duration": round(duration, 1)},
        )
        self.async_write_ha_state()
