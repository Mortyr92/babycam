"""Websocket commands used by the BabyCam sidebar panel."""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import voluptuous as vol

from .const import DOMAIN, MONITOR_MANUAL, OPT_STREAM_MODE, SIGNAL_UPDATE, STREAM_MODES
from .coordinator import BabyCamCoordinator
from .model_downloader import ModelDownloadError


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_start)
    websocket_api.async_register_command(hass, ws_stop)
    websocket_api.async_register_command(hass, ws_check_now)
    websocket_api.async_register_command(hass, ws_set_stream_mode)
    websocket_api.async_register_command(hass, ws_download_models)


def _coordinator(hass: HomeAssistant, entry_id: str) -> BabyCamCoordinator | None:
    return hass.data.get(DOMAIN, {}).get(entry_id)


def _entity_ids(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    registry = er.async_get(hass)
    result: dict[str, str] = {}
    for entry in er.async_entries_for_config_entry(registry, entry_id):
        if entry.unique_id and entry.unique_id.startswith(f"{entry_id}_"):
            result[entry.unique_id[len(entry_id) + 1 :]] = entry.entity_id
    return result


def _payload(hass: HomeAssistant, coordinator: BabyCamCoordinator) -> dict[str, Any]:
    return {
        "entry_id": coordinator.entry.entry_id,
        "title": coordinator.entry.title,
        "camera_name": coordinator.source.name,
        "audio_url": coordinator.audio_url,
        "roi": list(coordinator.roi),
        "entities": _entity_ids(hass, coordinator.entry.entry_id),
        "state": coordinator.state.as_dict(),
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list"})
@callback
def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinators: dict[str, BabyCamCoordinator] = hass.data.get(DOMAIN, {})
    connection.send_result(msg["id"], [_payload(hass, c) for c in coordinators.values()])


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/subscribe", vol.Required("entry_id"): str}
)
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown entry")
        return

    @callback
    def _forward() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], _payload(hass, coordinator)))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, SIGNAL_UPDATE.format(msg["entry_id"]), _forward
    )
    connection.send_result(msg["id"])
    _forward()


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/start_monitoring", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_start(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown entry")
        return
    await coordinator.async_start_monitoring(MONITOR_MANUAL)
    connection.send_result(msg["id"], _payload(hass, coordinator))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/stop_monitoring", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_stop(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown entry")
        return
    await coordinator.async_stop_monitoring()
    connection.send_result(msg["id"], _payload(hass, coordinator))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/check_now", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_check_now(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown entry")
        return
    await coordinator.async_check_now()
    connection.send_result(msg["id"], _payload(hass, coordinator))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_stream_mode",
        vol.Required("entry_id"): str,
        vol.Required("mode"): vol.In(STREAM_MODES),
    }
)
@callback
def ws_set_stream_mode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown entry")
        return
    coordinator.async_update_option(OPT_STREAM_MODE, msg["mode"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/download_models", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_download_models(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown entry")
        return
    try:
        await coordinator.async_download_models()
    except ModelDownloadError as err:
        connection.send_error(msg["id"], "download_failed", str(err))
        return
    connection.send_result(msg["id"], _payload(hass, coordinator))
