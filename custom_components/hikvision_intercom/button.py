"""Command buttons for the intercom."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HikvisionIntercomCoordinator
from .entity import build_device_info
from .isapi.client import IsapiClient


@dataclass(frozen=True, kw_only=True)
class IntercomButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[IsapiClient], Coroutine[Any, Any, None]]


BUTTONS: tuple[IntercomButtonDescription, ...] = (
    IntercomButtonDescription(
        key="answer", translation_key="answer", press_fn=lambda c: c.answer_call()
    ),
    IntercomButtonDescription(
        key="reject", translation_key="reject", press_fn=lambda c: c.reject_call()
    ),
    IntercomButtonDescription(
        key="hangup", translation_key="hangup", press_fn=lambda c: c.hangup_call()
    ),
    IntercomButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda c: c.reboot(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(IntercomButton(coordinator, desc) for desc in BUTTONS)


class IntercomButton(ButtonEntity):
    """A command button.

    Deliberately does NOT inherit from CoordinatorEntity: when the coordinator
    updates, Home Assistant logs a spurious "Pressed" entry in the logbook for
    button entities.
    """

    _attr_has_entity_name = True
    entity_description: IntercomButtonDescription

    def __init__(
        self,
        coordinator: HikvisionIntercomCoordinator,
        description: IntercomButtonDescription,
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_info.unique_id}_{description.key}"
        self._attr_device_info = build_device_info(coordinator)

    async def async_press(self) -> None:
        await self.coordinator.async_execute(self.entity_description.press_fn)
