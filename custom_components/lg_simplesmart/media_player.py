"""Media player for LG Simple Smart TVs."""

from __future__ import annotations

import mimetypes
import re
from datetime import timedelta
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature as F,
    MediaPlayerState,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LGConfigEntry
from .api import APPS, LGError, LGSimpleSmart
from .const import DOMAIN

SCAN_INTERVAL = timedelta(seconds=10)
YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/)([\w-]{11})")
TRANSPORT = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED_PLAYBACK": MediaPlayerState.PAUSED,
    "TRANSITIONING": MediaPlayerState.BUFFERING,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: LGConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        [LGTV(entry.runtime_data, entry.unique_id or entry.entry_id)], update_before_add=True
    )


class LGTV(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_source_list = list(APPS)
    _attr_volume_step = 0.02

    def __init__(self, client: LGSimpleSmart, unique_id: str) -> None:
        self.client = client
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            connections={(CONNECTION_NETWORK_MAC, client.mac)} if client.mac else set(),
            manufacturer="LG Electronics",
            model="Simple Smart TV (NetCast 4.0)",
            name="LG TV",
        )
        self._attr_supported_features = (
            F.VOLUME_SET | F.VOLUME_MUTE | F.VOLUME_STEP | F.SELECT_SOURCE
            | F.PLAY_MEDIA | F.BROWSE_MEDIA | F.PLAY | F.PAUSE | F.STOP
        )
        if client.mac:
            self._attr_supported_features |= F.TURN_ON
        # ponytail: no TURN_OFF, the firmware has no network power off (CEC/IR only)

    async def async_update(self) -> None:
        if not await self.client.is_on():
            self._attr_state = MediaPlayerState.OFF
            self._attr_source = None
            return
        self._attr_source = await self.client.running_app()
        try:
            self._attr_volume_level = await self.client.get_volume() / 100
            self._attr_is_volume_muted = await self.client.get_mute()
            transport = await self.client.transport_state()
        except LGError:
            transport = ""  # DMR still booting; DIAL already says it's on
        self._attr_state = TRANSPORT.get(transport, MediaPlayerState.ON)

    async def _call(self, coro) -> None:
        try:
            await coro
        except LGError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_on(self) -> None:
        await self.hass.async_add_executor_job(self.client.wake)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._call(self.client.set_volume(round(volume * 100)))
        self._attr_volume_level = volume

    async def async_mute_volume(self, mute: bool) -> None:
        await self._call(self.client.set_mute(mute))
        self._attr_is_volume_muted = mute

    async def async_select_source(self, source: str) -> None:
        await self._call(self.client.launch(source))
        self._attr_source = source

    async def async_media_play(self) -> None:
        await self._call(self.client.play())

    async def async_media_pause(self) -> None:
        await self._call(self.client.pause())

    async def async_media_stop(self) -> None:
        if self._attr_source:  # a DIAL app is in front; DMR stop would do nothing
            await self._call(self.client.stop(self._attr_source))
        else:
            await self._call(self.client.stop_media())

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_type, media_id = item.mime_type, async_process_play_media_url(
                self.hass, item.url
            )
        if (match := YOUTUBE_ID.search(media_id)) and "youtu" in media_id:
            await self._call(self.client.launch("YouTube", f"v={match[1]}"))
            return
        if media_type == "youtube":
            await self._call(self.client.launch("YouTube", f"v={media_id}"))
            return
        mime = media_type if "/" in media_type else (
            mimetypes.guess_type(media_id.split("?")[0])[0] or "video/mp4"
        )
        await self._call(self.client.play_url(media_id, mime))

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await media_source.async_browse_media(self.hass, media_content_id)
