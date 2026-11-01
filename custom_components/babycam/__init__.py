"""BabyCam - a fully local baby monitor for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .camera_client import (
    CameraError,
    CameraSource,
    EntityCameraSource,
    ReolinkCameraSource,
)
from .const import (
    CONF_CAMERA_ENTITY,
    CONF_CHANNEL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RTSP_PORT,
    CONF_SOURCE_TYPE,
    CONF_STREAM_MAIN,
    CONF_STREAM_SUB,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    DEFAULT_CHANNEL,
    DEFAULT_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_STREAM_MAIN,
    DEFAULT_STREAM_SUB,
    DOMAIN,
    MODELS_SUBDIR,
    MONITOR_MANUAL,
    PANEL_ICON,
    PANEL_MODULE,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PLATFORMS,
    SERVICE_CHECK_NOW,
    SERVICE_DOWNLOAD_MODELS,
    SERVICE_START_MONITORING,
    SERVICE_STOP_MONITORING,
    SOURCE_ENTITY,
    STATIC_URL_BASE,
)
from .coordinator import BabyCamCoordinator
from .http import BabyCamAudioView
from .model_downloader import ModelDownloadError
from .websocket_api import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type BabyCamConfigEntry = ConfigEntry[BabyCamCoordinator]

SERVICE_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})
DOWNLOAD_SCHEMA = SERVICE_SCHEMA.extend({vol.Optional("force", default=False): cv.boolean})


def build_source(hass: HomeAssistant, data: dict) -> CameraSource:
    """Create the camera back-end described by the config entry data."""
    if data.get(CONF_SOURCE_TYPE, SOURCE_ENTITY) == SOURCE_ENTITY:
        return EntityCameraSource(hass, data[CONF_CAMERA_ENTITY])
    return ReolinkCameraSource(
        hass,
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        rtsp_port=data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
        channel=data.get(CONF_CHANNEL, DEFAULT_CHANNEL),
        use_https=data.get(CONF_USE_HTTPS, False),
        stream_main=data.get(CONF_STREAM_MAIN, DEFAULT_STREAM_MAIN),
        stream_sub=data.get(CONF_STREAM_SUB, DEFAULT_STREAM_SUB),
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the panel, HTTP view, websocket API and services once."""
    hass.data.setdefault(DOMAIN, {})

    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL_BASE, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    hass.http.register_view(BabyCamAudioView(hass))
    async_register_websocket_api(hass)

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="babycam-panel",
        frontend_url_path=PANEL_URL_PATH,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=PANEL_MODULE,
        embed_iframe=False,
        require_admin=False,
    )

    def _coordinators(call: ServiceCall) -> list[BabyCamCoordinator]:
        entry_id = call.data.get("entry_id")
        entries = [
            e for e in hass.config_entries.async_entries(DOMAIN) if entry_id in (None, e.entry_id)
        ]
        return [e.runtime_data for e in entries if hasattr(e, "runtime_data") and e.runtime_data]

    async def _start(call: ServiceCall) -> None:
        for coordinator in _coordinators(call):
            await coordinator.async_start_monitoring(MONITOR_MANUAL)

    async def _stop(call: ServiceCall) -> None:
        for coordinator in _coordinators(call):
            await coordinator.async_stop_monitoring()

    async def _check(call: ServiceCall) -> None:
        for coordinator in _coordinators(call):
            await coordinator.async_check_now()

    hass.services.async_register(DOMAIN, SERVICE_START_MONITORING, _start, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP_MONITORING, _stop, schema=SERVICE_SCHEMA)
    async def _download(call: ServiceCall) -> None:
        errors = []
        for coordinator in _coordinators(call):
            try:
                await coordinator.async_download_models(force=call.data["force"])
            except ModelDownloadError as err:
                errors.append(str(err))
        if errors:
            raise HomeAssistantError("; ".join(errors))

    hass.services.async_register(DOMAIN, SERVICE_CHECK_NOW, _check, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DOWNLOAD_MODELS, _download, schema=DOWNLOAD_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BabyCamConfigEntry) -> bool:
    """Set up a BabyCam config entry."""
    models_dir = Path(hass.config.path(MODELS_SUBDIR))
    await hass.async_add_executor_job(lambda: models_dir.mkdir(parents=True, exist_ok=True))

    source = build_source(hass, entry.data)
    coordinator = BabyCamCoordinator(hass, entry, source)

    try:
        await source.async_get_snapshot()
    except CameraError as err:
        # Keep the entry loaded so the connection-lost alarm and manual monitoring still work.
        _LOGGER.warning("Camera %s not reachable during setup: %s", source.name, err)

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BabyCamConfigEntry) -> None:
    entry.runtime_data.apply_options()


async def async_unload_entry(hass: HomeAssistant, entry: BabyCamConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the sidebar panel when the last entry is removed."""
    if not hass.config_entries.async_entries(DOMAIN):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
