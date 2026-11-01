"""Tests for the optional model downloader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.babycam.model_downloader import (
    ModelDownloader,
    ModelDownloadError,
)

MANIFEST_URL = "https://example.invalid/models.json"
PRESENCE = b"presence-model-bytes"
CRY = b"cry-model-bytes"


def _manifest(version: str = "1", presence: bytes = PRESENCE, cry: bytes = CRY) -> dict:
    return {
        "version": version,
        "models": {
            "presence": {
                "file": "yolov8n.onnx",
                "url": "https://example.invalid/yolov8n.onnx",
                "sha256": hashlib.sha256(presence).hexdigest(),
            },
            "cry": {
                "file": "yamnet.onnx",
                "url": "https://example.invalid/yamnet.onnx",
                "sha256": hashlib.sha256(cry).hexdigest(),
            },
        },
    }


def _mock_all(mocker: AiohttpClientMocker, manifest: dict, presence: bytes = PRESENCE, cry: bytes = CRY) -> None:
    mocker.clear_requests()
    mocker.get(MANIFEST_URL, json=manifest)
    mocker.get("https://example.invalid/yolov8n.onnx", content=presence)
    mocker.get("https://example.invalid/yamnet.onnx", content=cry)


async def test_download_installs_and_verifies(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    _mock_all(aioclient_mock, _manifest("1"))
    downloader = ModelDownloader(hass, tmp_path, MANIFEST_URL)

    result = await downloader.async_update()
    assert result.version == "1"
    assert sorted(result.updated) == ["cry", "presence"]
    assert (tmp_path / "yolov8n.onnx").read_bytes() == PRESENCE
    assert (tmp_path / "yamnet.onnx").read_bytes() == CRY
    assert downloader.installed_version() == "1"
    assert not list(tmp_path.glob("*.part"))

    # Same version, files present -> nothing downloaded again.
    aioclient_mock.mock_calls.clear()
    result = await downloader.async_update()
    assert result.updated == []
    assert len(aioclient_mock.mock_calls) == 1  # manifest only

    # New version -> both files refreshed.
    _mock_all(aioclient_mock, _manifest("2", presence=b"new-presence"), presence=b"new-presence")
    manifest, needs = await downloader.async_check_update()
    assert manifest.version == "2" and needs is True
    result = await downloader.async_update()
    assert sorted(result.updated) == ["cry", "presence"]
    assert (tmp_path / "yolov8n.onnx").read_bytes() == b"new-presence"
    assert json.loads((tmp_path / "installed.json").read_text())["version"] == "2"


async def test_checksum_mismatch_discards_file(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    _mock_all(aioclient_mock, _manifest("1"), presence=b"tampered")
    downloader = ModelDownloader(hass, tmp_path, MANIFEST_URL)

    with pytest.raises(ModelDownloadError, match="checksum"):
        await downloader.async_update()
    assert not (tmp_path / "yolov8n.onnx").exists()
    assert not list(tmp_path.glob("*.part"))
    assert downloader.installed_version() is None


async def test_manifest_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path) -> None:
    downloader = ModelDownloader(hass, tmp_path, MANIFEST_URL)

    aioclient_mock.get(MANIFEST_URL, status=404)
    with pytest.raises(ModelDownloadError, match="HTTP 404"):
        await downloader.async_fetch_manifest()

    aioclient_mock.clear_requests()
    aioclient_mock.get(MANIFEST_URL, json={"version": "1"})
    with pytest.raises(ModelDownloadError, match="Malformed"):
        await downloader.async_fetch_manifest()

    aioclient_mock.clear_requests()
    aioclient_mock.get(MANIFEST_URL, exc=aiohttp.ClientConnectionError("boom"))
    with pytest.raises(ModelDownloadError):
        await downloader.async_fetch_manifest()
