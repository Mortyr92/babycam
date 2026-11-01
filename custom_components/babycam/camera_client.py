"""Camera sources: a Home Assistant camera entity or a Reolink camera reached directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import secrets
from urllib.parse import quote

import aiohttp
from homeassistant.components import camera as ha_camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import MODE_HIGH

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_TIMEOUT = aiohttp.ClientTimeout(total=15)


class CameraError(Exception):
    """Raised when the camera cannot be reached or returns garbage."""


class CameraSource(ABC):
    """Common interface over the camera back-ends."""

    @abstractmethod
    async def async_get_snapshot(self) -> bytes:
        """Return a JPEG snapshot."""

    @abstractmethod
    async def async_get_stream_source(self, mode: str) -> str | None:
        """Return an RTSP (or other ffmpeg-readable) URL for the given quality mode."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human readable name."""


class EntityCameraSource(CameraSource):
    """Use an existing Home Assistant camera entity (e.g. the official Reolink integration)."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self._hass = hass
        self._entity_id = entity_id

    @property
    def name(self) -> str:
        state = self._hass.states.get(self._entity_id)
        if state is not None:
            return state.name
        return self._entity_id

    async def async_get_snapshot(self) -> bytes:
        try:
            image = await ha_camera.async_get_image(self._hass, self._entity_id, timeout=15)
        except Exception as err:  # noqa: BLE001 - HA raises HomeAssistantError and friends
            raise CameraError(f"Snapshot from {self._entity_id} failed: {err}") from err
        if not image.content:
            raise CameraError(f"Empty snapshot from {self._entity_id}")
        return image.content

    async def async_get_stream_source(self, mode: str) -> str | None:
        try:
            return await ha_camera.async_get_stream_source(self._hass, self._entity_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No stream source for %s: %s", self._entity_id, err)
            return None


class ReolinkCameraSource(CameraSource):
    """Talk to a Reolink camera (tested profile: E1 Pro) over its HTTP CGI and RTSP."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 80,
        rtsp_port: int = 554,
        channel: int = 0,
        use_https: bool = False,
        stream_main: str = "h264Preview_01_main",
        stream_sub: str = "h264Preview_01_sub",
    ) -> None:
        self._hass = hass
        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._rtsp_port = rtsp_port
        self._channel = channel
        self._use_https = use_https
        self._stream_main = stream_main
        self._stream_sub = stream_sub

    @property
    def name(self) -> str:
        return f"Reolink {self._host}"

    @property
    def _base_url(self) -> str:
        scheme = "https" if self._use_https else "http"
        return f"{scheme}://{self._host}:{self._port}"

    def snapshot_url(self) -> str:
        rs = secrets.token_hex(8)
        return (
            f"{self._base_url}/cgi-bin/api.cgi?cmd=Snap&channel={self._channel}"
            f"&rs={rs}&user={quote(self._username, safe='')}&password={quote(self._password, safe='')}"
        )

    async def async_get_snapshot(self) -> bytes:
        session = async_get_clientsession(self._hass, verify_ssl=False)
        try:
            async with session.get(self.snapshot_url(), timeout=SNAPSHOT_TIMEOUT) as resp:
                if resp.status != 200:
                    raise CameraError(f"Reolink snapshot HTTP {resp.status}")
                data = await resp.read()
        except aiohttp.ClientError as err:
            raise CameraError(f"Reolink snapshot failed: {err}") from err
        except TimeoutError as err:
            raise CameraError("Reolink snapshot timed out") from err
        if not data.startswith(b"\xff\xd8"):
            # Reolink answers with JSON when the credentials are wrong.
            raise CameraError("Reolink returned a non-JPEG response (check credentials)")
        return data

    async def async_get_stream_source(self, mode: str) -> str | None:
        path = self._stream_main if mode == MODE_HIGH else self._stream_sub
        return (
            f"rtsp://{quote(self._username, safe='')}:{quote(self._password, safe='')}"
            f"@{self._host}:{self._rtsp_port}/{path}"
        )
