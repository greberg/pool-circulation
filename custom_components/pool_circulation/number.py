"""Number platform for Pool Circulation – editable daily hours target."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS, DOMAIN
from .coordinator import PoolCirculationCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolDailyHoursNumber(coordinator, entry)])


class PoolDailyHoursNumber(CoordinatorEntity, NumberEntity):
    """Editable target for daily circulation hours."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Circulation Daily Hours"
        self._attr_unique_id = f"{entry.entry_id}_daily_hours"
        self._attr_icon = "mdi:clock"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 24
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "h"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Pool Circulation",
            "model": "Price-based Scheduler",
        }

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_DAILY_HOURS: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()
