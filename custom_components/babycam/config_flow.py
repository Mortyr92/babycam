"""Config and options flow for BabyCam."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
import voluptuous as vol

from .camera_client import CameraError, EntityCameraSource, ReolinkCameraSource
from .const import (
    CONF_ABSENCE_GRACE,
    CONF_CAMERA_ENTITY,
    CONF_CHANNEL,
    CONF_CONNECTION_TIMEOUT,
    CONF_CRY_COOLDOWN,
    CONF_CRY_DETECTION,
    CONF_CRY_MODEL,
    CONF_CRY_THRESHOLD,
    CONF_DETECTION_THRESHOLD,
    CONF_HOST,
    CONF_MEDIA_PLAYERS,
    CONF_MODEL_DOWNLOAD,
    CONF_MODEL_MANIFEST_URL,
    CONF_NOTIFY_SERVICES,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PRESENCE_MODEL,
    CONF_ROI,
    CONF_RTSP_PORT,
    CONF_SNAPSHOT_INTERVAL,
    CONF_SOURCE_TYPE,
    CONF_STREAM_MAIN,
    CONF_STREAM_SUB,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    DEFAULT_ABSENCE_GRACE,
    DEFAULT_CHANNEL,
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_CRY_COOLDOWN,
    DEFAULT_CRY_MODEL,
    DEFAULT_CRY_THRESHOLD,
    DEFAULT_DETECTION_THRESHOLD,
    DEFAULT_MODEL_DOWNLOAD,
    DEFAULT_MODEL_MANIFEST_URL,
    DEFAULT_PORT,
    DEFAULT_PRESENCE_MODEL,
    DEFAULT_ROI,
    DEFAULT_RTSP_PORT,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_STREAM_MAIN,
    DEFAULT_STREAM_SUB,
    DOMAIN,
    MAX_SNAPSHOT_INTERVAL,
    MIN_SNAPSHOT_INTERVAL,
    MODEL_DOWNLOAD_MODES,
    SOURCE_ENTITY,
    SOURCE_REOLINK,
)
from .detector import parse_roi

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE_TYPE, default=SOURCE_ENTITY): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[SOURCE_ENTITY, SOURCE_REOLINK],
                translation_key="source_type",
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    }
)

STEP_ENTITY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CAMERA_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="camera")
        )
    }
)

STEP_REOLINK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USE_HTTPS, default=False): bool,
        vol.Optional(CONF_RTSP_PORT, default=DEFAULT_RTSP_PORT): int,
        vol.Optional(CONF_CHANNEL, default=DEFAULT_CHANNEL): int,
        vol.Optional(CONF_STREAM_MAIN, default=DEFAULT_STREAM_MAIN): str,
        vol.Optional(CONF_STREAM_SUB, default=DEFAULT_STREAM_SUB): str,
    }
)


async def _validate_entity(hass: HomeAssistant, entity_id: str) -> str:
    source = EntityCameraSource(hass, entity_id)
    await source.async_get_snapshot()
    return source.name


async def _validate_reolink(hass: HomeAssistant, data: dict[str, Any]) -> str:
    source = ReolinkCameraSource(
        hass,
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        port=data[CONF_PORT],
        rtsp_port=data[CONF_RTSP_PORT],
        channel=data[CONF_CHANNEL],
        use_https=data[CONF_USE_HTTPS],
        stream_main=data[CONF_STREAM_MAIN],
        stream_sub=data[CONF_STREAM_SUB],
    )
    await source.async_get_snapshot()
    return source.name


class BabyCamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._source_type = SOURCE_ENTITY

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._source_type = user_input[CONF_SOURCE_TYPE]
            if self._source_type == SOURCE_ENTITY:
                return await self.async_step_entity()
            return await self.async_step_reolink()
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    async def async_step_entity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_CAMERA_ENTITY]
            await self.async_set_unique_id(f"entity:{entity_id}")
            self._abort_if_unique_id_configured()
            try:
                name = await _validate_entity(self.hass, entity_id)
            except CameraError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"BabyCam {name}",
                    data={CONF_SOURCE_TYPE: SOURCE_ENTITY, CONF_CAMERA_ENTITY: entity_id},
                )
        return self.async_show_form(step_id="entity", data_schema=STEP_ENTITY_SCHEMA, errors=errors)

    async def async_step_reolink(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"reolink:{user_input[CONF_HOST]}:{user_input[CONF_CHANNEL]}")
            self._abort_if_unique_id_configured()
            try:
                name = await _validate_reolink(self.hass, user_input)
            except CameraError as err:
                _LOGGER.debug("Reolink validation failed: %s", err)
                errors["base"] = "invalid_auth" if "credentials" in str(err) else "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"BabyCam {name}", data={CONF_SOURCE_TYPE: SOURCE_REOLINK, **user_input}
                )
        return self.async_show_form(
            step_id="reolink",
            data_schema=self.add_suggested_values_to_schema(STEP_REOLINK_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BabyCamOptionsFlow:
        return BabyCamOptionsFlow()


class BabyCamOptionsFlow(OptionsFlow):
    """Tune detection, notifications and playback."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_roi(user_input[CONF_ROI])
            except ValueError:
                errors[CONF_ROI] = "invalid_roi"
            if not user_input[CONF_MODEL_MANIFEST_URL].startswith(("http://", "https://")):
                errors[CONF_MODEL_MANIFEST_URL] = "invalid_url"
            if not errors:
                return self.async_create_entry(data={**self.config_entry.options, **user_input})

        opts = {**self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SNAPSHOT_INTERVAL, default=opts.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL)
                ): vol.All(int, vol.Range(min=MIN_SNAPSHOT_INTERVAL, max=MAX_SNAPSHOT_INTERVAL)),
                vol.Required(
                    CONF_DETECTION_THRESHOLD,
                    default=opts.get(CONF_DETECTION_THRESHOLD, DEFAULT_DETECTION_THRESHOLD),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=0.99)),
                vol.Required(
                    CONF_ABSENCE_GRACE, default=opts.get(CONF_ABSENCE_GRACE, DEFAULT_ABSENCE_GRACE)
                ): vol.All(int, vol.Range(min=1, max=50)),
                vol.Required(CONF_ROI, default=opts.get(CONF_ROI, DEFAULT_ROI)): str,
                vol.Required(
                    CONF_CONNECTION_TIMEOUT,
                    default=opts.get(CONF_CONNECTION_TIMEOUT, DEFAULT_CONNECTION_TIMEOUT),
                ): vol.All(int, vol.Range(min=10, max=3600)),
                vol.Required(CONF_CRY_DETECTION, default=opts.get(CONF_CRY_DETECTION, True)): bool,
                vol.Required(
                    CONF_CRY_THRESHOLD, default=opts.get(CONF_CRY_THRESHOLD, DEFAULT_CRY_THRESHOLD)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=0.99)),
                vol.Required(
                    CONF_CRY_COOLDOWN, default=opts.get(CONF_CRY_COOLDOWN, DEFAULT_CRY_COOLDOWN)
                ): vol.All(int, vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_NOTIFY_SERVICES, default=opts.get(CONF_NOTIFY_SERVICES, [])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._notify_services(), multiple=True, custom_value=True
                    )
                ),
                vol.Optional(
                    CONF_MEDIA_PLAYERS, default=opts.get(CONF_MEDIA_PLAYERS, [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player", multiple=True)
                ),
                vol.Required(
                    CONF_PRESENCE_MODEL, default=opts.get(CONF_PRESENCE_MODEL, DEFAULT_PRESENCE_MODEL)
                ): str,
                vol.Required(CONF_CRY_MODEL, default=opts.get(CONF_CRY_MODEL, DEFAULT_CRY_MODEL)): str,
                vol.Required(
                    CONF_MODEL_DOWNLOAD, default=opts.get(CONF_MODEL_DOWNLOAD, DEFAULT_MODEL_DOWNLOAD)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=MODEL_DOWNLOAD_MODES,
                        translation_key="model_download",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_MODEL_MANIFEST_URL,
                    default=opts.get(CONF_MODEL_MANIFEST_URL, DEFAULT_MODEL_MANIFEST_URL),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _notify_services(self) -> list[str]:
        services = self.hass.services.async_services_for_domain("notify")
        return sorted(f"notify.{name}" for name in services if name != "send_message")
