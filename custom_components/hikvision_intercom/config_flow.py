"""Config flow for the Hikvision Video Intercom integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ANSWERED_RING_THRESHOLD,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_ANSWERED_RING_THRESHOLD,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)
from .isapi.client import AuthError, CannotConnect, IsapiClient, IsapiError

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_USERNAME, default="admin"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
        ),
    }
)


class HikvisionIntercomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Drives adding an intercom through the UI."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def _validate(self, data: dict[str, Any]) -> tuple[str, str]:
        """Connect and return (unique_id, title). Raises on failure."""
        client = IsapiClient(
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            port=int(data.get(CONF_PORT, DEFAULT_PORT)),
            session=async_get_clientsession(self.hass),
        )
        info = await client.get_device_info()
        if not info.unique_id:
            raise CannotConnect("The device reported neither a serial number nor a MAC")
        return info.unique_id, info.name or info.model

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_PORT] = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            try:
                unique_id, title = await self._validate(user_input)
            except AuthError:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except IsapiError:
                _LOGGER.exception("Unexpected failure talking to the intercom")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again without redoing the whole setup."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None

        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                await self._validate(data)
            except AuthError:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except IsapiError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HikvisionIntercomOptionsFlow()


class HikvisionIntercomOptionsFlow(OptionsFlow):
    """Post-setup options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        poll = options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        threshold = options.get(CONF_ANSWERED_RING_THRESHOLD, DEFAULT_ANSWERED_RING_THRESHOLD)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POLL_INTERVAL, default=poll): NumberSelector(
                        NumberSelectorConfig(
                            min=0.5, max=10, step=0.5, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Optional(CONF_ANSWERED_RING_THRESHOLD, default=threshold): NumberSelector(
                        NumberSelectorConfig(min=5, max=120, step=0.5, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
        )
