"""Integration-level tests: setup, entities and the monitoring state machine."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.babycam.camera_client import CameraError
from custom_components.babycam.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SOURCE_TYPE,
    CONF_USERNAME,
    DOMAIN,
    MONITOR_AUTO,
    MONITOR_MANUAL,
    MONITOR_OFF,
    SERVICE_START_MONITORING,
    SERVICE_STOP_MONITORING,
    SOURCE_REOLINK,
)
from custom_components.babycam.coordinator import BabyCamCoordinator
from custom_components.babycam.detector import PresenceResult
from custom_components.babycam.model_downloader import DownloadResult, ModelDownloadError

from .conftest import JPEG


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="BabyCam Test",
        data={
            CONF_SOURCE_TYPE: SOURCE_REOLINK,
            CONF_HOST: "192.168.1.50",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    await _setup(hass)

    assert hass.states.get("binary_sensor.babycam_test_baby_in_bed").state == "off"
    assert hass.states.get("binary_sensor.babycam_test_crying").state == "off"
    assert hass.states.get("binary_sensor.babycam_test_connection_alarm").state == "off"
    assert hass.states.get("binary_sensor.babycam_test_camera_online").state == "on"
    assert hass.states.get("switch.babycam_test_monitor").state == "off"
    assert hass.states.get("select.babycam_test_view_mode").state == "low"
    assert hass.states.get("sensor.babycam_test_monitoring_mode").state == MONITOR_OFF
    assert float(hass.states.get("number.babycam_test_snapshot_interval").state) == 30
    assert hass.states.get("camera.babycam_test_live") is not None
    assert hass.services.has_service(DOMAIN, SERVICE_START_MONITORING)


async def test_manual_monitoring_via_switch_and_service(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass)
    coordinator: BabyCamCoordinator = entry.runtime_data

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.babycam_test_monitor"}, blocking=True
    )
    assert coordinator.state.monitoring == MONITOR_MANUAL
    assert hass.states.get("binary_sensor.babycam_test_monitoring").state == "on"

    await hass.services.async_call(DOMAIN, SERVICE_STOP_MONITORING, {}, blocking=True)
    assert coordinator.state.monitoring == MONITOR_OFF
    assert hass.states.get("sensor.babycam_test_monitoring_mode").state == MONITOR_OFF


async def test_ai_detection_starts_and_stops_monitoring(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass, {"absence_grace": 2})
    coordinator: BabyCamCoordinator = entry.runtime_data

    present = PresenceResult(present=True, confidence=0.9, detections=[])
    absent = PresenceResult(present=False, confidence=0.0, detections=[])

    with (
        patch.object(coordinator._presence, "detect", return_value=present),
        patch("pathlib.Path.is_file", return_value=True),
    ):
        await coordinator.async_check_now()
    assert hass.states.get("binary_sensor.babycam_test_baby_in_bed").state == "on"
    assert coordinator.state.monitoring == MONITOR_AUTO

    with (
        patch.object(coordinator._presence, "detect", return_value=absent),
        patch("pathlib.Path.is_file", return_value=True),
    ):
        await coordinator.async_check_now()
        assert coordinator.state.baby_present is True  # grace period
        await coordinator.async_check_now()
    assert coordinator.state.baby_present is False
    assert coordinator.state.monitoring == MONITOR_OFF


async def test_connection_lost_alarm_only_while_monitoring(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass, {"connection_timeout": 10, "notify_services": ["notify.phone"]})
    coordinator: BabyCamCoordinator = entry.runtime_data
    notify_calls = []
    hass.services.async_register("notify", "phone", lambda call: notify_calls.append(call.data))

    mock_snapshot.side_effect = CameraError("boom")
    await coordinator.async_check_now()
    assert hass.states.get("binary_sensor.babycam_test_camera_online").state == "off"
    assert coordinator.state.connection_lost is False  # not monitoring -> no alarm

    await coordinator.async_start_monitoring(MONITOR_MANUAL)
    with patch(
        "custom_components.babycam.coordinator.dt_util.utcnow",
        return_value=coordinator._last_ok.__class__.fromtimestamp(4102444800, tz=coordinator._last_ok.tzinfo),
    ):
        await coordinator.async_check_now()
    await hass.async_block_till_done()
    assert coordinator.state.connection_lost is True
    assert hass.states.get("binary_sensor.babycam_test_connection_alarm").state == "on"
    assert notify_calls and "connection lost" in notify_calls[0]["title"]

    mock_snapshot.side_effect = None
    await coordinator.async_check_now()
    assert coordinator.state.connection_lost is False


async def test_cry_score_sets_binary_sensor_and_event(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass, {"cry_threshold": 0.5})
    coordinator: BabyCamCoordinator = entry.runtime_data
    events = []
    hass.bus.async_listen(f"{DOMAIN}_cry_detected", lambda e: events.append(e))

    await coordinator._async_on_cry_score(0.2)
    assert hass.states.get("binary_sensor.babycam_test_crying").state == "off"

    await coordinator._async_on_cry_score(0.8)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.babycam_test_crying").state == "on"
    assert len(events) == 1
    assert hass.states.get("sensor.babycam_test_cry_score").state == "80.0"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert coordinator.state.crying is False


async def test_options_apply_without_reload(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass)
    coordinator: BabyCamCoordinator = entry.runtime_data

    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.babycam_test_snapshot_interval", "value": 12}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.update_interval.total_seconds() == 12
    assert float(hass.states.get("number.babycam_test_snapshot_interval").state) == 12

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.babycam_test_view_mode", "option": "high"}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.state.stream_mode == "high"
    assert await coordinator.async_stream_source() == "rtsp://admin:secret@192.168.1.50:554/h264Preview_01_main"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert mock_snapshot.return_value == JPEG


async def test_model_download_off_never_downloads(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass)
    coordinator: BabyCamCoordinator = entry.runtime_data
    with patch.object(coordinator, "async_download_models", AsyncMock()) as dl:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=2))
        await hass.async_block_till_done()
    dl.assert_not_called()
    assert hass.states.get("sensor.babycam_test_models_version").state == "manual"
    assert hass.states.get("button.babycam_test_download_update_models") is not None


async def test_model_download_once(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    entry = await _setup(hass, {"model_download": "once"})
    coordinator: BabyCamCoordinator = entry.runtime_data
    result = DownloadResult("1", ["presence", "cry"], {"presence": "yolov8n.onnx", "cry": "yamnet.onnx"})

    with patch(
        "custom_components.babycam.coordinator.ModelDownloader.async_update", AsyncMock(return_value=result)
    ) as upd:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
        await hass.async_block_till_done()
        assert upd.await_count == 1
        assert coordinator.state.models_version == "1"
        assert hass.states.get("sensor.babycam_test_models_version").state == "1"

        # Models are now "ready" -> no further checks, not even a day later.
        coordinator.state.presence_model_ready = True
        coordinator.state.cry_model_ready = True
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=2))
        await hass.async_block_till_done()
        assert upd.await_count == 1


async def test_model_download_auto_checks_daily_and_reloads(
    hass: HomeAssistant, mock_snapshot: AsyncMock
) -> None:
    entry = await _setup(hass, {"model_download": "auto", "presence_model": "old.onnx"})
    coordinator: BabyCamCoordinator = entry.runtime_data
    result = DownloadResult("5", ["presence"], {"presence": "yolov8n.onnx", "cry": "yamnet.onnx"})
    events = []
    hass.bus.async_listen(f"{DOMAIN}_models_updated", lambda e: events.append(e))

    with patch(
        "custom_components.babycam.coordinator.ModelDownloader.async_update", AsyncMock(return_value=result)
    ) as upd:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
        await hass.async_block_till_done()
        assert upd.await_count == 1
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=25))
        await hass.async_block_till_done()
        assert upd.await_count == 2

    assert events and events[0].data["version"] == "5"
    # Detector now points at the downloaded file and the option was persisted.
    assert coordinator._presence.model_path.name == "yolov8n.onnx"
    assert entry.options["presence_model"] == "yolov8n.onnx"


async def test_download_service_reports_errors(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    await _setup(hass)
    with (
        patch(
            "custom_components.babycam.coordinator.ModelDownloader.async_update",
            AsyncMock(side_effect=ModelDownloadError("nope")),
        ),
        pytest.raises(HomeAssistantError, match="nope"),
    ):
        await hass.services.async_call(DOMAIN, "download_models", {}, blocking=True)
