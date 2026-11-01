"""Number entities: snapshot interval and detection thresholds."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BabyCamConfigEntry
from .const import (
    CONF_CRY_THRESHOLD,
    CONF_DETECTION_THRESHOLD,
    CONF_SNAPSHOT_INTERVAL,
    DEFAULT_CRY_THRESHOLD,
    DEFAULT_DETECTION_THRESHOLD,
    DEFAULT_SNAPSHOT_INTERVAL,
    MAX_SNAPSHOT_INTERVAL,
    MIN_SNAPSHOT_INTERVAL,
)
from .entity import BabyCamEntity

DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key=CONF_SNAPSHOT_INTERVAL,
        translation_key="snapshot_interval",
        icon="mdi:timer-outline",
        native_min_value=MIN_SNAPSHOT_INTERVAL,
        native_max_value=MAX_SNAPSHOT_INTERVAL,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key=CONF_DETECTION_THRESHOLD,
        translation_key="detection_threshold",
        icon="mdi:target",
        native_min_value=0.1,
        native_max_value=0.95,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key=CONF_CRY_THRESHOLD,
        translation_key="cry_threshold",
        icon="mdi:ear-hearing",
        native_min_value=0.1,
        native_max_value=0.95,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)

DEFAULTS = {
    CONF_SNAPSHOT_INTERVAL: DEFAULT_SNAPSHOT_INTERVAL,
    CONF_DETECTION_THRESHOLD: DEFAULT_DETECTION_THRESHOLD,
    CONF_CRY_THRESHOLD: DEFAULT_CRY_THRESHOLD,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyCamConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(BabyCamNumber(entry.runtime_data, d) for d in DESCRIPTIONS)


class BabyCamNumber(BabyCamEntity, NumberEntity):
    def __init__(self, coordinator, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        return float(self.coordinator.opt(self._key, DEFAULTS[self._key]))

    async def async_set_native_value(self, value: float) -> None:
        if self._key == CONF_SNAPSHOT_INTERVAL:
            value = int(value)
        self.coordinator.async_update_option(self._key, value)
