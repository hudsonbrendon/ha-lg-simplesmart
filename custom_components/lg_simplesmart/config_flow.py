"""Config flow: host -> probe DIAL, read wake MAC from the DIAL SSDP reply."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .api import ST_DIAL, LGSimpleSmart, ssdp_search, wake_mac
from .const import CONF_MAC, DOMAIN


class LGSimpleSmartConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            client = LGSimpleSmart(async_get_clientsession(self.hass), host, None)
            if not await client.is_on():
                errors["base"] = "cannot_connect"
            else:
                mac = user_input.get(CONF_MAC) or wake_mac(
                    await self.hass.async_add_executor_job(ssdp_search, host, ST_DIAL)
                    or {}
                )
                mac = format_mac(mac) if mac else None
                await self.async_set_unique_id(mac or host)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=f"LG TV ({host})", data={CONF_HOST: host, CONF_MAC: mac}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST): str, vol.Optional(CONF_MAC): str}
            ),
            errors=errors,
        )
