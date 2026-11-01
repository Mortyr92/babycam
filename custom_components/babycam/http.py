"""HTTP view that streams the camera audio as MP3 (transcoded locally by ffmpeg)."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MODE_LOW

_LOGGER = logging.getLogger(__name__)

CHUNK = 4096


class BabyCamAudioView(HomeAssistantView):
    """Serve /api/babycam/<entry_id>/audio.mp3?token=... for browsers and media players."""

    url = "/api/babycam/{entry_id}/audio.mp3"
    name = "api:babycam:audio"
    requires_auth = False  # media players cannot send auth headers; a per-entry token is used instead

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        coordinator = self._hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise web.HTTPNotFound
        if request.query.get("token") != coordinator.audio_token:
            raise web.HTTPUnauthorized

        source = await coordinator.async_stream_source(MODE_LOW)
        if source is None:
            raise web.HTTPServiceUnavailable(text="Camera has no audio stream")

        cmd = [
            get_ffmpeg_manager(self._hass).binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            source,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-b:a",
            "96k",
            "-f",
            "mp3",
            "pipe:1",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        assert proc.stdout is not None

        response = web.StreamResponse(
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache, no-store",
                "Connection": "close",
            }
        )
        await response.prepare(request)
        try:
            while True:
                data = await proc.stdout.read(CHUNK)
                if not data:
                    break
                await response.write(data)
        except (asyncio.CancelledError, ConnectionResetError, web.HTTPException):
            pass
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return response
