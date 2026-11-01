"""Binary sensors: baby in bed, crying, connection-lost alarm, monitoring active."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .const import MONITOR_OFF
from .entity import BabyCamEntity

DESCRIPTIONS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="baby_in_bed", translation_key="baby_in_bed", device_class=BinarySensorDeviceClass.OCCUPANCY
    ),
    BinarySensorEntityDescription(
        key="crying", translation_key="crying", device_class=BinarySensorDeviceClass.SOUND
    ),
    BinarySensorEntityDescription(
        key="connection_alarm", translation_key="connection_alarm", device_class=BinarySensorDeviceClass.PROBLEM
    ),
    BinarySensorEntityDescription(
        key="camera_online", translation_key="camera_online", device_class=BinarySensorDeviceClass.CONNECTIVITY
    ),
    BinarySensorEntityDescription(
        key="monitoring", translation_key="monitoring", device_class=BinarySensorDeviceClass.RUNNING
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(BabyCamBinarySensor(entry.runtime_data, d) for d in DESCRIPTIONS)


class BabyCamBinarySensor(BabyCamEntity, BinarySensorEntity):
    def __init__(self, coordinator, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        s = self.state_data
        match self._key:
            case "baby_in_bed":
                return s.baby_present
            case "crying":
                return s.crying
            case "connection_alarm":
                return s.connection_lost
            case "camera_online":
                return s.camera_online
            case "monitoring":
                return s.monitoring != MONITOR_OFF
        return False

    @property
    def extra_state_attributes(self) -> dict:
        s = self.state_data
        match self._key:
            case "baby_in_bed":
                return {
                    "confidence": round(s.presence_confidence, 3),
                    "last_detection": s.last_detection,
                    "detections": s.detections,
                }
            case "crying":
                return {"score": round(s.cry_score, 3), "last_cry": s.last_cry}
            case "camera_online":
                return {"last_snapshot": s.last_snapshot, "last_error": s.last_error}
            case "monitoring":
                return {"mode": s.monitoring}
        return {}
