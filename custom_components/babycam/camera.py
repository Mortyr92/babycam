"""Camera entity: last snapshot, plus a live MJPEG/HLS view in the selected quality."""

from __future__ import annotations

import logging

from aiohttp import web
from haffmpeg.camera import CameraMjpeg
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .camera_client import CameraError
from .const import MODE_SNAPSHOTS
from .entity import BabyCamEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([BabyCamCamera(entry.runtime_data)])


class BabyCamCamera(BabyCamEntity, Camera):
    _attr_translation_key = "live"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator) -> None:
        BabyCamEntity.__init__(self, coordinator, "live")
        Camera.__init__(self)

    @property
    def is_on(self) -> bool:
        return True

    @property
    def is_streaming(self) -> bool:
        return self.coordinator.state.stream_mode != MODE_SNAPSHOTS

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            image = await self.coordinator.source.async_get_snapshot()
        except CameraError as err:
            _LOGGER.debug("Live snapshot failed, serving cached frame: %s", err)
            return self.coordinator.last_image
        self.coordinator.last_image = image
        return image

    async def stream_source(self) -> str | None:
        return await self.coordinator.async_stream_source()

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse | None:
        source = await self.stream_source()
        if source is None:
            return await super().handle_async_mjpeg_stream(request)

        manager = get_ffmpeg_manager(self.hass)
        stream = CameraMjpeg(manager.binary)
        await stream.open_camera(source, extra_cmd="-rtsp_transport tcp -q:v 5")
        try:
            stream_reader = await stream.get_reader()
            return await async_aiohttp_proxy_stream(
                self.hass, request, stream_reader, manager.ffmpeg_stream_content_type
            )
        finally:
            await stream.close()
