"""Base entity for BabyCam."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BabyCamCoordinator


class BabyCamEntity(CoordinatorEntity[BabyCamCoordinator]):
    """Common device info and unique id handling."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BabyCamCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="BabyCam",
            model=coordinator.source.name,
            configuration_url="https://github.com/Mortyr92/babycam",
        )

    @property
    def state_data(self):
        return self.coordinator.state
