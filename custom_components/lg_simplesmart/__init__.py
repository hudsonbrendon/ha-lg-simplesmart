"""LG Simple Smart TV (2016 non-webOS LG, e.g. 32LH570B)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LGSimpleSmart
from .const import CONF_MAC

PLATFORMS = [Platform.MEDIA_PLAYER]

type LGConfigEntry = ConfigEntry[LGSimpleSmart]


async def async_setup_entry(hass: HomeAssistant, entry: LGConfigEntry) -> bool:
    # No ConfigEntryNotReady: the TV being in standby is the normal "off" state.
    entry.runtime_data = LGSimpleSmart(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data.get(CONF_MAC)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LGConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
