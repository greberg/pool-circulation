"""Switch platform for Pool Circulation."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_OFF
from .coordinator import PoolCirculationCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolCirculationAutomationSwitch(coordinator, entry)])


class PoolCirculationAutomationSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable / disable price-based automation."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Circulation Automation"
        self._attr_unique_id = f"{entry.entry_id}_automation"
        self._attr_icon = "mdi:water-pump"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Pool Circulation",
            "model": "Price-based Scheduler",
        }

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
