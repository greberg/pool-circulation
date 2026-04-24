"""Number platform for Pool Circulation — editable runtime targets."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DAILY_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DOMAIN,
)
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
            PoolDailyHoursNumber(coordinator, entry),
            PoolExtraFilterDurationNumber(coordinator, entry),
        ]
    )


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
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_DAILY_HOURS: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()


class PoolExtraFilterDurationNumber(CoordinatorEntity, NumberEntity):
    """How long extra filter mode runs before auto-disabling (minutes)."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Extra Filter Duration"
        self._attr_unique_id = f"{entry.entry_id}_extra_filter_duration"
        self._attr_icon = "mdi:timer"
        self._attr_native_min_value = 5
        self._attr_native_max_value = 480
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "min"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_EXTRA_FILTER_DURATION, DEFAULT_EXTRA_FILTER_DURATION)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_EXTRA_FILTER_DURATION: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()
