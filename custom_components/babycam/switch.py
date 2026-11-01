"""Switches: manual monitoring, AI detection, cry detection, notifications."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .const import (
    CONF_CRY_DETECTION,
    MONITOR_MANUAL,
    MONITOR_OFF,
    OPT_AI_ENABLED,
    OPT_NOTIFICATIONS_ENABLED,
)
from .entity import BabyCamEntity

DESCRIPTIONS: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(key="monitor", translation_key="monitor", icon="mdi:baby-face-outline"),
    SwitchEntityDescription(key="ai_detection", translation_key="ai_detection", icon="mdi:brain"),
    SwitchEntityDescription(key="cry_detection", translation_key="cry_detection", icon="mdi:ear-hearing"),
    SwitchEntityDescription(key="notifications", translation_key="notifications", icon="mdi:bell-ring"),
)

OPTION_KEYS = {
    "ai_detection": OPT_AI_ENABLED,
    "cry_detection": CONF_CRY_DETECTION,
    "notifications": OPT_NOTIFICATIONS_ENABLED,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(BabyCamSwitch(entry.runtime_data, d) for d in DESCRIPTIONS)


class BabyCamSwitch(BabyCamEntity, SwitchEntity):
    def __init__(self, coordinator, description: SwitchEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        s = self.state_data
        match self._key:
            case "monitor":
                return s.monitoring != MONITOR_OFF
            case "ai_detection":
                return s.ai_enabled
            case "cry_detection":
                return s.cry_detection_enabled
            case "notifications":
                return s.notifications_enabled
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._key == "monitor":
            await self.coordinator.async_start_monitoring(MONITOR_MANUAL)
        else:
            self.coordinator.async_update_option(OPTION_KEYS[self._key], True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._key == "monitor":
            await self.coordinator.async_stop_monitoring()
        else:
            self.coordinator.async_update_option(OPTION_KEYS[self._key], False)
