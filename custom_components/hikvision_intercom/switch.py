"""Intercom switches: output relay and permanent-unlock mode."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HikvisionIntercomCoordinator
from .entity import HikvisionIntercomEntity
from .isapi.const import DOOR_ALWAYS_OPEN, DOOR_RESUME


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HikvisionIntercomCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = []

    # The relay count comes from the device (IOOutputPortNums), not from a
    # guessed default -- this model has 1, and the SDK-based add-on assumes 2.
    for output in range(1, coordinator.capabilities.output_ports + 1):
        entities.append(OutputRelaySwitch(coordinator, output))

    if DOOR_ALWAYS_OPEN in coordinator.capabilities.door_commands:
        entities.append(AlwaysOpenSwitch(coordinator))

    async_add_entities(entities)


class OutputRelaySwitch(HikvisionIntercomEntity, SwitchEntity):
    """Triggers an output relay.

    The device does not report relay state, so this is assumed_state.
    """

    _attr_assumed_state = True

    def __init__(self, coordinator: HikvisionIntercomCoordinator, output_no: int) -> None:
        super().__init__(coordinator, f"relay_{output_no}")
        self._output_no = output_no
        self._state = False

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_execute(
            lambda c: c.trigger_output(self._output_no, "high"), refresh=False
        )
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_execute(
            lambda c: c.trigger_output(self._output_no, "low"), refresh=False
        )
        self._state = False
        self.async_write_ha_state()


class AlwaysOpenSwitch(HikvisionIntercomEntity, SwitchEntity):
    """Holds the door permanently unlocked (`alwaysOpen` / `resume`).

    Deliberately separate from the `lock` entity: a momentary unlock and a
    permanent unlock are different intents, and merging them into one control
    is how a door gets left open by accident.
    """

    _attr_assumed_state = True

    def __init__(self, coordinator: HikvisionIntercomCoordinator) -> None:
        super().__init__(coordinator, "always_open")
        self._state = False

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_execute(
            lambda c: c.open_door(1, DOOR_ALWAYS_OPEN), refresh=False
        )
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_execute(lambda c: c.open_door(1, DOOR_RESUME), refresh=False)
        self._state = False
        self.async_write_ha_state()
