"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from aiohttp.resolver import ThreadedResolver
import pytest

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components in tests."""


@pytest.fixture(autouse=True)
def no_dns_resolver_thread() -> Generator[None]:
    """aiodns spawns a daemon thread per TCPConnector, which trips the lingering-thread check."""
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        yield


@pytest.fixture(autouse=True)
def mock_frontend_bits() -> Generator[None]:
    """The panel and static path need the full frontend; stub them out."""
    with (
        patch("custom_components.babycam.panel_custom.async_register_panel", AsyncMock()),
        patch("custom_components.babycam.frontend.async_remove_panel"),
    ):
        yield


@pytest.fixture
def mock_snapshot() -> Generator[AsyncMock]:
    """Make the Reolink source return a fixed JPEG."""
    with patch(
        "custom_components.babycam.camera_client.ReolinkCameraSource.async_get_snapshot",
        AsyncMock(return_value=JPEG),
    ) as mocked:
        yield mocked
