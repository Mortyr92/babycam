"""Sensors: monitoring mode, presence confidence, cry score, last snapshot."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .const import MONITOR_AUTO, MONITOR_MANUAL, MONITOR_OFF
from .entity import BabyCamEntity

DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="monitoring_mode",
        translation_key="monitoring_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[MONITOR_OFF, MONITOR_AUTO, MONITOR_MANUAL],
    ),
    SensorEntityDescription(
        key="presence_confidence",
        translation_key="presence_confidence",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="cry_score",
        translation_key="cry_score",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="last_snapshot", translation_key="last_snapshot", device_class=SensorDeviceClass.TIMESTAMP
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(BabyCamSensor(entry.runtime_data, d) for d in DESCRIPTIONS)


class BabyCamSensor(BabyCamEntity, SensorEntity):
    def __init__(self, coordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | float | datetime | None:
        s = self.state_data
        match self._key:
            case "monitoring_mode":
                return s.monitoring
            case "presence_confidence":
                return round(s.presence_confidence * 100, 1)
            case "cry_score":
                return round(s.cry_score * 100, 1)
            case "last_snapshot":
                return s.last_snapshot
        return None
