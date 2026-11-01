"""Select: live view mode (snapshots / low / high definition)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .const import OPT_STREAM_MODE, STREAM_MODES
from .entity import BabyCamEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([BabyCamStreamModeSelect(entry.runtime_data)])


class BabyCamStreamModeSelect(BabyCamEntity, SelectEntity):
    _attr_translation_key = "stream_mode"
    _attr_icon = "mdi:video-switch"
    _attr_options = STREAM_MODES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "stream_mode")

    @property
    def current_option(self) -> str:
        return self.state_data.stream_mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.async_update_option(OPT_STREAM_MODE, option)
