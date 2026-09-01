"""Door lock for the intercom."""

from __future__ import annotations

import asyncio

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HikvisionIntercomCoordinator
from .entity import HikvisionIntercomEntity
from .isapi.const import DOOR_OPEN

# How long we show the lock as unlocked after triggering it. The device does
# not report latch state, so this is purely visual feedback.
RELOCK_AFTER = 5


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HikvisionIntercomCoordinator = entry.runtime_data
    if not coordinator.capabilities.supports_remote_open_door:
        return
    async_add_entities(
        IntercomLock(coordinator, door_no) for door_no in coordinator.capabilities.door_numbers
    )


class IntercomLock(HikvisionIntercomEntity, LockEntity):
    """Triggers the door lock.

    The DS-KB8113-IME1(B) exposes no latch state, only an open command. So the
    entity is modelled as a pulse: it unlocks, then relocks itself after a few
    seconds. `assumed_state` makes that explicit in the UI instead of
    pretending we know the real state.
    """

    _attr_assumed_state = True
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: HikvisionIntercomCoordinator, door_no: int) -> None:
        super().__init__(coordinator, "door" if door_no == 1 else f"door_{door_no}")
        self._door_no = door_no
        self._unlocked = False

    @property
    def is_locked(self) -> bool:
        return not self._unlocked

    async def async_unlock(self, **kwargs) -> None:
        await self._pulse()

    async def async_open(self, **kwargs) -> None:
        await self._pulse()

    async def async_lock(self, **kwargs) -> None:
        """The device has no lock command; the door relocks itself."""
        self._unlocked = False
        self.async_write_ha_state()

    async def _pulse(self) -> None:
        await self.coordinator.async_execute(
            lambda client: client.open_door(self._door_no, DOOR_OPEN), refresh=False
        )
        self._unlocked = True
        self.async_write_ha_state()

        async def relock() -> None:
            await asyncio.sleep(RELOCK_AFTER)
            self._unlocked = False
            self.async_write_ha_state()

        self.hass.async_create_task(relock())
