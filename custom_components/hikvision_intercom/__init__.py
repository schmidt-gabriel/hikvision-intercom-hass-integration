"""Hikvision Video Intercom integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, DEFAULT_PORT
from .coordinator import HikvisionIntercomCoordinator
from .isapi.client import AuthError, CannotConnect, IsapiClient, IsapiError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.EVENT,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.LOCK,
    Platform.SWITCH,
]

type HikvisionIntercomEntry = ConfigEntry[HikvisionIntercomCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionIntercomEntry) -> bool:
    """Set up an intercom from a config entry."""
    client = IsapiClient(
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        port=int(entry.data.get(CONF_PORT, DEFAULT_PORT)),
        session=async_get_clientsession(hass),
    )

    try:
        device_info = await client.get_device_info()
        capabilities = await client.probe_capabilities()
    except AuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (CannotConnect, IsapiError) as err:
        raise ConfigEntryNotReady(f"Could not reach the intercom: {err}") from err

    _LOGGER.info(
        "%s (fw %s): starting on callStatus polling; push candidate channel = %s",
        device_info.model,
        device_info.firmware_version,
        capabilities.push_candidate or "none",
    )
    for note in capabilities.notes:
        _LOGGER.debug("Probe: %s", note)

    coordinator = HikvisionIntercomCoordinator(hass, entry, client, device_info, capabilities)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionIntercomEntry) -> bool:
    """Unload the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: HikvisionIntercomEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
