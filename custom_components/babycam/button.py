"""Buttons: download/update models, check now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .entity import BabyCamEntity
from .model_downloader import ModelDownloadError

DESCRIPTIONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(key="check_now", translation_key="check_now", icon="mdi:camera-iris"),
    ButtonEntityDescription(
        key="download_models",
        translation_key="download_models",
        icon="mdi:cloud-download-outline",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(BabyCamButton(entry.runtime_data, d) for d in DESCRIPTIONS)


class BabyCamButton(BabyCamEntity, ButtonEntity):
    def __init__(self, coordinator, description: ButtonEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        if self._key == "check_now":
            await self.coordinator.async_check_now()
            return
        try:
            await self.coordinator.async_download_models()
        except ModelDownloadError as err:
            raise HomeAssistantError(f"Model download failed: {err}") from err
