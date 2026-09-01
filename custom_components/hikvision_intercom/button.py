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
    # `answer` and `hangup` are disabled by default until two-way audio is
    # wired up. Answering from Home Assistant today would pick the call up on
    # the door station -- the visitor sees someone answered -- while no audio
    # path exists on this side. They talk and nobody hears them, which is worse
    # than not answering at all. `hangup` only exists to undo that, so it is
    # equally premature.
    #
    # The device accepts all three cmdTypes with 200 OK even while idle, so a
    # successful response is not evidence that the command does anything.
    #
    # Both remain available for anyone who wants to enable them explicitly.
    IntercomButtonDescription(
        key="answer",
        translation_key="answer",
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.answer_call(),
    ),
    IntercomButtonDescription(
        key="reject", translation_key="reject", press_fn=lambda c: c.reject_call()
    ),
    IntercomButtonDescription(
        key="hangup",
        translation_key="hangup",
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.hangup_call(),
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
