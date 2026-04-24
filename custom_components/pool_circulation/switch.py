"""Switch platform for Pool Circulation."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PoolCirculationCoordinator

_DEVICE_INFO = lambda entry: {
    "identifiers": {(DOMAIN, entry.entry_id)},
    "name": entry.title,
    "manufacturer": "Pool Circulation",
    "model": "Price-based Scheduler",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PoolCirculationAutomationSwitch(coordinator, entry),
            PoolExtraFilterSwitch(coordinator, entry),
        ]
    )


class PoolCirculationAutomationSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable / disable price-based automation."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Circulation Automation"
        self._attr_unique_id = f"{entry.entry_id}_automation"
        self._attr_icon = "mdi:water-pump"
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.automation_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.automation_enabled = True
        self.hass.async_create_task(self.coordinator._save_state())
        await self.coordinator.async_evaluate_mode()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.automation_enabled = False
        self.hass.async_create_task(self.coordinator._save_state())
        self.async_write_ha_state()


class PoolExtraFilterSwitch(CoordinatorEntity, SwitchEntity):
    """Activate extra filter mode — forces high RPM for a configurable duration.

    Useful after bathing to run extra filtration. Overrides price logic but not
    freeze protection. Auto-disables after the configured duration (default 60 min).
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Extra Filter"
        self._attr_unique_id = f"{entry.entry_id}_extra_filter"
        self._attr_icon = "mdi:filter-plus"
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.extra_filter_active

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "duration_minutes": self.coordinator.extra_filter_duration,
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_extra_filter(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_extra_filter(False)
        self.async_write_ha_state()
