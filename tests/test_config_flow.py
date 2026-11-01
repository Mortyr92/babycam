"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.babycam.camera_client import CameraError
from custom_components.babycam.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SOURCE_TYPE,
    CONF_USERNAME,
    DOMAIN,
    SOURCE_REOLINK,
)

REOLINK_INPUT = {CONF_HOST: "192.168.1.50", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}


async def test_reolink_flow_success(hass: HomeAssistant, mock_snapshot: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_TYPE: SOURCE_REOLINK}
    )
    assert result["step_id"] == "reolink"

    with patch("custom_components.babycam.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], REOLINK_INPUT)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BabyCam Reolink 192.168.1.50"
    assert result["data"][CONF_SOURCE_TYPE] == SOURCE_REOLINK
    assert result["data"][CONF_HOST] == "192.168.1.50"


async def test_reolink_flow_errors(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_TYPE: SOURCE_REOLINK}
    )

    with patch(
        "custom_components.babycam.camera_client.ReolinkCameraSource.async_get_snapshot",
        AsyncMock(side_effect=CameraError("non-JPEG response (check credentials)")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], REOLINK_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    with patch(
        "custom_components.babycam.camera_client.ReolinkCameraSource.async_get_snapshot",
        AsyncMock(side_effect=CameraError("timed out")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], REOLINK_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}
